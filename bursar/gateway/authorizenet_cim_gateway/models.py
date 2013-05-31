from django.db import models

class CIMPurchase(models.Model):
    purchase = models.OneToOneField('bursar.Purchase', blank=True, null=True, related_name="cim_purchase")
    customer_profile_id = models.IntegerField(blank=True, null=True)
    payment_profile_id = models.IntegerField(blank=True, null=True)
    shipping_address_id = models.IntegerField(blank=True, null=True)

    class Meta:
        app_label = 'authorizenet_cim_gateway'
