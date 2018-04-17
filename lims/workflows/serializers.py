import re

from rest_framework import serializers
from pyparsing import ParseException

from lims.permissions.permissions import SerializerPermissionsMixin

from lims.equipment.models import Equipment
from lims.filetemplate.models import FileTemplate
from lims.inventory.models import ItemType, AmountMeasure
from lims.inventory.serializers import ItemTransferPreviewSerializer
from lims.projects.serializers import DetailedProductSerializer, SimpleProductSerializer
from .models import (Workflow,
                     Run,
                     TaskTemplate, InputFieldTemplate, VariableFieldTemplate,
                     OutputFieldTemplate, CalculationFieldTemplate, StepFieldTemplate,
                     StepFieldProperty)
from .calculation import NumericStringParser


class WorkflowSerializer(SerializerPermissionsMixin, serializers.ModelSerializer):
    created_by = serializers.SlugRelatedField(
        slug_field='username',
        read_only=True
    )

    class Meta:
        model = Workflow
        fields = '__all__'


class WorkflowExportSerializer(serializers.ModelSerializer):

    class Meta:
        model = Workflow
        fields = '__all__'
        depth = 5


class InputFieldTemplateSerializer(serializers.ModelSerializer):
    measure = serializers.SlugRelatedField(
        queryset=AmountMeasure.objects.all(),
        slug_field='symbol'
    )
    lookup_type = serializers.SlugRelatedField(
        queryset=ItemType.objects.all(),
        slug_field='name'
    )
    field_name = serializers.CharField(read_only=True)
    store_value_in = serializers.CharField(read_only=True)

    class Meta:
        model = InputFieldTemplate
        fields = '__all__'


class InputFieldImportSerializer(InputFieldTemplateSerializer):
    class Meta:
        model = InputFieldTemplate
        exclude = ('template', 'id')


class InputFieldValueSerializer(serializers.Serializer):
    """
    Serializes the values from an input field
    """
    label = serializers.CharField()
    amount = serializers.FloatField()
    measure = serializers.CharField()
    inventory_identifier = serializers.CharField(required=False)
    from_input_file = serializers.NullBooleanField()
    from_calculation = serializers.BooleanField(required=False, default=False)
    calculation_used = serializers.IntegerField(required=False, allow_null=True)
    auto_find_in_inventory = serializers.BooleanField(required=False, default=False)

    destination_barcode = serializers.CharField(required=False, allow_null=True)
    destination_coordinates = serializers.CharField(required=False, allow_null=True)


class VariableFieldTemplateSerializer(serializers.ModelSerializer):
    measure = serializers.SlugRelatedField(
        allow_null=True,
        required=False,
        queryset=AmountMeasure.objects.all(),
        slug_field='symbol'
    )
    field_name = serializers.CharField(read_only=True)

    class Meta:
        model = VariableFieldTemplate
        fields = '__all__'


class VariableFieldImportSerializer(VariableFieldTemplateSerializer):
    class Meta:
        model = VariableFieldTemplate
        exclude = ('template', 'id')


class VariableFieldValueSerializer(serializers.Serializer):
    """
    Serializes the values from an input field
    """
    label = serializers.CharField()
    amount = serializers.FloatField()
    measure = serializers.CharField(required=False, allow_null=True)
    measure_not_required = serializers.NullBooleanField(required=False)
    calculation_used = serializers.IntegerField(required=False, allow_null=True)


class OutputFieldTemplateSerializer(serializers.ModelSerializer):
    measure = serializers.SlugRelatedField(
        queryset=AmountMeasure.objects.all(),
        slug_field='symbol'
    )
    lookup_type = serializers.SlugRelatedField(
        queryset=ItemType.objects.all(),
        slug_field='name'
    )
    field_name = serializers.CharField(read_only=True)

    class Meta:
        model = OutputFieldTemplate
        fields = '__all__'


class OutputFieldImportSerializer(OutputFieldTemplateSerializer):
    class Meta:
        model = OutputFieldTemplate
        exclude = ('template', 'id')


class OutputFieldValueSerializer(serializers.Serializer):
    """
    Serializes the values from an input field
    """
    label = serializers.CharField()
    amount = serializers.FloatField()
    measure = serializers.CharField()
    lookup_type = serializers.CharField()
    calculation_used = serializers.IntegerField(required=False, allow_null=True)


class CalculationFieldTemplateSerializer(serializers.ModelSerializer):
    field_name = serializers.CharField(read_only=True)

    class Meta:
        model = CalculationFieldTemplate
        fields = '__all__'


class CalculationFieldImportSerializer(CalculationFieldTemplateSerializer):
    class Meta:
        model = CalculationFieldTemplate
        exclude = ('template', 'id')


class CalculationFieldIDTemplateSerializer(CalculationFieldTemplateSerializer):
    """
    Used for when an ID is also needed
    """
    id = serializers.IntegerField()


class CalculationFieldValueSerializer(serializers.Serializer):
    """
    Serializes the values from an input field
    """
    id = serializers.IntegerField()
    label = serializers.CharField()
    calculation = serializers.CharField()


class StepFieldPropertySerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(allow_null=True, required=False)
    measure = serializers.SlugRelatedField(
        queryset=AmountMeasure.objects.all(),
        slug_field='symbol',
        required=False,
        allow_null=True,
    )
    field_name = serializers.CharField(read_only=True)

    class Meta:
        model = StepFieldProperty
        fields = ('id', 'measure', 'amount', 'label',
                  'from_calculation', 'calculation_used', 'measure_not_required', 'field_name',)


class StepFieldPropertyImportSerializer(StepFieldPropertySerializer):
    class Meta:
        model = StepFieldProperty
        exclude = ('template', 'id')


class StepFieldPropertyValueSerializer(serializers.Serializer):
    """
    Serializes the values from an input field
    """
    id = serializers.IntegerField(required=False, allow_null=True)
    label = serializers.CharField()
    amount = serializers.FloatField()
    measure = serializers.CharField(required=False, allow_null=True)
    measure_not_required = serializers.NullBooleanField(required=False)
    calculation_used = serializers.IntegerField(required=False, allow_null=True)


class StepFieldTemplateSerializer(serializers.ModelSerializer):
    properties = StepFieldPropertySerializer(many=True, required=False)
    field_name = serializers.CharField(read_only=True)

    class Meta:
        model = StepFieldTemplate
        fields = '__all__'

    def create(self, validated_data):
        try:
            property_fields = validated_data.pop('properties')
        except:
            property_fields = []
        step = StepFieldTemplate.objects.create(**validated_data)
        for field in property_fields:
            StepFieldProperty.objects.create(step=step, **field)
        return step

    def update(self, instance, validated_data):
        properties_data = validated_data.pop('properties')

        properties = instance.properties

        instance.name = validated_data.get('label', instance.label)
        instance.description = validated_data.get('description', instance.description)
        instance.save()

        property_ids = [item['id'] for item in properties_data if 'id' in item]
        for field in properties.all():
            if field.id not in property_ids:
                field.delete()

        for f in properties_data:
            field = StepFieldProperty(step=instance, **f)
            field.save()

        return instance


class StepFieldImportSerializer(StepFieldTemplateSerializer):
    class Meta:
        model = StepFieldTemplate
        exclude = ('template', 'id')


class StepFieldValueSerializer(serializers.Serializer):
    label = serializers.CharField()
    description = serializers.CharField(required=False, allow_null=True)
    properties = StepFieldPropertyValueSerializer(many=True)


class TaskTemplateSerializer(SerializerPermissionsMixin, serializers.ModelSerializer):
    created_by = serializers.SlugRelatedField(
        slug_field='username',
        read_only=True
    )
    product_input = serializers.SlugRelatedField(
        queryset=ItemType.objects.all(),
        slug_field='name',
        allow_null=True,
    )
    product_input_measure = serializers.SlugRelatedField(
        queryset=AmountMeasure.objects.all(),
        slug_field='symbol',
        allow_null=True,
    )
    product_input_amount = serializers.FloatField(allow_null=True)
    labware = serializers.SlugRelatedField(
        required=False,
        allow_null=True,
        queryset=ItemType.objects.all(),
        slug_field='name'
    )
    labware_amount = serializers.IntegerField(required=False)
    capable_equipment = serializers.SlugRelatedField(
        required=False,
        many=True,
        queryset=Equipment.objects.all(),
        slug_field='name'
    )
    input_files = serializers.SlugRelatedField(
        required=False,
        many=True,
        queryset=FileTemplate.objects.all(),
        slug_field='name'
    )
    output_files = serializers.SlugRelatedField(
        required=False,
        many=True,
        queryset=FileTemplate.objects.all(),
        slug_field='name'
    )
    equipment_files = serializers.SlugRelatedField(
        required=False,
        many=True,
        queryset=FileTemplate.objects.all(),
        slug_field='name'
    )
    input_fields = InputFieldTemplateSerializer(read_only=True, many=True)
    variable_fields = VariableFieldTemplateSerializer(read_only=True, many=True)
    calculation_fields = CalculationFieldTemplateSerializer(read_only=True, many=True)
    output_fields = OutputFieldTemplateSerializer(read_only=True, many=True)
    step_fields = StepFieldTemplateSerializer(read_only=True, many=True)
    store_labware_as = serializers.CharField(read_only=True)

    class Meta:
        model = TaskTemplate
        fields = '__all__'

    def to_representation(self, obj):
        rep = super(TaskTemplateSerializer, self).to_representation(obj)
        self.handle_calculation(rep)
        return rep

    def _replace_fields(self, match):
        """
        Replace field names with their correct values
        """
        mtch = match.group(1)
        if mtch in self.flat:
            return str(self.flat[mtch])
        return str(0)

    def _perform_calculation(self, calculation):
        """
        Parse and perform a calculation using a dict of fields

        Using either a dict of values to field names

        Returns a NaN if the calculation cannot be performed, e.g.
        incorrect field names.
        """
        nsp = NumericStringParser()
        field_regex = r'\{(.+?)\}'
        interpolated_calculation = re.sub(field_regex, self._replace_fields, calculation)
        try:
            result = nsp.eval(interpolated_calculation)
        except ParseException:
            return None
        return result

    def _flatten_values(self, rep):
        flat_values = {}
        for field_type in ['input_fields', 'step_fields', 'variable_fields']:
            if field_type in rep:
                for field in rep[field_type]:
                    if field_type == 'step_fields':
                        for prop in field['properties']:
                            flat_values[prop['label']] = prop['amount']
                    else:
                        flat_values[field['label']] = field['amount']
        if 'product_input_amount' in rep:
            flat_values['product_input_amount'] = rep['product_input_amount']
        return flat_values

    def handle_calculation(self, rep):
        """
        Perform calculations on all calculation fields on the task

        If any data is provided, use that as source for the calculations
        rather than the defaults on the model.
        """
        # Flatten fields into named dict/ordered dict
        # Will need some sort of defer if not completed calculation dependent on other calculation
        if 'calculation_fields' in rep:
            self.flat = self._flatten_values(rep)
            for calc in rep['calculation_fields']:
                result = self._perform_calculation(calc['calculation'])
                calc['result'] = result
        return rep


class TaskTemplateNoProductInputSerializer(TaskTemplateSerializer):
    product_input = serializers.SlugRelatedField(
        queryset=ItemType.objects.all(),
        slug_field='name',
        required=False,
        allow_null=True,
    )
    product_input_measure = serializers.SlugRelatedField(
        queryset=AmountMeasure.objects.all(),
        slug_field='symbol',
        required=False,
        allow_null=True,
    )
    product_input_amount = serializers.FloatField(required=False, allow_null=True)


class RecalculateTaskTemplateSerializer(TaskTemplateSerializer):
    """
    Same as TaskTemplateSerializer but with ID's + no save
    """
    id = serializers.IntegerField()
    input_fields = InputFieldTemplateSerializer(many=True)
    variable_fields = VariableFieldTemplateSerializer(many=True)
    calculation_fields = CalculationFieldIDTemplateSerializer(many=True)
    output_fields = OutputFieldTemplateSerializer(many=True)
    step_fields = StepFieldTemplateSerializer(many=True)
    store_labware_as = serializers.CharField()
    created_by = serializers.CharField()  # Prevents modification of read-only User objects

    def save(self):
        # NEVER allow this serializer to create a new object
        return False


class SimpleTaskTemplateSerializer(TaskTemplateSerializer):
    valid_product_input_types = serializers.ListField(read_only=True)

    class Meta:
        model = TaskTemplate
        fields = ('id', 'name', 'description', 'product_input', 'valid_product_input_types',
                  'capable_equipment', 'created_by', 'date_created', 'product_input_not_required')


class TaskValuesSerializer(serializers.Serializer):
    product_input_not_required = serializers.NullBooleanField(required=False)
    product_input = serializers.CharField()
    product_input_amount = serializers.FloatField()
    product_input_measure = serializers.CharField()
    labware_not_required = serializers.NullBooleanField()
    labware_identifier = serializers.CharField(required=False, allow_null=True)
    labware_amount = serializers.IntegerField(required=False)
    labware_barcode = serializers.CharField(required=False, allow_null=True)
    equipment_choice = serializers.CharField(required=False, allow_null=True)
    input_fields = InputFieldValueSerializer(many=True)
    variable_fields = VariableFieldValueSerializer(many=True)
    calculation_fields = CalculationFieldValueSerializer(many=True)
    output_fields = OutputFieldValueSerializer(many=True)
    step_fields = StepFieldValueSerializer(many=True)


class TaskValuesNoProductInputSerializer(TaskValuesSerializer):
    product_input = serializers.CharField(required=False, allow_null=True)
    product_input_amount = serializers.FloatField(required=False, allow_null=True)
    product_input_measure = serializers.CharField(required=False, allow_null=True)


class TaskExportSerializer(serializers.ModelSerializer):
    input_fields = InputFieldTemplateSerializer(read_only=True, many=True)
    variable_fields = VariableFieldTemplateSerializer(read_only=True, many=True)
    calculation_fields = CalculationFieldTemplateSerializer(read_only=True, many=True)
    output_fields = OutputFieldTemplateSerializer(read_only=True, many=True)
    step_fields = StepFieldTemplateSerializer(read_only=True, many=True)

    class Meta:
        model = TaskTemplate
        fields = '__all__'
        depth = 10


class WorkflowImportSerializer(SerializerPermissionsMixin, serializers.Serializer):
    name = serializers.CharField()
    data = serializers.JSONField()


class RunSerializer(SerializerPermissionsMixin, serializers.ModelSerializer):
    """
    Provides basic serialisation of workflow run
    """
    started_by = serializers.SlugRelatedField(
        slug_field='username',
        read_only=True
    )
    tasks_list = SimpleTaskTemplateSerializer(read_only=True, many=True,
                                              source='get_tasks')
    validate_inputs = serializers.DictField(source='has_valid_inputs', read_only=True)
    products_list = SimpleProductSerializer(read_only=True, many=True, source='products')

    class Meta:
        model = Run
        fields = '__all__'


class DetailedRunSerializer(serializers.ModelSerializer):
    validate_inputs = serializers.DictField(source='has_valid_inputs')
    products = DetailedProductSerializer(read_only=True, many=True)
    tasks = SimpleTaskTemplateSerializer(read_only=True, many=True,
                                         source='get_tasks')
    transfers = ItemTransferPreviewSerializer(read_only=True, many=True)

    class Meta:
        model = Run
        fields = '__all__'
