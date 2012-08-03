from bursar.errors import GatewayError
from bursar.gateway.base import BasePaymentProcessor, ProcessorResult, NOTSET, PaymentPending
from bursar.gateway.authorizenet_cim_gateway.models import CIMPurchase
from bursar.numbers import trunc_decimal
from datetime import datetime
from decimal import Decimal
from django.template.loader import get_template
from django.template import Context
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _
from xml.dom.minidom import parse, parseString
import random
import urllib2
from django.template import Context, Template

class AuthNetException(Exception):
    def get_code(self):
        return self.args[0]

    def get_text(self):
        return self.args[1]

    def __str__(self):
        return "AuthNetException, code: %s, message: %s" % self.args


class AuthNetResponse(object):
    SUCCESS = 'I00001'
    GENERIC_ERROR = 'E00001'
    PARSE_ERROR = 'E00003'
    API_DNE_ERROR = 'E00004' #the api call/node name doesn't exist
    TRANS_KEY_ERROR = 'E00005'
    NAME_ERROR = 'E00006'
    AUTH_FAIL = 'E00007'
    INACTIVE_FAIL = 'E00008'
    TEST_MODE = 'E00009'
    PERM_FAIL = 'E00010'
    ACCESS_FAIL = 'E00011'
    FIELD_ERROR = 'E00013'
    REQD_ERROR = 'E00014'
    LEN_ERROR = 'E00015'
    TYPE_ERROR = 'E00016'
    TRANS_ERROR = 'E00027'
    PAY_REQD_ERROR = 'E00029'
    DUPLICATE_ERROR = 'E00039'
    MATCH_ERROR = 'E00051' #If the customer profile ID, payment profile ID, and shipping address ID are included, they must match the original transaction. 

    @staticmethod
    def is_error(code):
        return True if code[0] == 'E' else False

    @staticmethod
    def is_success(code):
        return True if code == AuthNetResponse.SUCCESS else False

    @staticmethod
    def get_text(elm):
        return ''.join([ "%s" % node.data for node in elm.childNodes if node.nodeType == node.TEXT_NODE ])

    @staticmethod
    def get_duplicate(text):
        import re
        regex = re.compile("(\d+)")
        r = regex.search(text)
        return r.groups()[0]

    @staticmethod
    def get_message(elm):
        message = elm.getElementsByTagName('message')[0]
        code = AuthNetResponse.get_text(message.getElementsByTagName('code')[0])
        text = AuthNetResponse.get_text(message.getElementsByTagName('text')[0])
        return (code, text)


class PaymentProcessor(BasePaymentProcessor):
    """
    Authorize.NET payment processing module
    You must have an account with authorize.net in order to use this module.
    
    Additionally, you must have ARB enabled in your account to use recurring billing.
    
    Settings:
        ARB: Enable ARB processing for setting up subscriptions.  Note: You 
            must have this enabled in your Authorize account for it to work.
        ARB_CONNECTION: Submit to URL for ARB transactions. This is the address 
            to submit live transactions for ARB.
        CAPTURE: Capture Payment immediately? IMPORTANT: If false, 
            a capture attempt will be made when the order is marked as shipped.
        CONNECTION: This is the address to submit live transactions.
        CONNECTION_TEST: Submit to Test URL.  A Quick note on the urls.
            If you are posting to https://test.authorize.net/gateway/transact.dll,
            and you are not using an account whose API login ID starts with
            "cpdev" or "cnpdev", you will get an Error 13 message. 
            Make sure you are posting to https://certification.authorize.net/gateway/transact.dll
            for test transactions if you do not have a cpdev or cnpdev.
        CREDITCHOICES: Available credit cards, as (key, name).  To add American Express, 
            use (('American Express', 'American Express'))
        EXTRA_LOGGING: Verbose Logs?
        LABEL: English name for this payment module on the checkout screens.
        LIVE: Accept real payments. NOTE: If you are testing, then you can use the cc# 
            4222222222222 to force a bad credit card response.  If you use that number 
            and a ccv of 222, that will force a bad ccv response from authorize.net
        LOGIN: (REQUIRED) Your authorize.net transaction login
        SIMULATE: Force a test post?
        STORE_NAME: (REQUIRED) The name of your store
        TRANKEY : (REQUIRED) Your authorize.net transaction key
    """
    TRANS_VOID = 1
    TRANS_AUTH = 2
    TRANS_AUTHCAPTURE = 3
    TRANS_CAPTURE = 4
    TRANS_REFUND = 5

    TRANS_XML = {TRANS_VOID : "profileTransVoid",
        TRANS_AUTH : "profileTransAuthOnly",
        TRANS_AUTHCAPTURE : "profileTransAuthCapture",
        TRANS_CAPTURE : "profileTransPriorAuthCapture",
        TRANS_REFUND : "profileTransRefund",}

    def __init__(self, settings={}):
        working_settings = {
            'CONNECTION' : 'https://api.authorize.net/xml/v1/request.api',
            'CONNECTION_TEST' : 'https://apitest.authorize.net/xml/v1/request.api',
            'DELIM_CHAR' : ',',
            }
        working_settings.update(settings)            
        super(PaymentProcessor, self).__init__('authorizenet', working_settings)
        self.require_settings('LOGIN', 'STORE_NAME', 'TRANKEY', 'API_LOGIN_KEY')

    def authorize_payment(self, cim_purchase=None, testing=False):
        """Authorize a single payment.
        
        Returns: ProcessorResult
        """
        assert(cim_purchase)
        if cim_purchase.purchase.remaining == Decimal('0.00'):
            self.log_extra('%s is paid in full, no authorization attempted.', cim_purchase)
            results = ProcessorResult(self.key, True, _("No charge needed, paid in full."))
        else:
            data={}
            try:
                pending = cim_purchase.purchase.get_pending(self.key)
                amount = pending.amount
                data['pending'] = pending
            except PaymentPending.DoesNotExist:
                amount = cim_purchase.purchase.remaining
            self.log_extra('Authorizing payment of %s for %s', amount, cim_purchase)

            results = self.send_post(data, self.TRANS_AUTH, cim_purchase, amount, testing)

        return results

    def can_authorize(self):
        return True

    def can_recur_bill(self):
        return False 

    def capture_authorized_payments(self, cim_purchase=None):
        """Capture all outstanding payments for this processor.  This is usually called by a 
        listener which watches for a 'shipped' status change on the Order."""
        assert(cim_purchase)
        results = []
        if self.can_authorize():
            auths = cim_purchase.purchase.authorizations.filter(method__exact=self.key, complete=False)
            self.log_extra('Capturing %i %s authorizations for purchase on order #%s', auths.count(), self.key, cim_purchase.purchase.orderno)
            for auth in auths:
                results.append(self.capture_authorized_payment(auth, cim_purchase))
                
        return results

    def capture_authorized_payment(self, authorization, cim_purchase=None, testing=False):
        """Capture a single payment"""
        assert(cim_purchase)
        if cim_purchase.purchase.authorized_remaining == Decimal('0.00'):
            self.log_extra('No remaining authorizations on %s', cim_purchase)
            return ProcessorResult(self.key, True, _("Already complete"))

        amount = authorization.amount

        remaining = authorization.remaining
        if amount == NOTSET or amount > remaining:
            if amount != NOTSET:
                self.log_extra('Adjusting auth amount from %s to %s', amount, remaining)
            amount = trunc_decimal(remaining, 2)

        self.log_extra('Capturing Authorization #%i of %s', authorization.id, amount)
        results = None
        if authorization.transaction_id:
            data = {'transaction_id' : authorization.transaction_id, 'authorization' : authorization}
            if hasattr(authorization, 'payment_profile_id') and authorization.payment_profile_id is not None:
                data['payment_profile_id'] = authorization.payment_profile_id
            if hasattr(authorization, 'customer_profile_id') and authorization.customer_profile_id is not None:
                data['customer_profile_id'] = authorization.customer_profile_id
            results = self.send_post(data, self.TRANS_CAPTURE, cim_purchase, amount, testing)
        
        return results
        
    def capture_payment(self, cim_purchase=None, testing=False):
        """Process payments without an authorization step."""
        assert(cim_purchase)

        if cim_purchase.purchase.remaining == Decimal('0.00'):
            self.log_extra('%s is paid in full, no capture attempted.', cim_purchase)
            results = ProcessorResult(self.key, True, _("No charge needed, paid in full."))
            self.record_payment(purchase=cim_purchase.purchase)
        else:
            amount = cim_purchase.purchase.remaining
            self.log_extra('Capturing payment for %s', cim_purchase)
            
            data = {}
            results = self.send_post(data, self.TRANS_AUTHCAPTURE, cim_purchase, amount, testing)
            
        return results

    def create_payment(self, cim_purchase=None, amount=NOTSET):
        return super(PaymentProcessor, self).record_payment(purchase=cim_purchase.purchase, amount=amount)

    def create_pending_payment(self, cim_purchase=None, amount=NOTSET):
        return super(PaymentProcessor, self).create_pending_payment(purchase=cim_purchase.purchase, amount=amount)
        
    def release_authorized_payment(self, cim_purchase=None, auth=None, testing=False):
        """Release a previously authorized payment."""
        assert(cim_purchase)
        self.log_extra('Releasing Authorization #%i for %s', auth.id, cim_purchase.purchase)
        results = None
        if auth.transaction_id:
            data = {'transaction_id' : auth.transaction_id}
            if hasattr(auth, 'payment_profile_id') and auth.payment_profile_id is not None:
                data['payment_profile_id'] = auth.payment_profile_id
            if hasattr(auth, 'customer_profile_id') and auth.customer_profile_id is not None:
                data['customer_profile_id'] = auth.customer_profile_id
            results = self.send_post(data, self.TRANS_VOID, cim_purchase)
            
        if results.success:
            auth.complete = True
            auth.save()
            
        return results

    def refund_payment(self, cim_purchase=None, auth=None, amount=None):
        """ Perform a refund """
        assert(cim_purchase)
        assert(amount)
        self.log_extra('Performing refund on #%i for %s', auth.id, cim_purchase.purchase)
        results = None
        if auth.transaction_id:
            data = {'transaction_id' : auth.transaction_id, 'amount': amount}
            if hasattr(auth, 'payment_profile_id') and auth.payment_profile_id is not None:
                data['payment_profile_id'] = auth.payment_profile_id
            if hasattr(auth, 'customer_profile_id') and auth.customer_profile_id is not None:
                data['customer_profile_id'] = auth.customer_profile_id
            results = self.send_post(data, self.TRANS_REFUND, cim_purchase=cim_purchase, amount=amount)
            
        return results

    def create_customer_profile(self, data, testing=False):
        t = get_template('bursar/create_customer_profile_request.xml')
        data.update(self.get_api_data())
        xml_request = t.render(Context(data))
        try:
            xml_response = self.cim_post(url=data['connection'], xml_request=xml_request)
            message = self.parse_cim_response(xml_response, 'customerProfileId')
            return ProcessorResult(self.key, True, message)
        except urllib2.URLError, ue:
            self.log.error("error opening %s\n%s", data['connection'], ue)
            return ProcessorResult(self.key, False, _('Could not talk to Authorize.net gateway'))
        except AuthNetException as e:
            msg = xml_response.toxml() if hasattr(xml_response, 'toxml') else xml_response
            self.log.error(e, xml_request, msg)
            return ProcessorResult(self.key, False, e)

    def delete_customer_profile(self, data, testing=False):
        t = get_template('bursar/delete_customer_profile_request.xml')
        data.update(self.get_api_data())
        xml_request = t.render(Context(data))
        try:
            xml_response = self.cim_post(url=data['connection'], xml_request=xml_request)
            return ProcessorResult(self.key, True, data['customer_profile_id'])
        except urllib2.URLError, ue:
            self.log.error("error opening %s\n%s", data['connection'], ue)
            return ProcessorResult(self.key, False, _('Could not talk to Authorize.net gateway'))
        except AuthNetException as e:
            msg = xml_response.toxml() if hasattr(xml_response, 'toxml') else xml_response
            self.log.error(e, xml_request, msg)
            return ProcessorResult(self.key, False, e)

    def create_payment_profile(self, cim_purchase, credit_card, credit_card_number, testing=False):
        credit_card.expire_month = "%.2d" % credit_card.expire_month #force two digits
        data = {'action' : 'create', 'cc' : credit_card, 'credit_card_number' : credit_card_number, 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        return self.send_payment_profile(data, testing)

    def update_payment_profile(self, cim_purchase, credit_card, credit_card_number, testing=False):
        credit_card.expire_month = "%.2d" % credit_card.expire_month #force two digits
        data = {'include_id': True, 'action' : 'update', 'cc' : credit_card, 'credit_card_number' : credit_card_number, 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        return self.send_payment_profile(data, testing)

    def send_payment_profile(self, data, testing=False):
        t = get_template('bursar/customer_payment_profile_request.xml')
        data.update(self.get_api_data())
        xml_request = t.render(Context(data))
        try:
            xml_response = self.cim_post(url=data['connection'], xml_request=xml_request)
            if not data.has_key('include_id'):
                message = self.parse_cim_response(xml_response, 'customerPaymentProfileId')
            else: # update does not return the profile id
                code, text = AuthNetResponse.get_message(xml_response)
                if AuthNetResponse.is_success(code):
                    message = data.get('cim_purchase').payment_profile_id
                else:
                    msg = xml_response.toxml() if hasattr(xml_response, 'toxml') else xml_response
                    self.log.debug("payment profile creation failed %s", msg)
                    raise AuthNetException(code, text)
            return ProcessorResult(self.key, True, message)
        except urllib2.URLError, ue:
            self.log.error("error opening %s\n%s", data['connection'], ue)
            return ProcessorResult(self.key, False, _('Could not talk to Authorize.net gateway'))
        except AuthNetException as e:
            msg = xml_response.toxml() if hasattr(xml_response, 'toxml') else xml_response
            self.log.error(e, xml_request, msg)
            return ProcessorResult(self.key, False, e)

    def update_shipping_address(self, cim_purchase, testing=False):
        data = {'action' : 'update', 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        return self.send_shipping_address(data, testing)

    def create_shipping_address(self, cim_purchase, testing=False):
        data = {'action' : 'create', 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        return self.send_shipping_address(data, testing)

    def delete_shipping_address(self, data, testing=False):
        template = "bursar/delete_shipping_address_request.xml"
        t = get_template(template)
        data.update(self.get_api_data())
        xml_request = t.render(Context(data))
        try:
            xml_response = self.cim_post(url=data['connection'], xml_request=xml_request)
            return ProcessorResult(self.key, True, "Address Removed")
        except urllib2.URLError, ue:
            self.log.error("error opening %s\n%s", data['connection'], ue)
            return ProcessorResult(self.key, False, _('Could not talk to Authorize.net gateway'))
        except AuthNetException as e:
            msg = xml_response.toxml() if hasattr(xml_response, 'toxml') else xml_response
            self.log.error(e, xml_request, msg)
            return ProcessorResult(self.key, False, e)

    def send_shipping_address(self, data, testing=False):
        template = 'bursar/customer_shipping_address_request.xml'
        t = get_template(template)
        data.update(self.get_api_data())
        xml_request = t.render(Context(data))
        try:
            xml_response = self.cim_post(url=data['connection'], xml_request=xml_request)
            message = self.parse_cim_response(xml_response, 'customerAddressId')
            return ProcessorResult(self.key, True, message)
        except urllib2.URLError, ue:
            self.log.error("error opening %s\n%s", data['connection'], ue)
            return ProcessorResult(self.key, False, _('Could not talk to Authorize.net gateway'))
        except AuthNetException as e:
            msg = xml_response.toxml() if hasattr(xml_response, 'toxml') else xml_response
            self.log.error(e, xml_request, msg)
            return ProcessorResult(self.key, False, e)

    def parse_cim_response(self, xml_dom, tag):
        code, text = AuthNetResponse.get_message(xml_dom)
        if AuthNetResponse.is_success(code):
            return AuthNetResponse.get_text(xml_dom.getElementsByTagName(tag)[0])
        else:
            raise AuthNetException(code, text)

    def get_api_data(self):
        if self.is_live():
            conn = self.settings["CONNECTION"]
            self.log_extra('Using live connection.')
        else:
            testflag = 'TRUE'
            conn = self.settings["CONNECTION_TEST"]
            self.log_extra('Using test connection.')
        return { 'connection' : conn, 'api_login_id' : self.settings['API_LOGIN_KEY'], 'transaction_key' : self.settings['TRANKEY'] }

    def cim_post(self, url, xml_request):
        from django.utils.encoding import smart_str
        #print xml_request
        headers = { 'Content-Type' : 'text/xml' }
        xml_request = smart_str(xml_request)
        conn = urllib2.Request(url=url, data=xml_request, headers=headers)
        f = urllib2.urlopen(conn)
        all_results = f.read()
        #print all_results
        self.log_extra('Authorize response: %s', all_results)
        return parseString(all_results)

    def send_post(self, data, action, cim_purchase=None, amount=None, testing=False):
        """Execute the post to Authorize Net.
        
        Params:
        - data: dictionary which may contain 'transaction_id', 'extra_options', or amount 
        - testing: if true, then don't record the payment
        
        Returns:
        - ProcessorResult
        """
        assert(cim_purchase)
        self.log.info("About to send a request to authorize.net: %(connection)s\n%(logPostString)s", data)

        if action != self.TRANS_VOID:
            if amount is None:
                amount = cim_purchase.purchase.total
            if not data.has_key('amount'):
                data['amount'] = amount

        data.update(self.get_api_data())
        object_data = { 'action' : self.TRANS_XML[action], 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        data.update(object_data)
        if not data.has_key('payment_profile_id'):
            data['payment_profile_id'] = cim_purchase.payment_profile_id
        if not data.has_key('customer_profile_id'):
            data['customer_profile_id'] = cim_purchase.customer_profile_id

        t = get_template('bursar/create_customer_profile_transaction_request.xml')
        xml_request = t.render(Context(data))
        try:
            xml_response = self.cim_post(url=data['connection'], xml_request=xml_request)
            data_response = self.parse_cim_response(xml_response, 'directResponse')
        except urllib2.URLError, ue:
            self.log.error("error opening %s\n%s", data['connection'], ue)
            return ProcessorResult(self.key, False, _('Could not talk to Authorize.net gateway'))
        except AuthNetException as e:
            msg = xml_response.toxml() if hasattr(xml_response, 'toxml') else xml_response
            self.log.error("reponse error %s\n%s", xml_request, msg, e)
            payment = self.record_failure(amount=amount, reason_code=e.get_code(), details=e.get_text(), purchase=cim_purchase.purchase)
            return ProcessorResult(self.key, False, _('%s: %s' % (e.get_code(), e.get_text())), payment=payment)

        parsed_results = data_response.split(self.settings['DELIM_CHAR'])
        response_code = parsed_results[0]   
        reason_code = parsed_results[1]     
        response_text = parsed_results[3]   
        transaction_id = parsed_results[6]  
            
        success = response_code == '1'

        payment = None
        if success and not testing:
            if action == self.TRANS_AUTH:
                self.log_extra('Success, recording authorization')
                payment = self.record_authorization(purchase=cim_purchase.purchase, amount=amount, 
                    transaction_id=transaction_id, reason_code=reason_code)
                payment.payment_profile_id = cim_purchase.payment_profile_id
                payment.customer_profile_id = cim_purchase.customer_profile_id
                payment.save()
            elif action == self.TRANS_REFUND:
                payment = self.record_refund(purchase=cim_purchase.purchase, amount=amount, 
                    transaction_id=transaction_id, reason_code=reason_code)
            else:
                self.log_extra('Success, recording payment')
                authorization = data.get('authorization', None)
                payment = self.record_payment(purchase=cim_purchase.purchase, amount=amount, 
                    transaction_id=transaction_id, reason_code=reason_code, authorization=authorization)
            
        elif not testing:
            payment = self.record_failure(amount=amount, transaction_id=transaction_id, 
                reason_code=reason_code, details=response_text, purchase=cim_purchase.purchase)

        self.log_extra("Returning success=%s, reason=%s, response_text=%s", success, reason_code, response_text)
        return ProcessorResult(self.key, success, response_text, payment=payment)
