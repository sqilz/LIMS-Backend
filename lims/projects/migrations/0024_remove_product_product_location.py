# -*- coding: utf-8 -*-
# Generated by Django 1.11.3 on 2018-10-29 07:27
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0023_auto_20181029_0724'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='product',
            name='product_location',
        ),
    ]