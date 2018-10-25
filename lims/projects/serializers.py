from django.contrib.auth.models import User

from rest_framework import serializers

from lims.inventory.models import ItemType
from lims.inventory.serializers import LinkedItemSerializer
from lims.crm.serializers import CRMProjectSerializer
from lims.permissions.permissions import (SerializerPermissionsMixin,
                                          SerializerReadOnlyPermissionsMixin)
from lims.shared.models import Organism
from .models import (Project, ProjectStatus, Product, ProductStatus, Comment, WorkLog,
                     DeadlineExtension)
from lims.datastore.serializers import (CompactDataEntrySerializer, AttachmentSerializer,
                                        DataEntrySerializer)


class DeadlineExtensionSerializer(serializers.ModelSerializer):
    extended_by = serializers.SlugRelatedField(
        read_only=True,
        slug_field='username',
    )

    class Meta:
        model = DeadlineExtension
        fields = '__all__'


class ProjectSerializer(SerializerPermissionsMixin, serializers.ModelSerializer):
    project_identifier = serializers.CharField(read_only=True)
    identifier = serializers.IntegerField(read_only=True)
    primary_lab_contact = serializers.SlugRelatedField(
        queryset=User.objects.all(),
        slug_field='username',
    )
    created_by = serializers.SlugRelatedField(
        read_only=True,
        slug_field='username',
    )
    status = serializers.SlugRelatedField(
        queryset=ProjectStatus.objects.all(),
        slug_field='name',
    )
    crm_project = CRMProjectSerializer(read_only=True)
    links = serializers.JSONField(required=False, allow_null=True)
    past_deadline = serializers.BooleanField(read_only=True)
    warn_deadline = serializers.BooleanField(read_only=True)
    deadline_extensions = DeadlineExtensionSerializer(read_only=True, many=True)

    class Meta:
        model = Project
        fields = '__all__'
        read_only_fields = ('date_started',)


class SimpleProductSerializer(SerializerReadOnlyPermissionsMixin, serializers.ModelSerializer):
    product_identifier = serializers.CharField(read_only=True)
    runs = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    on_run = serializers.BooleanField(read_only=True)
    product_type = serializers.SlugRelatedField(
        queryset=ItemType.objects.all(),
        slug_field='name',
    )
    linked_inventory = LinkedItemSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = ['id', 'product_identifier', 'runs', 'on_run', 'product_type', 'name',
                  'linked_inventory']


class ProductSerializer(SerializerReadOnlyPermissionsMixin, serializers.ModelSerializer):
    identifier = serializers.CharField(read_only=True)
    product_identifier = serializers.CharField(read_only=True)
    runs = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    on_run = serializers.BooleanField(read_only=True)
    created_by = serializers.SlugRelatedField(
        read_only=True,
        slug_field='username',
    )
    product_type = serializers.SlugRelatedField(
        queryset=ItemType.objects.all(),
        slug_field='name',
    )
    status = serializers.SlugRelatedField(
        queryset=ProductStatus.objects.all(),
        slug_field='name',
    )
    optimised_for = serializers.SlugRelatedField(
        required=False,
        allow_null=True,
        queryset=Organism.objects.all(),
        slug_field='name',
    )
    design = serializers.CharField(allow_blank=True,
                                   required=False,
                                   write_only=True)

    class Meta:
        model = Product
        fields = '__all__'


class DetailedProductSerializer(ProductSerializer):
    linked_inventory = LinkedItemSerializer(many=True, read_only=True)
    data = CompactDataEntrySerializer(many=True, read_only=True)
    attachments = AttachmentSerializer(read_only=True, many=True)


class FullProductSerializer(ProductSerializer):
    data = DataEntrySerializer(many=True, read_only=True)


class ProductStatusSerializer(serializers.ModelSerializer):

    class Meta:
        model = ProductStatus
        fields = '__all__'


class ProjectStatusSerializer(serializers.ModelSerializer):

    class Meta:
        model = ProjectStatus
        fields = '__all__'


class CommentSerializer(serializers.ModelSerializer):

    class Meta:
        model = Comment
        fields = '__all__'


class WorkLogSerializer(serializers.ModelSerializer):

    class Meta:
        model = WorkLog
        fields = '__all__'
