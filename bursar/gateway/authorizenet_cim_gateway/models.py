from django.db import models
from bursar.models import Purchase

class CIMPurchase(models.Model):
    purchase = models.ForeignKey(Purchase, blank=True, null=True)
    customer_profile_id = models.IntegerField(blank=True, null=True)
    payment_profile_id = models.IntegerField(blank=True, null=True)
    shipping_address_id = models.IntegerField(blank=True, null=True)
