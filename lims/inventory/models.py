from django.db import models
from django.db.models.signals import post_init
import reversion
from django.contrib.auth.models import User
from lims.projects.models import StudyGroup

from mptt.models import MPTTModel, TreeForeignKey
from pint import UnitRegistry, UndefinedUnitError


@reversion.register()
class ItemType(MPTTModel):
    """
    Provides a tree based model of types, each which can have parents/children
    """
    name = models.CharField(max_length=150, unique=True, db_index=True)
    parent = TreeForeignKey('self', null=True, blank=True, related_name='children', db_index=True)

    class Meta:
        permissions = (
            ('view_itemtype', 'View item type',),
        )

    def has_children(self):
        return True if self.get_descendant_count() > 0 else False

    def display_name(self):
        if self.level > 0:
            return '{} {}'.format('\u00a0\u00a0\u00a0' * self.level, self.name)
        return self.name

    def root(self):
        if self.parent:
            return self.parent.get_root().name
        return self.name

    def __str__(self):
        return self.name


@reversion.register()
class Tag(models.Model):
    name = models.CharField(max_length=30)

    def __str__(self):
        return self.name


@reversion.register()
class AmountMeasure(models.Model):
    """
    A measurement and corrosponding postfix
    """
    name = models.CharField(max_length=100, unique=True, db_index=True)
    symbol = models.CharField(max_length=10, unique=True, db_index=True)

    class Meta:
        ordering = ['-id']
        permissions = (
            ('view_amountmeasure', 'View measure',),
        )

    def __str__(self):
        return "{} ({})".format(self.name, self.symbol)


@reversion.register()
class Location(MPTTModel):
    """
    Provides a physical location for an item
    """
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=12, unique=True, null=True)
    parent = TreeForeignKey('self', null=True, blank=True, related_name='children', db_index=True)

    class Meta:
        ordering = ['tree_id', '-lft']
        permissions = (
            ('view_location', 'View location',),
        )

    def has_children(self):
        return True if self.get_descendant_count() > 0 else False

    def display_name(self):
        if self.level > 0:
            return '{} {} ({})'.format('\u00a0\u00a0\u00a0' * self.level, self.name, self.code)
        return '{} ({})'.format(self.name, self.code)

    def __str__(self):
        if self.parent:
            return '{} ({})'.format(self.name, self.parent.name)
        return self.name


@reversion.register()
class Set(models.Model):
    """
    A named set of items in the inventory
    """
    name = models.CharField(max_length=40)
    is_public = models.BooleanField(default=False)
    is_partset = models.BooleanField(default=False)

    class Meta:
        ordering = ['-id']
        permissions = (
            ('view_set', 'View item set',),
        )

    def number_of_items(self):
        return self.items.count()

    def __str__(self):
        return self.name


@reversion.register()
class Item(models.Model):
    """
    Represents an item in a inventory
    """
    name = models.CharField(max_length=200, db_index=True)
    identifier = models.CharField(max_length=200, null=True, blank=True, db_index=True)
    barcode = models.CharField(max_length=128, null=True, blank=True, db_index=True, unique=True)
    description = models.TextField(blank=True, null=True)
    item_type = TreeForeignKey(ItemType)

    tags = models.ManyToManyField(Tag, blank=True)

    in_inventory = models.BooleanField(default=False)
    amount_available = models.FloatField(default=0)
    amount_measure = models.ForeignKey(AmountMeasure)
    concentration = models.FloatField(default=0)
    concentration_measure = models.ForeignKey(AmountMeasure,
                                              blank=True, null=True,
                                              related_name='concentration_measure')
    location = TreeForeignKey(Location)

    # Add an optional "wells" for recording the number of items that can fit on it, if any
    wells = models.IntegerField(default=0)

    added_by = models.ForeignKey(User)
    added_on = models.DateTimeField(auto_now_add=True)
    last_updated_on = models.DateTimeField(auto_now=True)

    sets = models.ManyToManyField(Set, related_name='items', blank=True)

    created_from = models.ManyToManyField('self', blank=True, symmetrical=False)

    class Meta:
        ordering = ['-id']
        permissions = (
            ('view_item', 'View item',),
        )

    def get_tags(self):
        return ", ".join([t.name for t in self.tags.all()])

    def location_path(self):
        return ' > '.join([x.name for x in self.location.get_ancestors(include_self=True)])

    def save(self, *args, **kwargs):
        if self.amount_available > 0:
            self.in_inventory = True
        super(Item, self).save(*args, **kwargs)

    def __str__(self):
        return self.name


@reversion.register()
class ItemProperty(models.Model):
    """
    Represents a singular user defined property of an item
    """
    item = models.ForeignKey(Item, related_name='properties')
    name = models.CharField(max_length=200, db_index=True)
    value = models.TextField(db_index=True)

    def __str__(self):
        return self.name


@reversion.register()
class ItemTransfer(models.Model):
    """
    Represents an amount of item in transfer for task
    """
    item = models.ForeignKey(Item, related_name='transfers')
    # The amount originally taken from the inventory
    amount_taken = models.FloatField(default=0)
    # The amount now available in this transfer
    amount_available = models.FloatField(default=0)
    # The amount to take from this transfer (set initially as amount_taken)
    amount_to_take = models.FloatField(default=0)
    amount_measure = models.ForeignKey(AmountMeasure)
    run_identifier = models.UUIDField(blank=True, null=True, db_index=True)
    barcode = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    coordinates = models.CharField(max_length=2, blank=True, null=True)

    # Link the current transfer to a another ItemTransfer e.g. a plate
    # Can then use this to construct a "plate view"
    linked_transfer = models.ForeignKey('self', blank=True, null=True)

    # Location -> not yet implemented
    # location = models.ForeignKey(Location, default=get_default_location)

    date_created = models.DateTimeField(auto_now_add=True)

    # You're adding not taking away
    is_addition = models.BooleanField(default=False)

    # The transfer has taken out from the inventory; It might not yet be finished
    # so we need a way of knowing if we should take stuff out or not
    has_taken = models.BooleanField(default=False, db_index=True)

    # The transfer has completed and there is nothing left, this is now just
    # a history entry
    transfer_complete = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ['-date_created']

    def item_name(self):
        return self.item.name

    def save(self, *args, **kwargs):
        # Match some fields
        if self.pk is None:
            if self.amount_available == 0:
                self.amount_available = self.amount_taken
            self.amount_to_take = self.amount_taken
        # Link to an existing ItemTransfer with the given barcode
        if self.barcode:
            try:
                linked = ItemTransfer.objects.get(barcode=self.barcode, transfer_complete=False)
            except:
                pass
            else:
                self.linked_transfer = linked
        super(ItemTransfer, self).save(*args, **kwargs)

    def _as_measured_value(self, amount, measure, ureg):
        """
        Convert if possible to a value with units
        """
        try:
            value = amount * ureg(measure)
        except UndefinedUnitError:
            value = amount * ureg.count
        return value

    def check_transfer(self):
        ureg = UnitRegistry()
        existing = self._as_measured_value(self.item.amount_available,
                                           self.item.amount_measure.symbol,
                                           ureg)
        to_take = self._as_measured_value(self.amount_taken,
                                          self.amount_measure.symbol,
                                          ureg)
        if not self.is_addition and existing < to_take:
            missing = ((existing - to_take) * -1)
            return (False, missing)
        return (True, 0)

    def do_transfer(self, ureg=False):
        """
        Alter the item to reflect new amount
        """
        if not ureg:
            ureg = UnitRegistry()
        # Check if it is taking stuff from inventory or not
        # Note: if something has been taken you cannot put it
        # back
        if not self.has_taken:
            existing = self._as_measured_value(self.item.amount_available,
                                               self.item.amount_measure.symbol,
                                               ureg)
            to_take = self._as_measured_value(self.amount_taken,
                                              self.amount_measure.symbol,
                                              ureg)
            if self.is_addition:
                new_amount = existing + to_take
            else:
                if existing > to_take:
                    new_amount = existing - to_take
                else:
                    return False
            self.item.amount_available = new_amount.magnitude
            self.item.save()
            self.amount_available = self.amount_available - to_take.magnitude
        else:
            # We take from the transfer not the actual item since we've
            # already got it from the item
            existing = self._as_measured_value(self.amount_available,
                                               self.amount_measure.symbol,
                                               ureg)
            to_take = self._as_measured_value(self.amount_to_take,
                                              self.amount_measure.symbol,
                                              ureg)
            if self.is_addition:
                new_amount = existing + to_take
            else:
                if existing > to_take:
                    new_amount = existing - to_take
                else:
                    return False
            self.amount_available = new_amount.magnitude
        self.save()
        return True

    def do_complete(self, ureg=False):
        """
        Check if there is anything left available, if not complete.
        """
        if self.amount_available == 0:
            self.transfer_complete = True
        self.has_taken = True
        self.save()

    def __str__(self):
        return '{} {}/{}'.format(self.item.name, self.barcode, self.coordinates)


def initItemTransfer(**kwargs):
    """
    Extra init stuff for creating ItemTransfers
    """
    instance = kwargs.get('instance')
    # Convience, these are identical values at creation
    if instance.pk is None:
        instance.amount_available = instance.amount_taken
        instance.amount_to_take = instance.amount_taken
post_init.connect(initItemTransfer, ItemTransfer)


@reversion.register
class Animal(models.Model):
    name = models.CharField(max_length=45)
    description = models.TextField()
    species = models.PositiveIntegerField(default=0)
    gender = models.PositiveIntegerField(default=0)
    #TODO one-to many to study_group
    study_group = models.ForeignKey(StudyGroup)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return self.name

