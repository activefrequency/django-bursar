# encoding: utf-8
import datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models

class Migration(SchemaMigration):
    
    def forwards(self, orm):
        
        # Changing field 'CIMPurchase.purchase'
        db.alter_column('bursar_cimpurchase', 'purchase_id', self.gf('django.db.models.fields.related.OneToOneField')(blank=True, unique=True, null=True, to=orm['bursar.Purchase']))

        # Adding unique constraint on 'CIMPurchase', fields ['purchase']
        db.create_unique('bursar_cimpurchase', ['purchase_id'])
    
    
    def backwards(self, orm):
        
        # Changing field 'CIMPurchase.purchase'
        db.alter_column('bursar_cimpurchase', 'purchase_id', self.gf('django.db.models.fields.related.ForeignKey')(to=orm['bursar.Purchase'], null=True, blank=True))

        # Removing unique constraint on 'CIMPurchase', fields ['purchase']
        db.delete_unique('bursar_cimpurchase', ['purchase_id'])
    
    
    models = {
        'bursar.cimpurchase': {
            'Meta': {'object_name': 'CIMPurchase'},
            'customer_profile_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'payment_profile_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'purchase': ('django.db.models.fields.related.OneToOneField', [], {'blank': 'True', 'related_name': "'cim_purchase'", 'unique': 'True', 'null': 'True', 'to': "orm['bursar.Purchase']"}),
            'shipping_address_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'})
        },
        'bursar.purchase': {
            'Meta': {'object_name': 'Purchase'},
            'bill_city': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '50'}),
            'bill_country': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '2'}),
            'bill_postal_code': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '30'}),
            'bill_state': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '50'}),
            'bill_street1': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '80'}),
            'bill_street2': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '80'}),
            'email': ('django.db.models.fields.EmailField', [], {'default': "''", 'max_length': '75'}),
            'first_name': ('django.db.models.fields.CharField', [], {'max_length': '30'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'last_name': ('django.db.models.fields.CharField', [], {'max_length': '30'}),
            'orderno': ('django.db.models.fields.CharField', [], {'max_length': '20'}),
            'phone': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '30'}),
            'ship_city': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '50'}),
            'ship_country': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '2'}),
            'ship_postal_code': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '30'}),
            'ship_state': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '50'}),
            'ship_street1': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '80'}),
            'ship_street2': ('django.db.models.fields.CharField', [], {'default': "''", 'max_length': '80'}),
            'shipping': ('bursar.fields.CurrencyField', [], {'null': 'True', 'max_digits': '18', 'decimal_places': '2', 'blank': 'True'}),
            'site': ('django.db.models.fields.related.ForeignKey', [], {'to': "orm['sites.Site']"}),
            'sub_total': ('bursar.fields.CurrencyField', [], {'null': 'True', 'max_digits': '18', 'decimal_places': '2', 'blank': 'True'}),
            'tax': ('bursar.fields.CurrencyField', [], {'null': 'True', 'max_digits': '18', 'decimal_places': '2', 'blank': 'True'}),
            'time_stamp': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'total': ('bursar.fields.CurrencyField', [], {'max_digits': '18', 'decimal_places': '2'})
        },
        'sites.site': {
            'Meta': {'object_name': 'Site', 'db_table': "'django_site'"},
            'domain': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '50'})
        }
    }
    
    complete_apps = ['bursar']
