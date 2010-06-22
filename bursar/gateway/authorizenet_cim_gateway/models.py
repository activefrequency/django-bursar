from django.db import models
from bursar.models import Purchase

class CIMPurchase(models.Model):
    purchase = models.OneToOneField(Purchase, blank=True, null=True, related_name="cim_purchase")
    customer_profile_id = models.IntegerField(blank=True, null=True)
    payment_profile_id = models.IntegerField(blank=True, null=True)
    shipping_address_id = models.IntegerField(blank=True, null=True)
