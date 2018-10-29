# -*- coding: utf-8 -*-
# Generated by Django 1.11.3 on 2018-10-29 08:05
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0023_auto_20180315_1414'),
        ('projects', '0025_remove_product_location'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='location',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='inventory.Location'),
        ),
        migrations.AddField(
            model_name='product',
            name='product_location',
            field=models.CharField(db_index=True, default='', max_length=20),
        ),
    ]
