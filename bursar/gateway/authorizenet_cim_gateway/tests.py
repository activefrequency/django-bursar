# -*- coding: UTF-8 -*-
"""Bursar Authorizenet Gateway Tests."""
from bursar.gateway.authorizenet_cim_gateway import processor
from bursar.gateway.authorizenet_cim_gateway.processor import AuthNetException
from bursar.errors import GatewayError
from bursar.models import Authorization, Payment, CreditCardDetail
from bursar.tests import make_test_purchase
from bursar.gateway.authorizenet_cim_gateway.models import CIMPurchase
from bursar.bursar_settings import get_bursar_setting
from decimal import Decimal
from django.conf import settings
from django.contrib.sites.models import Site
from django.core import urlresolvers
from django.core.urlresolvers import reverse as url
from django.test import TestCase
from django.test.client import Client

SKIP_TESTS = False
NEED_SETTINGS = """Tests for authorizenet_gateway module require an
AUTHORIZENET_TEST section in settings.BURSAR_SETTINGS.  At a 
minimum, you must specify the LOGIN, TRANKEY, and STORE_NAME."""

class TestGateway(TestCase):
    def setUp(self):
        global SKIP_TESTS
        self.client = Client()
        if not SKIP_TESTS:
            settings = get_bursar_setting('AUTHORIZENET_TEST', default_value=None)
            if not settings:
                SKIP_TESTS = True
                raise GatewayError(NEED_SETTINGS)
            settings['EXTRA_LOGGING'] = True
            self.gateway = processor.PaymentProcessor(settings=settings)
            self.default_payment = {
                'ccv' : '111',
                #'card_number' : '4111111111111111',
                'card_number' : '4007000000027',
                'expire_month' : 12,
                'expire_year' : 2012,
                'card_type' : 'visa'
            }

    def tearDown(self):
        pass

    def customer_profile(self, purchase):
        result = self.gateway.create_customer_profile(purchase)
        cim_purchase = CIMPurchase(purchase=purchase, customer_profile_id=result.message)
        return cim_purchase

    def payment_profile(self, cim_purchase):
        credit_card_number = '4007000000027'
        cc = CreditCardDetail()
        cc.credit_type='Visa'
        cc.expire_month='08'
        cc.expire_year='2012'
        cc.card_holder = "%s %s" % (cim_purchase.purchase.first_name, cim_purchase.purchase.last_name)
        result = self.gateway.create_payment_profile(cim_purchase, cc, credit_card_number)
        cim_purchase.payment_profile_id = result.message
        return cim_purchase

    def shipping(self, cim_purchase):
        result = self.gateway.create_shipping_address(cim_purchase)
        cim_purchase.shipping_address_id = result.message
        return cim_purchase

    def cim_setup(self):
        purchase = make_test_purchase(sub_total=Decimal('20.00'), email="jobelenus+af+lenspro@gmail.com")
        cim = self.customer_profile(purchase)
        cim = self.payment_profile(cim)
        cim = self.shipping(cim)
        data = {'customer_profile_id' : cim.customer_profile_id}
        self.gateway.delete_customer_profile(data)
        return cim

    def test_deletion(self):
        data = {'customer_profile_id' : 1775467}
        self.gateway.delete_customer_profile(data)

    def test_profile_setup(self):
        cim = self.cim_setup()
        #print cim.customer_profile_id
        #print cim.payment_profile_id
        #print cim.shipping_address_id
        self.assertEqual(isinstance(cim.customer_profile_id, AuthNetException), False)
        self.assertEqual(isinstance(cim.payment_profile_id, AuthNetException), False)
        self.assertEqual(isinstance(cim.shipping_address_id, AuthNetException), False)
        
    def test_authorize(self):
        if SKIP_TESTS: return
        cim_purchase = self.cim_setup()
        result = self.gateway.authorize_payment(cim_purchase=cim_purchase)
        self.assert_(result.success)
        payment = result.payment
        self.assertEqual(payment.amount, Decimal('20.00'))
        self.assertEqual(cim_purchase.purchase.total_payments, Decimal('0.00'))
        self.assertEqual(cim_purchase.purchase.authorized_remaining, Decimal('20.00'))

    def test_pending_authorize(self):
        if SKIP_TESTS: return
        cim_purchase = self.cim_setup()
        self.assert_(cim_purchase.purchase.credit_card)
        pending = self.gateway.create_pending_payment(cim_purchase)
        self.assertEqual(pending.amount, Decimal('20.00'))
        result = self.gateway.authorize_payment(cim_purchase=cim_purchase)
        self.assert_(result.success)
        payment = result.payment
        self.assertEqual(payment.amount, Decimal('20.00'))
        self.assertEqual(cim_purchase.purchase.total_payments, Decimal('0.00'))
        self.assertEqual(cim_purchase.purchase.authorized_remaining, Decimal('20.00'))

    def test_capture(self):
        """Test making a direct payment using AUTHORIZENET."""
        if SKIP_TESTS: return
        cim_purchase = self.cim_setup()
        self.assertEqual(cim_purchase.purchase.total, Decimal('10.00'))
        result = self.gateway.capture_payment(cim_purchase=cim_purchase)
        self.assert_(result.success)
        payment = result.payment
        self.assertEqual(payment.amount, Decimal('10.00'))
        self.assertEqual(cim_purchase.purchase.total_payments, Decimal('10.00'))
        self.assertEqual(cim_purchase.purchase.authorized_remaining, Decimal('0.00'))

    def test_authorize_multiple(self):
        """Test making multiple authorization using AUTHORIZENET."""
        if SKIP_TESTS: return
        cim_purchase = self.cim_setup()
        self.assertEqual(cim_purchase.purchase.total, Decimal('100.00'))
        pending = self.gateway.create_pending_payment(cim_purchase=cim_purchase, amount=Decimal('25.00'))
        self.assertEqual(pending.amount, Decimal('25.00'))
        self.assertEqual(cim_purchase.purchase.paymentspending.count(), 1)
        pending2 = cim_purchase.purchase.get_pending(self.gateway.key)
        self.assertEqual(pending, pending2)
        result = self.gateway.authorize_payment(cim_purchase)
        self.assertEqual(result.success, True)
        self.assertEqual(cim_purchase.purchase.authorized_remaining, Decimal('25.00'))
        self.assertEqual(cim_purchase.purchase.remaining, Decimal('75.00'))

        self.gateway.create_pending_payment(cim_purchase=cim_purchase, amount=Decimal('75.00'))
        result = self.gateway.authorize_payment(cim_purchase)
        self.assert_(result.success)
        auth = result.payment
        self.assertEqual(auth.amount, Decimal('75.00'))
        self.assertEqual(cim_purchase.purchase.authorized_remaining, Decimal('100.00'))

        results = self.gateway.capture_authorized_payments(cim_purchase)
        self.assertEqual(len(results), 2)
        r1 = results[0]
        r2 = results[1]
        self.assertEqual(r1.success, True)
        self.assertEqual(r2.success, True)
        self.assertEqual(cim_purchase.purchase.total_payments, Decimal('100'))

    def test_multiple_pending(self):
        """Test that creating a second pending payment deletes the first one."""
        if SKIP_TESTS: return
        cim_purchase = self.cim_setup()
        self.assertEqual(cim_purchase.purchase.total, Decimal('125.00'))
        pend1 = self.gateway.create_pending_payment(cim_purchase=cim_purchase, amount=cim_purchase.purchase.total)
        pend2 = self.gateway.create_pending_payment(cim_purchase=cim_purchase, amount=cim_purchase.purchase.total)
    
        self.assertEqual(cim_purchase.purchase.paymentspending.count(), 1)
