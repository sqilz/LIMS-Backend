# -*- coding: utf-8 -*-
# Generated by Django 1.11.3 on 2018-03-01 09:58
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shared', '0004_trigger_fire_on_create'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='organism',
            options={'ordering': ['-id']},
        ),
        migrations.AlterModelOptions(
            name='trigger',
            options={'ordering': ['-id']},
        ),
        migrations.AlterModelOptions(
            name='triggeralert',
            options={'ordering': ['-id']},
        ),
        migrations.AlterModelOptions(
            name='triggeralertstatus',
            options={'ordering': ['-id']},
        ),
        migrations.AlterModelOptions(
            name='triggerset',
            options={'ordering': ['-id']},
        ),
        migrations.AlterModelOptions(
            name='triggersubscription',
            options={'ordering': ['-id']},
        ),
        migrations.AlterField(
            model_name='triggerset',
            name='email_title',
            field=models.CharField(default='Alert from Leaf LIMS', max_length=255),
        ),
    ]
