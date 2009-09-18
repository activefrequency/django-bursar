from livesettings import *
from django.utils.translation import ugettext_lazy as _

# this is so that the translation utility will pick up the string
gettext = lambda s: s
_strings = (gettext('CreditCard'), gettext('Credit Card'))

GATEWAY_MODULES = config_get('GATEWAY', 'MODULES')
GATEWAY_MODULES.add_choice(('GATEWAY_CYBERSOURCE', 'Cybersource'))


GATEWAY_GROUP = ConfigurationGroup('GATEWAY_CYBERSOURCE', 
    _('Cybersource.net Payment Settings'), 
    requires=GATEWAY_MODULES,
    ordering=102)

config_register_list(

    StringValue(GATEWAY_GROUP, 
        'CONNECTION',
        description=_("WSDL URL"),
        help_text=_("""This is the address to submit live transactions."""),
        default='https://ics2ws.ic3.com/commerce/1.x/transactionProcessor/CyberSourceTransaction_1.26.wsdl'),

    StringValue(GATEWAY_GROUP, 
        'CONNECTION_TEST',
        description=_("Submit to Test WSDL URL"),
        help_text=("""This is the address to submit test transactions"""),
        default='https://ics2wstest.ic3.com/commerce/1.x/transactionProcessor/CyberSourceTransaction_1.26.wsdl'),

    BooleanValue(GATEWAY_GROUP, 
        'SSL', 
        description=_("Use SSL for the checkout pages?"), 
        default=False),
    
    BooleanValue(GATEWAY_GROUP, 
        'LIVE', 
        description=_("Accept real payments"),
        help_text=_("False if you want to be in test mode"),
        default=False),
    
    ModuleValue(GATEWAY_GROUP,
        'MODULE',
        description=_('Implementation module'),
        hidden=True,
        default = 'payment.modules.cybersource'),
    
    StringValue(GATEWAY_GROUP,
        'KEY',
        description=_("Module key"),
        hidden=True,
        default = 'CYBERSOURCE'),

    StringValue(GATEWAY_GROUP,
        'LABEL',
        description=_('English name for this group on the checkout screens'),
        default = 'Credit Cards',
        help_text = _('This will be passed to the translation utility')),
        
    StringValue(GATEWAY_GROUP,
        'CURRENCY_CODE',
        description=_('Currency Code'),
        help_text=_('Currency code for Cybersource transactions.'),
        default = 'USD'),
        
    StringValue(GATEWAY_GROUP,
        'URL_BASE',
        description=_('The url base used for constructing urlpatterns which will use this module'),
        default = r'^credit/'),

    MultipleStringValue(GATEWAY_GROUP,
        'CREDITCHOICES',
        description=_('Available credit cards'),
        choices = (
            (('American Express', 'American Express')),
            (('Visa','Visa')),
            (('Mastercard','Mastercard')),
            (('Discover','Discover'))),
        default = ('Visa', 'Mastercard', 'Discover')),
    
    StringValue(GATEWAY_GROUP, 
        'MERCHANT_ID', 
        description=_('Your Cybersource merchant ID'),
        default=""),
    
    LongStringValue(GATEWAY_GROUP, 
        'TRANKEY', 
        description=_('Your Cybersource transaction key'),
        default=""),
        
    BooleanValue(GATEWAY_GROUP,
        'EXTRA_LOGGING',
        description=_("Verbose logs"),
        help_text=_("Add extensive logs during post."),
        default=False)
)