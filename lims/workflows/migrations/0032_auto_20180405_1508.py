# -*- coding: utf-8 -*-
# Generated by Django 1.11.3 on 2018-04-05 15:08
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workflows', '0031_stepfieldproperty_measure_not_required'),
    ]

    operations = [
        migrations.AlterField(
            model_name='workflow',
            name='order',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]