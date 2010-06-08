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

    TRANS_XML = (
        (TRANS_VOID, "profileTransVoid"),
        (TRANS_AUTH, "profileTransAuthOnly"),
        (TRANS_AUTHCAPTURE, "profileTransAuthCapture"),
        (TRANS_CAPTURE, "profileTransPriorAuthCapture"),
    )

    def __init__(self, settings={}):
        working_settings = {
            'CONNECTION' : 'https://api.authorize.net/xml/v1/request.api',
            'CONNECTION_TEST' : 'https://apitest.authorize.net/xml/v1/request.api',
            }
        working_settings.update(settings)            
        super(PaymentProcessor, self).__init__('authorizenet', working_settings)
        self.require_settings('LOGIN', 'STORE_NAME', 'TRANKEY', 'API_LOGIN_KEY')

    def authorize_payment(self, cim_purchase=None, amount=NOTSET, testing=False):
        """Authorize a single payment.
        
        Returns: ProcessorResult
        """
        assert(cim_purchase)
        if cim_purchase.purchase.remaining == Decimal('0.00'):
            self.log_extra('%s is paid in full, no authorization attempted.', purchase)
            results = ProcessorResult(self.key, True, _("No charge needed, paid in full."))
        else:
            if amount == NOTSET:
                try:
                    pending = cim_purchase.purchase.get_pending(self.key)
                    amount = pending.amount
                except PaymentPending.DoesNotExist:
                    amount = cim_purchase.purchase.remaining
            self.log_extra('Authorizing payment of %s for %s', amount, cim_purchase)

            standard = self.get_standard_charge_data(authorize=True, cim_purchase=cim_purchase, amount=amount)
            results = self.send_post(standard, self.TRANS_AUTH, testing, cim_purchase=cim_purchase)

        return results

    def can_authorize(self):
        return True

    def can_recur_bill(self):
        return True

    def capture_authorized_payment(self, authorization, testing=False, cim_purchase=None, amount=NOTSET):
        """Capture a single payment"""
        assert(cim_purchase)
        if cim_purchase.purchase.authorized_remaining == Decimal('0.00'):
            self.log_extra('No remaining authorizations on %s', cim_purchase)
            return ProcessorResult(self.key, True, _("Already complete"))

        self.log_extra('Capturing Authorization #%i of %s', authorization.id, amount)
        if amount==NOTSET:
            amount = authorization.amount
        data = self.get_prior_auth_data(authorization, amount=amount)
        results = None
        if data:
            results = self.send_post(data, self.TRANS_CAPTURE, testing, cim_purchase=cim_purchase)
        
        return results
        
    def capture_payment(self, testing=False, cim_purchase=None, amount=NOTSET):
        """Process payments without an authorization step."""
        assert(cim_purchase)

        if cim_purchase.purchase.remaining == Decimal('0.00'):
            self.log_extra('%s is paid in full, no capture attempted.', cim_purchase)
            results = ProcessorResult(self.key, True, _("No charge needed, paid in full."))
            self.record_payment(cim_purchase=cim_purchase)
        else:
            self.log_extra('Capturing payment for %s', cim_purchase)
            
            standard = self.get_standard_charge_data(amount=amount, cim_purchase=cim_purchase)
            results = self.send_post(standard, self.TRANS_AUTHCAPTURE, testing, cim_purchase=cim_purchase)
            
        return results

    def get_prior_auth_data(self, authorization, amount=NOTSET):
        """Build the dictionary needed to process a prior auth capture."""
        trans = {'authorization' : authorization}
        remaining = authorization.remaining
        if amount == NOTSET or amount > remaining:
            if amount != NOTSET:
                self.log_extra('Adjusting auth amount from %s to %s', amount, remaining)
            amount = remaining
        
        balance = trunc_decimal(amount, 2)
        trans['amount'] = amount
        
        if self.is_live():
            conn = self.settings["CONNECTION"]
            self.log_extra('Using live connection.')
        else:
            testflag = 'TRUE'
            conn = self.settings["CONNECTION_TEST"]
            self.log_extra('Using test connection.')
            
        if self.settings["SIMULATE"]:
            testflag = 'TRUE'
        else:
            testflag = 'FALSE'

        trans['connection'] = conn

        trans['configuration'] = {
            'x_login' : self.settings["LOGIN"],
            'x_tran_key' : self.settings["TRANKEY"],
            'x_version' : '3.1',
            'x_relay_response' : 'FALSE',
            'x_test_request' : testflag,
            'x_delim_data' : 'TRUE',
            'x_delim_char' : '|',
            'x_type': 'PRIOR_AUTH_CAPTURE',
            'x_trans_id' : authorization.transaction_id
            }
        
        self.log_extra('prior auth configuration: %s', trans['configuration'])
                
        trans['transactionData'] = {
            'x_amount' : balance,
            }

        part1 = urlencode(trans['configuration']) 
        postdata = part1 + "&" + urlencode(trans['transactionData'])
        trans['postString'] = postdata
        
        self.log_extra('prior auth poststring: %s', postdata)
        trans['logPostString'] = postdata
        
        return trans
        
        
    def get_void_auth_data(self, authorization):
        """Build the dictionary needed to process a prior auth release."""
        trans = {
            'authorization' : authorization,
            'amount' : Decimal('0.00'),
        }

        if self.is_live():
            conn = self.settings['CONNECTION']
            self.log_extra('Using live connection.')
        else:
            testflag = 'TRUE'
            conn = self.settings["CONNECTION_TEST"]
            self.log_extra('Using test connection.')

        if self.settings['SIMULATE']:
            testflag = 'TRUE'
        else:
            testflag = 'FALSE'

        trans['connection'] = conn

        trans['configuration'] = {
            'x_login' : self.settings["LOGIN"],
            'x_tran_key' : self.settings["TRANKEY"],
            'x_version' : '3.1',
            'x_relay_response' : 'FALSE',
            'x_test_request' : testflag,
            'x_delim_data' : 'TRUE',
            'x_delim_char' : '|',
            'x_type': 'VOID',
            'x_trans_id' : authorization.transaction_id
            }

        self.log_extra('void auth configuration: %s', trans['configuration'])

        postdata = urlencode(trans['configuration']) 
        trans['postString'] = postdata

        self.log_extra('void auth poststring: %s', postdata)
        trans['logPostString'] = postdata

        return trans

        
    def get_standard_charge_data(self, cim_purchase=None, amount=NOTSET, authorize=False):
        """Build the dictionary needed to process a credit card charge"""
        assert(cim_purchase)
        trans = {}
        if amount == NOTSET:
            amount = cim_purchase.purchase.total
            
        balance = trunc_decimal(amount, 2)
        trans['amount'] = balance
        
        if self.is_live():
            conn = self.settings['CONNECTION']
            self.log_extra('Using live connection.')
        else:
            testflag = 'TRUE'
            conn = self.settings['CONNECTION_TEST']
            self.log_extra('Using test connection.')
            
        if self.settings['SIMULATE']:
            testflag = 'TRUE'
        else:
            testflag = 'FALSE'

        trans['connection'] = conn
            
        trans['authorize_only'] = authorize

        if not authorize:
            transaction_type = 'AUTH_CAPTURE'
        else:
            transaction_type = 'AUTH_ONLY'
                        
        trans['configuration'] = {
            'x_login' : self.settings['LOGIN'],
            'x_tran_key' : self.settings['TRANKEY'],
            'x_version' : '3.1',
            'x_relay_response' : 'FALSE',
            'x_test_request' : testflag,
            'x_delim_data' : 'TRUE',
            'x_delim_char' : '|',
            'x_type': transaction_type,
            'x_method': 'CC',
            }
        
        self.log_extra('standard charges configuration: %s', trans['configuration'])
        
        trans['custBillData'] = {
            'x_first_name' : cim_purchase.purchase.first_name,
            'x_last_name' : cim_purchase.purchase.last_name,
            'x_address': cim_purchase.purchase.full_bill_street,
            'x_city': cim_purchase.purchase.bill_city,
            'x_state' : cim_purchase.purchase.bill_state,
            'x_zip' : cim_purchase.purchase.bill_postal_code,
            'x_country': cim_purchase.purchase.bill_country,
            'x_phone' : cim_purchase.purchase.phone,
            'x_email' : cim_purchase.purchase.email,
            }
    
        self.log_extra('standard charges configuration: %s', trans['custBillData'])
        
        invoice = "%s" % cim_purchase.purchase.orderno
        failct = cim_purchase.purchase.paymentfailures.count()
        if failct > 0:
            invoice = "%s_%i" % (invoice, failct)

        if not self.is_live():
            # add random test id to this, for testing repeatability
            invoice = "%s_test_%s_%i" % (invoice,  datetime.now().strftime('%m%d%y'), random.randint(1,1000000))
        
        card = cim_purchase.purchase.credit_card
        cc = card.decryptedCC
        ccv = card.ccv
        if not self.is_live() and cc == '4222222222222':
            if ccv == '222':
                self.log_extra('Setting a bad ccv number to force an error')
                ccv = '1'
            else:
                self.log_extra('Setting a bad credit card number to force an error')
                cc = '1234'
        trans['transactionData'] = {
            'x_amount' : balance,
            'x_card_num' : cc,
            'x_exp_date' : card.expirationDate,
            'x_card_code' : ccv,
            'x_invoice_num' : invoice
            }

        part1 = urlencode(trans['configuration']) + "&"
        part2 = "&" + urlencode(trans['custBillData'])
        trans['postString'] = part1 + urlencode(trans['transactionData']) + part2
        
        redactedData = {
            'x_amount' : balance,
            'x_card_num' : card.display_cc,
            'x_exp_date' : card.expirationDate,
            'x_card_code' : "REDACTED",
            'x_invoice_num' : invoice
        }
        self.log_extra('standard charges transactionData: %s', redactedData)
        trans['logPostString'] = part1 + urlencode(redactedData) + part2
        
        return trans

    def create_pending_payment(self, cim_purchase=None, amount=NOTSET):
        return super(PaymentProcessor, self).create_pending_payment(purchase=cim_purchase.purchase, amount=amount)
        
    def release_authorized_payment(self, cim_purchase=None, auth=None, testing=False):
        """Release a previously authorized payment."""
        assert(cim_purchase)
        self.log_extra('Releasing Authorization #%i for %s', auth.id, cim_purchase)
        data = self.get_void_auth_data(auth)
        results = None
        if data:
            results = self.send_post(data, self.TRANS_VOID, testing, cim_purchase=cim_purchase)
            
        if results.success:
            auth.complete = True
            auth.save()
            
        return results

    def create_customer_profile(self, data, testing=False):
        template = get_template('bursar/create_customer_profile_transaction_request.xml')
        xml = t.render(Context(data))

    def create_payment_profile(self, data, testing=False):
        data['action'] = 'create'
        return self.send_payment_profile(data, testing)

    def update_payment_profile(self, data, testing=False):
        data['action'] = 'update'
        return self.send_payment_profile(data, testing)

    def send_payment_profile(self, data, testing=False):
        template = get_template('bursar/create_customer_profile_transaction_request.xml')
        xml = t.render(Context(data))

    def update_shipping_address(self, data, testing=False):
        data['action'] = 'update'
        return self.send_shipping_address(data, testing)

    def create_shipping_address(self, data, testing=False):
        data['action'] = 'create'
        return self.send_shipping_address(data, testing)

    def send_shipping_address(self, data, testing=False):
        template = get_template('bursar/customer_shipping_address_request.xml')
        xml = t.render(Context(data))

    def cim_post(self, url, xml_request):
        headers = { 'Content-Type' : 'text/xml' }
        conn = urllib2.Request(url=url, data=xml_request, headers=headers)
        f = urllib2.urlopen(conn)
        all_results = f.read()
        self.log_extra('Authorize response: %s', all_results)
        return parseString(all_results)
        
    def send_post(self, data, action, testing=False, cim_purchase=None, amount=NOTSET):
        """Execute the post to Authorize Net.
        
        Params:
        - data: dictionary as returned by get_standard_charge_data
        - testing: if true, then don't record the payment
        
        Returns:
        - ProcessorResult
        """
        assert(cim_purchase)
        self.log.info("About to send a request to authorize.net: %(connection)s\n%(logPostString)s", data)

        api_data = { 'api_login_id' : self.settings['API_LOGIN_KEY'], 'transaction_key' : self.settings['TRANKEY'] }
        object_data = { 'action' : self.TRANS_XML[action][1], 'purchase' : cim_purchase.purchase, 'cim_purchase' : cim_purchase }
        data.update(api_data)
        data.update(object_data)

        t = get_template('bursar/create_customer_profile_transaction_request.xml')
        xml_request = t.render(Context(data))
        print xml_request
        try:
            response = self.cim_post(url=data['connection'], xml_request=xml_request)
            print response
        except urllib2.URLError, ue:
            self.log.error("error opening %s\n%s", data['connection'], ue)
            return ProcessorResult(self.key, False, _('Could not talk to Authorize.net gateway'))
            
        success = response_code == '1'
        if amount == NOTSET:
            amount = data['amount']

        payment = None
        if success and not testing:
            if data.get('authorize_only', False):
                self.log_extra('Success, recording authorization')
                payment = self.record_authorization(cim_purchase=cim_purchase, amount=amount, 
                    transaction_id=transaction_id, reason_code=reason_code)
            else:
                if amount <= 0:
                    self.log_extra('Success, recording refund')
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
