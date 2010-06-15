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
            self.log_extra('%s is paid in full, no authorization attempted.', purchase)
            results = ProcessorResult(self.key, True, _("No charge needed, paid in full."))
        else:
            data={}
            try:
                pending = cim_purchase.purchase.get_pending(self.key)
                amount = pending.amount
                data['pending'] = pending
            except PaymentPending.DoesNotExist:
                amount = cim_purchase.purchase.total
            self.log_extra('Authorizing payment of %s for %s', amount, cim_purchase)

            results = self.send_post(data, self.TRANS_AUTH, testing, cim_purchase=cim_purchase)

        return results

    def can_authorize(self):
        return True

    def can_recur_bill(self):
        return False 

    def get_prior_approval_code(self, authorization):
        pass

    def capture_authorized_payment(self, authorization, testing=False, cim_purchase=None):
        """Capture a single payment"""
        assert(cim_purchase)
        if cim_purchase.purchase.authorized_remaining == Decimal('0.00'):
            self.log_extra('No remaining authorizations on %s', cim_purchase)
            return ProcessorResult(self.key, True, _("Already complete"))

        amount = authorization.amount
        self.log_extra('Capturing Authorization #%i of %s', authorization.id, amount)
        trans_id = self.get_prior_trans_id(authorization)
        results = None
        if trans_id:
            data = {'transaction_id' : trans_id, 'authorization' : authorization}
            results = self.send_post(data, self.TRANS_CAPTURE, testing, cim_purchase=cim_purchase)
        
        return results
        
    def capture_payment(self, testing=False, cim_purchase=None):
        """Process payments without an authorization step."""
        assert(cim_purchase)

        if cim_purchase.purchase.remaining == Decimal('0.00'):
            self.log_extra('%s is paid in full, no capture attempted.', cim_purchase)
            results = ProcessorResult(self.key, True, _("No charge needed, paid in full."))
            self.record_payment(cim_purchase=cim_purchase)
        else:
            self.log_extra('Capturing payment for %s', cim_purchase)
            
            data = {}
            results = self.send_post(data, self.TRANS_AUTHCAPTURE, testing, cim_purchase=cim_purchase)
            
        return results


    def create_pending_payment(self, cim_purchase=None, amount=NOTSET):
        return super(PaymentProcessor, self).create_pending_payment(purchase=cim_purchase.purchase, amount=amount)
        
    def release_authorized_payment(self, cim_purchase=None, auth=None, testing=False):
        """Release a previously authorized payment."""
        assert(cim_purchase)
        self.log_extra('Releasing Authorization #%i for %s', auth.id, cim_purchase.purchase)
        trans_id = self.get_prior_trans_id(auth)
        results = None
        if trans_id:
            data = {'transaction_id' : trans_id}
            results = self.send_post(data, self.TRANS_VOID, testing, cim_purchase=cim_purchase)
            
        if results.success:
            auth.complete = True
            auth.save()
            
        return results

    def refund_payment(self, cim_purchase=None, auth=None):
        """ Perform a refund """
        assert(cim_purchase)
        self.log_extra('Performing refund on #%i for %s', auth.id, cim_purchase.purchase)
        trans_id = self.get_prior_trans_id(auth)
        results = None
        if trans_id:
            data = {'transaction_id' : trans_id}
            results = self.send_post(data, self.TRANS_REFUND, testing, cim_purchase=cim_purchase)
            
        return results

    def create_customer_profile(self, purchase, testing=False):
        t = get_template('bursar/create_customer_profile_request.xml')
        data = {'purchase' : purchase}
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
            self.log.error(e, xml_request, xml_response.toxml())
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
            self.log.error(e, xml_request, xml_response.toxml())
            return ProcessorResult(self.key, False, e)

    def create_payment_profile(self, cim_purchase, credit_card, credit_card_number, testing=False):
        data = {'action' : 'create', 'cc' : credit_card, 'credit_card_number' : credit_card_number, 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        return self.send_payment_profile(data, testing)

    def update_payment_profile(self, cim_purchase, testing=False):
        data = {'action' : 'update', 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        return self.send_payment_profile(data, testing)

    def send_payment_profile(self, data, testing=False):
        t = get_template('bursar/customer_payment_profile_request.xml')
        data.update(self.get_api_data())
        xml_request = t.render(Context(data))
        try:
            xml_response = self.cim_post(url=data['connection'], xml_request=xml_request)
            message = self.parse_cim_response(xml_response, 'customerPaymentProfileId')
            return ProcessorResult(self.key, True, message)
        except urllib2.URLError, ue:
            self.log.error("error opening %s\n%s", data['connection'], ue)
            return ProcessorResult(self.key, False, _('Could not talk to Authorize.net gateway'))
        except AuthNetException as e:
            self.log.error(e, xml_request, xml_response.toxml())
            return ProcessorResult(self.key, False, e)

    def update_shipping_address(self, cim_purchase, testing=False):
        data = {'action' : 'update', 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        return self.send_shipping_address(data, testing)

    def create_shipping_address(self, cim_purchase, testing=False):
        data = {'action' : 'create', 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        return self.send_shipping_address(data, testing)

    def send_shipping_address(self, data, testing=False):
        t = get_template('bursar/customer_shipping_address_request.xml')
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
            self.log.error(e, xml_request, xml_response.toxml())
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
        headers = { 'Content-Type' : 'text/xml' }
        conn = urllib2.Request(url=url, data=xml_request, headers=headers)
        f = urllib2.urlopen(conn)
        all_results = f.read()
        self.log_extra('Authorize response: %s', all_results)
        return parseString(all_results)

    def send_post(self, data, action, testing=False, cim_purchase=None):
        """Execute the post to Authorize Net.
        
        Params:
        - data: dictionary which may contain 'transaction_id', 'extra_options', 'authorization', or 'pending'
        - testing: if true, then don't record the payment
        
        Returns:
        - ProcessorResult
        """
        assert(cim_purchase)
        self.log.info("About to send a request to authorize.net: %(connection)s\n%(logPostString)s", data)

        data.update(self.get_api_data())
        object_data = { 'action' : self.TRANS_XML[action], 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        data.update(object_data)

        t = get_template('bursar/create_customer_profile_transaction_request.xml')
        xml_request = t.render(Context(data))
        try:
            xml_response = self.cim_post(url=data['connection'], xml_request=xml_request)
            data_response = self.parse_cim_response(xml_response, 'directResponse')
        except urllib2.URLError, ue:
            self.log.error("error opening %s\n%s", data['connection'], ue)
            return ProcessorResult(self.key, False, _('Could not talk to Authorize.net gateway'))
        except AuthNetException as e:
            self.log.error("reponse error %s\n%s", xml_request, xml_response.toxml(), e)
            return ProcessorResult(self.key, False, _('Response contained error code %s' % e))

        parsed_results = data_response.split(self.settings['DELIM_CHAR'])
        response_code = parsed_results[0]
        reason_code = parsed_results[1]
        response_text = parsed_results[3]
        transaction_id = parsed_results[6]
            
        success = response_code == '1'
        amount = cim_purchase.purchase.total

        payment = None
        if success and not testing:
            if action == self.TRANS_AUTH:
                self.log_extra('Success, recording authorization')
                payment = self.record_authorization(cim_purchase=cim_purchase, amount=amount, 
                    transaction_id=transaction_id, reason_code=reason_code)
            else:
                self.log_extra('Success, recording payment')
                authorization = data.get('authorization', None)
                payment = self.record_payment(cim_purchase=cim_purchase, amount=amount, 
                    transaction_id=transaction_id, reason_code=reason_code, authorization=authorization)
            
        elif not testing:
            payment = self.record_failure(amount=amount, transaction_id=transaction_id, 
                reason_code=reason_code, details=response_text, cim_purchase=cim_purchase)

        self.log_extra("Returning success=%s, reason=%s, response_text=%s", success, reason_code, response_text)
        return ProcessorResult(self.key, success, response_text, payment=payment)
