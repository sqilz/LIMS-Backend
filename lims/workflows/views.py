from io import TextIOWrapper
import json
import copy
import uuid
import re

from pint import UnitRegistry, UndefinedUnitError
from pyparsing import ParseException

from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned

from django.utils import timezone
from guardian.shortcuts import get_group_perms

import django_filters

from rest_framework import viewsets
from rest_framework.views import APIView
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.decorators import detail_route
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.validators import ValidationError
from rest_framework.filters import (OrderingFilter,
                                    SearchFilter,
                                    DjangoFilterBackend)
from rest_framework.reverse import reverse
from rest_framework.exceptions import PermissionDenied, NotFound
from rest_framework_csv.renderers import CSVRenderer

from lims.shared.filters import ListFilter
from lims.permissions.permissions import (ViewPermissionsMixin,
                                          ExtendedObjectPermissions,
                                          ExtendedObjectPermissionsFilter)

from lims.shared.mixins import StatsViewMixin, AuditTrailViewMixin
from lims.inventory.models import (Item, ItemTransfer, AmountMeasure, Location,
                                   ItemType)
from lims.filetemplate.models import FileTemplate
from lims.filetemplate.serializers import FileTemplateSerializer  # noqa
from lims.inventory.serializers import (ItemTransferPreviewSerializer,  # noqa
                                        AmountMeasureSerializer,  # noqa
                                        LocationSerializer,  # noqa
                                        ItemTypeSerializer)  # noqa
# Disable flake8 on this line as we need the templates to be imported but
# they do not appear to be used (selected from globals)
from .models import (Workflow,  # noqa
                     Run, RunLabware,  # noqa
                     TaskTemplate, InputFieldTemplate, OutputFieldTemplate,  # noqa
                     StepFieldTemplate, VariableFieldTemplate,  # noqa
                     CalculationFieldTemplate)  # noqa
from .serializers import (WorkflowSerializer, WorkflowExportSerializer,  # noqa
                          WorkflowImportSerializer,  # noqa
                          SimpleTaskTemplateSerializer,  # noqa
                          TaskTemplateSerializer,  # noqa
                          TaskTemplateNoProductInputSerializer,  # noqa
                          TaskValuesSerializer,  # noqa
                          TaskValuesNoProductInputSerializer,  # noqa
                          TaskExportSerializer,  # noqa
                          RunSerializer,  # noqa
                          DetailedRunSerializer,  # noqa
                          InputFieldTemplateSerializer,  # noqa
                          OutputFieldTemplateSerializer,  # noqa
                          VariableFieldTemplateSerializer,  # noqa
                          VariableFieldValueSerializer,  # noqa
                          StepFieldTemplateSerializer,  # noqa
                          CalculationFieldTemplateSerializer,  # noqa
                          RecalculateTaskTemplateSerializer)  # noqa
from .serializers import (  # noqa
                          InputFieldImportSerializer,  # noqa
                          OutputFieldImportSerializer,  # noqa
                          VariableFieldImportSerializer,  # noqa
                          VariableFieldValueSerializer,  # noqa
                          StepFieldImportSerializer,  # noqa
                          CalculationFieldImportSerializer,  # noqa
                          )  # noqa
from lims.datastore.models import DataEntry
from lims.datastore.serializers import DataEntrySerializer
from lims.equipment.models import Equipment
from .calculation import NumericStringParser


class WorkflowViewSet(AuditTrailViewMixin, ViewPermissionsMixin, viewsets.ModelViewSet):
    """
    Provide a list of workflow templates that are available.

    ### query_params

    - _search_: search workflow name and created_by
    """
    queryset = Workflow.objects.all()
    serializer_class = WorkflowSerializer
    search_fields = ('name', 'created_by__username',)
    permission_classes = (ExtendedObjectPermissions,)
    filter_backends = (SearchFilter, DjangoFilterBackend,
                       OrderingFilter, ExtendedObjectPermissionsFilter,)

    def perform_create(self, serializer):
        serializer, permissions = self.clean_serializer_of_permissions(serializer)
        instance = serializer.save(created_by=self.request.user)
        self.assign_permissions(instance, permissions)

    @detail_route()
    def export(self, request, pk=None):
        obj = self.get_object()
        serialized_workflow = WorkflowSerializer(obj)
        # TODO: Strip user and perms data
        tasks = TaskTemplate.objects.filter(id__in=obj.order.split(','))
        serialized_tasks = TaskTemplateSerializer(tasks, many=True)
        export_data = {}
        # file templates, locations, measures, item types, equipment
        for source in [FileTemplate, AmountMeasure, ItemType]:
            results = source.objects.all()
            serializer_name = '{}Serializer'.format(source._meta.label.split('.')[-1])
            serializer_class = globals()[serializer_name]
            serialized_source = serializer_class(results, many=True)
            label_name = source._meta.label_lower.split('.')[-1]
            export_data[label_name] = serialized_source.data
        export_data['workflow'] = serialized_workflow.data
        export_data['tasks'] = serialized_tasks.data
        return Response(export_data)

    @detail_route()
    def tasks(self, request, pk=None):
        workflow = self.get_object()
        serializer = self.get_serializer(workflow)
        result = serializer.data
        tasklist = []
        tasks = self.get_object().get_tasks()
        for t in tasks:
            serializer_task = SimpleTaskTemplateSerializer(t)
            tasklist.append(serializer_task.data)
        result['tasks'] = tasklist
        return Response(result)

    @detail_route()
    def task_details(self, request, pk=None):
        """
        Get a detailed version of a specific task.

        ### query_params

        - _position_ (**required**): The task position in the workflow
        """
        workflow = self.get_object()
        position = request.query_params.get('position', None)
        if position:
            try:
                taskId = workflow.order.split(',')[int(position)]
                task = TaskTemplate.objects.get(pk=taskId)
                serializer = TaskTemplateSerializer(task)
                result = serializer.data
            except IndexError:
                return Response({'message': 'Invalid position'}, status=400)
            except ObjectDoesNotExist:
                return Response({'message': 'Task does not exist'}, status=400)
            return Response(result)
        return Response({'message': 'Please provide a task position'}, status=400)


class WorkflowImportView(ViewPermissionsMixin, APIView):
    queryset = Workflow.objects.none()
    permission_classes = (ExtendedObjectPermissions,)

    def data_to_task(self, task_data):
        # First check the task data is valid
        serialized_task = TaskTemplateSerializer(data=task_data)
        errors = {}
        serialized_fields = []
        try:
            serialized_task.is_valid(raise_exception=True)
        except:
            new_errors = self.parse_errors(serialized_task.errors, serialized_task)
            errors.update(new_errors)
        # Iterate through fields and check all are valid
        for field_type in ['Input', 'Output', 'Variable', 'Calculation', 'Step']:
            serializer_name = field_type + 'FieldImportSerializer'
            serializer_class = globals()[serializer_name]
            for field in task_data[field_type.lower() + '_fields']:
                # We need the calculations to still have their ID as
                # we can use this to swap them out later with the newly
                # created one.
                field_id = field.pop('id', None)
                field['template'] = None
                # Erase the calculation used as it doesn't exist
                # but store the value to use to lookup
                calc_id = None
                if 'calculation_used' in field:
                    calc_id = field['calculation_used']
                    field['calculation_used'] = None
                if field_type == 'Step':
                    for prop in field['properties']:
                        prop.pop('id', None)
                sf = serializer_class(data=field)
                try:
                    sf.is_valid(raise_exception=True)
                except:
                    new_errors = self.parse_errors(sf.errors, sf)
                    errors.update(new_errors)
                else:
                    # Bypass the validation as the calculation doesn't exist
                    # This ID will be swapped out later
                    if calc_id:
                        sf.validated_data['old_calculation_used'] = calc_id
                    if field_type.lower() == 'calculation':
                        sf.validated_data['id'] = field_id
                    serialized_fields.append(sf)
        return (serialized_task, serialized_fields, errors)

    def parse_errors(self, errors, serializer):
        # Iterate through dict and then error list
        # Extract value of object
        # Get field to lookup
        # List off type of object needed
        for field, error in errors.items():
            if 'label' in serializer.data:
                error_in = serializer.data['label']
            else:
                error_in = serializer.data['name']
            for e in error:
                if e.endswith('does not exist.'):
                    stripped_message = e.lstrip('Object with ')
                    lookup_name, s, v = stripped_message.partition('=')
                    value = serializer.data[field]
                    try:
                        model = serializer.fields[field].queryset.model
                    except:
                        model = serializer.fields[field].child_relation.queryset.model
                    model_name = model._meta.label_lower.split('.')[-1]
                    # Now get the info from the initial data
                    try:
                        if type(value) != list:
                            value = [value]
                        new_items = []
                        for v in value:
                            search_data = self.request.data['data'][model_name]
                            item_data = next((item for item in search_data
                                              if item[lookup_name] == v))
                            item_data['item_type'] = model._meta.label.split('.')[-1]
                            new_items.append(item_data)
                        errors[field] = {'error': error, 'items': new_items, 'error_in': error_in}
                    except:
                        errors[field] = {'error': error, 'error_in': error_in}
                else:
                    errors[field] = {'error': error, 'error_in': error_in}
        return errors

    def post(self, request, format=None):
        is_check = request.data.get('check', False)
        serializer = WorkflowImportSerializer(data=request.data)
        hasErrors = False
        errors = {}
        if serializer.is_valid(raise_exception=True):
            workflow_data = serializer.validated_data['data'].get('workflow', {})
            workflow_data.pop('id', None)
            workflow_data['name'] = serializer.data['name']
            workflow_data['assign_groups'] = self.request.data.get('assign_groups', None)
            workflow = WorkflowSerializer(data=workflow_data)
            try:
                workflow.is_valid(raise_exception=True)
            except:
                hasErrors = True
                new_errors = self.parse_errors(workflow.errors, workflow)
                errors.update(new_errors)
            # convert task id's in order to list
            order = workflow.validated_data['order'].split(',')
            # Store a list of tasks and their original ID
            task_mapping = {}
            for task_data in serializer.validated_data['data'].get('tasks', []):
                task_id = task_data.pop('id', None)
                task_data['assign_groups'] = self.request.data.get('assign_groups', None)
                serialized_task, serialized_fields, task_errors = self.data_to_task(task_data)
                if len(task_errors.keys()) > 0:
                    hasErrors = True
                    errors.update(task_errors)
                try:
                    serialized_task.is_valid(raise_exception=True)
                except:
                    hasErrors = True
                    new_errors = self.parse_errors(serialized_task.errors, serialized_task)
                    errors.update(new_errors)
                else:
                    if not is_check:
                        serialized_task, permissions = \
                                self.clean_serializer_of_permissions(serialized_task)
                        task_instance = serialized_task.save(created_by=self.request.user)
                        self.assign_permissions(task_instance, permissions)
                        task_mapping[task_id] = task_instance.id
                        # Handle saving + ID's of calculations
                        calcs = {}
                        for i, field in enumerate(serialized_fields):
                            if type(field) == CalculationFieldImportSerializer:
                                old_calc_id = field.validated_data.pop('id', None)
                                field.validated_data['template'] = task_instance
                                instance = field.save()
                                calcs[old_calc_id] = instance
                                serialized_fields.pop(i)
                        # And now the fields!!!
                        for field in serialized_fields:
                            # Check if there's a calculation associate first
                            if 'old_calculation_used' in field.validated_data:
                                c = calcs.get(field.validated_data['old_calculation_used'], None)
                                if c:
                                    field.validated_data['calculation_used'] = c
                                field.validated_data.pop('old_calculation_used', None)
                            field.validated_data['template'] = task_instance
                            field.save()
            if is_check:
                issues = [{'field': k, 'issues': i} for k, i in errors.items()
                          if 'items' not in i]
                needed_items = [i['items'] for i in errors.values() if 'items' in i]
                checks = {'issues': issues, 'required': needed_items}
                return Response(checks, status=200)
            if hasErrors:
                return Response(errors, status=400)
            # Replace the old ID with the new ID
            new_order = [str(task_mapping[int(n)]) for n in order]
            # Once complete replace order and save workflow
            workflow, permissions = self.clean_serializer_of_permissions(workflow)
            workflow.validated_data['order'] = ",".join(new_order)
            workflow_instance = workflow.save(created_by=self.request.user)
            self.assign_permissions(workflow_instance, permissions)
        return Response(workflow.data, status=201)


class RunFilterSet(django_filters.FilterSet):
    run_active = django_filters.BooleanFilter(name='date_finished', lookup_expr='isnull')

    class Meta:
        model = Run
        fields = {
            'id': ['exact'],
            'is_active': ['exact'],
            'task_in_progress': ['exact'],
            'has_started': ['exact'],
        }


class RunViewSet(AuditTrailViewMixin, ViewPermissionsMixin, StatsViewMixin, viewsets.ModelViewSet):
    """
    List all runs, active only be default
    """
    queryset = Run.objects.all()
    serializer_class = RunSerializer
    permission_classes = (ExtendedObjectPermissions,)
    filter_backends = (SearchFilter, DjangoFilterBackend,
                       OrderingFilter, ExtendedObjectPermissionsFilter,)
    # filter_fields = ('is_active', 'task_in_progress', 'has_started', 'date_finished')
    filter_class = RunFilterSet

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_serializer_class(self):
        """
        Provide a more detailed serializer when not a list
        """
        if self.action == 'retrieve':
            return DetailedRunSerializer
        return self.serializer_class

    def perform_create(self, serializer):
        # TODO:
        # Check tasks permissions valid
        # Check product permissions valid
        serializer, permissions = self.clean_serializer_of_permissions(serializer)
        instance = serializer.save(started_by=self.request.user)
        self.assign_permissions(instance, permissions)

    def _get_product_input_items(self, input_type):
        """
        Get input items from products in the run
        """
        run = self.get_object()
        excludes = []
        if run.exclude:
            excludes = [v for v in run.exclude.split(',') if v != '']
        task_input_items = {}
        for p in run.products.all():
            if input_type:
                input_type_mdl = ItemType.objects.get(name=input_type)
                # Get all decendents of the item type
                with_children = input_type_mdl.get_descendants(include_self=True)
                # Get list of names of types
                itn = [t.name for t in with_children]
                items_picked = p.linked_inventory.filter(item_type__name__in=itn) \
                    .exclude(id__in=excludes)
                task_input_items[p] = list(items_picked)
            else:
                task_input_items[p] = []
        return task_input_items

    def _generate_data_dict(self, input_items, task_data):
        """
        Generate data items from supplied task data

        One data item to each product to be produced.
        """
        # Link (product,item) -> serialized_data
        data_items = {}
        for product, items in input_items.items():
            key = product.product_identifier
            data_items[key] = copy.deepcopy(task_data.validated_data)
            # Now add the input items to the dict
            # Get data from task to put basic together
            data_items[key]['product_inputs'] = {}
            for itm in items:
                itm_data = {
                    'amount': task_data.validated_data['product_input_amount'],
                    'measure': task_data.validated_data['product_input_measure'],
                    'barcode': '',
                    'coordinates': '',
                }
                data_items[key]['product_inputs'][itm.id] = itm_data
        return data_items

    def _replace_fields(self, values):
        """
        Replace field names with their correct values
        """
        def match_value(match):
            mtch = match.group(1)
            if mtch in values:
                return str(values[mtch])
            return str(0)
        return match_value

    def _calculate_value(self, calculation, values):
        """
        Parse and perform a calculation using a dict of fields

        Using either a dict of values to field names

        Returns a NaN if the calculation cannot be performed, e.g.
        incorrect field names.
        """
        nsp = NumericStringParser()
        field_regex = r'\{(.+?)\}'
        interpolated_calculation = re.sub(field_regex, self._replace_fields(values), calculation)
        try:
            result = nsp.eval(interpolated_calculation)
        except ParseException:
            return None
        return result

    def _flatten_values(self, rep):
        """
        Take a dict of task data and reduce to field label: value
        """
        flat_values = {}
        for field_type in ['input_fields', 'step_fields', 'variable_fields', 'output_fields']:
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

    def _perform_calculations(self, task_data):
        """
        Alter fields based on calculations.
        """
        # Operate on each product entry and values for the fields
        for pid, product_data in task_data.items():
            # First, index any calculations to refer to later
            calculations = {c['id']: c for c in product_data['calculation_fields']}
            # Flatten data to a dict
            to_values = self._flatten_values(product_data)
            # Look through each field for calculations
            for field_type in ['input_fields', 'step_fields', 'variable_fields', 'output_fields']:
                if field_type in product_data:
                    for field in product_data[field_type]:
                        if 'calculation_used' in field and field['calculation_used'] is not None:
                            # Look up calculations from list
                            calc = calculations[field['calculation_used']]['calculation']
                            # Return the calculated value
                            result = self._calculate_value(calc, to_values)
                            field['amount'] = result
        return task_data

    def _update_data_items_from_file(self, file_data, data_items):
        """
        Process input file data in update data dict with new values
        """
        # TODO: Process file to allow product/item updates easily
        for f in file_data:
            try:
                ft = FileTemplate.objects.get(name=f.name)
            except:
                pass
            else:
                parsed_file = ft.read(TextIOWrapper(f.file, encoding=f.charset))
                if parsed_file:
                    for key, row in parsed_file.items():
                        data_items[key].update(row)
                else:
                    message = {
                        'message':
                            'Input file "{}" has incorrect headers/format'.format(f.name)}
                    raise ValidationError(message)
        return data_items

    def _as_measured_value(self, amount, measure):
        """
        Convert if possible to a value with units
        """
        if type(amount) is not float:
            amount = float(amount)
        try:
            value = amount * self.ureg(measure)
        except UndefinedUnitError:
            value = amount * self.ureg.count
        return value

    def _get_from_inventory(self, identifier):
        """
        Get an item from the inventory based on identifier
        """
        try:
            item = Item.objects.get(id=identifier)
        except Item.DoesNotExist:
            message = {'message': 'Item {} does not exist !'.format(identifier)}
            raise serializers.ValidationError(message)
        return item

    def _update_amounts(self, item, amount, store, field):
        """
        Referenced update of an amount indexed by identifier
        """
        if item not in store:
            store[item] = {'amount': amount,
                           'barcode': field.get('destination_barcode', None),
                           'coordinates': field.get('destination_coordinates', None)}
        else:
            store[item]['amount'] += amount

    def _update_item_amounts(self, field, key, data_item_amounts, sum_item_amounts):
        """
        Referenced update of item amounts + sum item amounts
        """
        amount = self._as_measured_value(field['amount'], field['measure'])
        item = self._get_from_inventory(field['inventory_identifier'])
        data_item_amounts[key][item] = amount
        self._update_amounts(item, amount, sum_item_amounts, field)

    def _get_item_amounts(self, data_items, task_data):
        """
        Get the per-product and total sum of items needed for task
        """
        sum_item_amounts = {}
        data_item_amounts = {}

        # Get labware amounts
        if task_data.validated_data.get('labware_not_required', False) is not True:
            labware_identifier = task_data.validated_data['labware_identifier']
            labware_item = self._get_from_inventory(labware_identifier)
            labware_required = task_data.validated_data['labware_amount']
            labware_barcode = task_data.validated_data.get('labware_barcode', None)
            labware_symbol = None
            if labware_item.amount_measure is not None:
                labware_symbol = labware_item.amount_measure.symbol
            sum_item_amounts[labware_item] = {
                    'amount': self._as_measured_value(labware_required, labware_symbol),
                    'barcode': labware_barcode,
            }

        # Get task input field amounts
        for key, item in data_items.items():
            data_item_amounts[key] = {}
            for field in item['input_fields']:
                if field['auto_find_in_inventory']:
                    identifier = '{}/{}'.format(key, field['label'])
                    try:
                        lookup_item = Item.objects.filter(properties__name='task_input',
                                                          properties__value=identifier)[0]
                    except:
                        raise serializers.ValidationError({'message':
                                                          'Item does not exist!'})
                    else:
                        field['inventory_identifier'] = lookup_item.id
                self._update_item_amounts(field, key, data_item_amounts, sum_item_amounts)

            for identifier, field in item['product_inputs'].items():
                field['inventory_identifier'] = identifier
                self._update_item_amounts(field, key, data_item_amounts, sum_item_amounts)
        return (data_item_amounts, sum_item_amounts)

    def _check_input_amounts(self, sum_item_amounts):
        """
        Check there is enough for each item available
        """
        errors = []
        error_items = []
        valid_amounts = True
        for item, required in sum_item_amounts.items():
            available = self._as_measured_value(item.amount_available,
                                                item.amount_measure.symbol)
            # Lookup transfers to see if one make sense for this
            # Only if a barcode is supplied, as this inidcates a plate may already exist
            if required.get('barcode', None):
                try:
                    transfer = ItemTransfer.objects.get(item=item,
                                                        barcode=required.get('barcode', None),
                                                        coordinates=required.get('coordinates',
                                                                                 None))
                    available = self._as_measured_value(transfer.amount_taken,
                                                        transfer.amount_measure.symbol)
                except ItemTransfer.DoesNotExist:
                    pass
            if available < required['amount']:
                missing = (available - required['amount']) * -1
                # Needs changing to reflext identifier is no longer a required field
                # as may just be name/barcode now
                message = 'Inventory item {0} ({1}) is short of amount by {2:.2f}'.format(
                    item.identifier, item.name, missing)
                errors.append(message)
                error_items.append(item)
                valid_amounts = False
        return (valid_amounts, errors, error_items)

    def _create_item_transfers(self, sum_item_amounts, error_items=[]):
        """
        Create ItemTransfers to alter inventory amounts
        """
        transfers = []
        for item, amount in sum_item_amounts.items():
            try:
                amount_symbol = '{:~}'.format(amount['amount']).split(' ')[1]
                measure = AmountMeasure.objects.get(symbol=amount_symbol)
                amount['amount'] = amount['amount'].magnitude
            except:
                measure = AmountMeasure.objects.get(symbol='item')

            # Look up to see if there is a matching ItemTransfer already and then
            # use this instead
            try:
                transfer = ItemTransfer.objects.get(item=item,
                                                    barcode=amount.get('barcode', None),
                                                    coordinates=amount.get('coordinates', None))
                # At this point need to subtract amount from available in existing
                # transfer! Need to mark in some way not completed though so can put
                # back if the trasnfer is cancelled
                transfer.amount_to_take = amount['amount']
            except (ObjectDoesNotExist, MultipleObjectsReturned):
                transfer = ItemTransfer(
                    item=item,
                    barcode=amount.get('barcode', None),
                    coordinates=amount.get('coordinates', None),
                    amount_taken=amount['amount'],
                    amount_measure=measure)
            if item in error_items:
                transfer.is_valid = False
            transfers.append(transfer)
        return transfers

    def _serialize_item_amounts(self, dict_of_amounts):
        output = []
        for item, amount in dict_of_amounts.items():
            output.append({
                'name': item.name,
                'identifier': item.id,
                'amount': amount.magnitude,
                'measure': '{:~}'.format(amount).split(' ')[1],
            })
        return output

    def _do_driver_actions(self, task_data):
        pass

    # Do not accept JSON as cannot send files this way
    @detail_route(methods=['POST'], parser_classes=(FormParser, MultiPartParser,))
    def start_task(self, request, pk=None):
        """
        Check input values and start or preview a task

        Takes in task data, any files and calculates if the data
        is valid to run the task. Pass is_check to check but not
        run the task.
        """
        # Get task data from request as may have been edited to
        # suit current situation.
        task_data = json.loads(self.request.data.get('task', '{}'))
        if task_data.get('product_input_not_required', False):
            serialized_task = TaskValuesNoProductInputSerializer(data=task_data)
        else:
            serialized_task = TaskValuesSerializer(data=task_data)

        # Get a list of input file data to be parsed
        file_data = self.request.data.getlist('input_files', [])

        # Perform checks on the validity of the data before the
        # task is run, return inventory requirements.
        is_check = request.query_params.get('is_check', False)
        # Is this a repeat of a failed task
        # is_repeat = request.query_params.get('is_repeat', False)

        if serialized_task.is_valid(raise_exception=True):
            # Init a unit registry for later use
            self.ureg = UnitRegistry()

            run = self.get_object()
            task = run.get_task_at_index(run.current_task)

            # Get items from products
            product_type = serialized_task.validated_data.get('product_input', None)
            product_input_items = self._get_product_input_items(product_type)

            # Process task data against input_items
            data_items = self._generate_data_dict(product_input_items,
                                                  serialized_task)
            # Process input files against task data
            data_items = self._update_data_items_from_file(file_data,
                                                           data_items)
            # Perform calculations here!
            data_items = self._perform_calculations(data_items)

            product_item_amounts, sum_item_amounts = self._get_item_amounts(data_items,
                                                                            serialized_task)
            valid_amounts, errors, error_items = self._check_input_amounts(sum_item_amounts)

            # Check if a transfer already exists with given barcode/well??
            transfers = self._create_item_transfers(sum_item_amounts, error_items)

            # Check if you can actually use the equipment
            if task.capable_equipment.count() > 0:
                equipment_name = serialized_task.validated_data['equipment_choice']
                try:
                    equipment = Equipment.objects.get(name=equipment_name)
                except Equipment.DoesNotExist:
                    raise serializers.ValidationError({'message':
                                                       'Equipment does not exist!'})
                else:
                    equipment_status = equipment.status
            else:
                equipment_status = 'idle'

            if is_check:
                check_output = {
                    'equipment_status': equipment_status,
                    'errors': errors,
                    'requirements': []
                }
                for t in transfers:
                    st = ItemTransferPreviewSerializer(t)
                    check_output['requirements'].append(st.data)
                return Response(check_output)
            else:
                if task.capable_equipment.count() > 0:
                    if equipment.status != 'idle':
                        raise serializers.ValidationError({'message':
                                                          'Equipment is currently in use'})
                    equipment.status = 'active'
                    equipment.save()
                    run.equipment_used = equipment

                if not valid_amounts:
                    raise ValidationError({'message': '\n'.join(errors)})
                task_run_identifier = uuid.uuid4()
                # driver_output = self._do_driver_actions(data_items)
                # Generate DataItem for inputs
                for product in run.products.all():
                    prod_amounts = product_item_amounts[product.product_identifier]
                    data_items[product.product_identifier]['product_input_amounts'] = \
                        self._serialize_item_amounts(prod_amounts)
                    entry = DataEntry(
                        run=run,
                        task_run_identifier=task_run_identifier,
                        product=product,
                        created_by=self.request.user,
                        state='active',
                        data=data_items[product.product_identifier],
                        task=task)
                    entry.save()

                # TODO: RunLabware creation
                # Link labeware barcode -> transfer
                # At this point transfers have the amount taken but are not complete
                # until task finished
                for t in transfers:
                    t.run_identifier = task_run_identifier
                    t.do_transfer(self.ureg)
                    t.save()
                    run.transfers.add(t)

                # Update run with new details
                run.task_in_progress = True
                run.has_started = True
                run.task_run_identifier = task_run_identifier
                run.save()
                return Response({'message': 'Task started successfully'})

    @detail_route(methods=["POST"])
    def cancel_task(self, request, pk=None):
        """
        Cancel a running task that has accidentally been started
        """
        run = self.get_object()

        if run.task_in_progress:
            ureg = UnitRegistry()
            # Get any transfers for this task
            transfers_for_this_task = run.transfers.filter(run_identifier=run.task_run_identifier)
            data_entries = DataEntry.objects.filter(task_run_identifier=run.task_run_identifier)

            if run.equipment_used:
                equipment = run.equipment_used
                equipment.status = 'idle'
                equipment.save()
                run.equipment_used = None

            # Transfer all the things taken back into the inventory
            for t in transfers_for_this_task:
                t.is_addition = True
                t.do_transfer(ureg)
            # Once transfers made delete them
            # TODO: DO NOT delete transfers marked as has_taken!!
            transfers_for_this_task.delete()
            # Trash the data entries now as they're irrelevant
            data_entries.delete()
            # No longer active
            run.task_in_progress = False
            run.has_started = False
            run.save()
            return Response({'message': 'Task cancelled'})
        return Response({'message': 'Task not in progress so cannot be cancelled'}, status=400)

    @detail_route(methods=["POST"])
    def recalculate(self, request, pk=None):
        """
        Given task data recalculate and return task.
        """
        obj = self.get_object()
        task_data = request.data
        if task_data:
            serializer = RecalculateTaskTemplateSerializer(data=task_data)
            if serializer.is_valid(raise_exception=True):
                return Response(serializer.data)  # Raw data, not objects
        serializer = TaskTemplateSerializer(obj)
        if serializer.is_valid(raise_exception=True):
            return Response(serializer.data)  # Raw data, not objects

    @detail_route()
    def monitor_task(self, request, pk=None):
        """
        Check up on a running task
        """
        run = self.get_object()

        if run.task_in_progress and run.is_active:
            task = run.get_task_at_index(run.current_task)
            transfers = run.transfers.filter(run_identifier=run.task_run_identifier)
            serialized_transfers = ItemTransferPreviewSerializer(transfers, many=True)
            # Get current data for each product
            data_entries = DataEntry.objects.filter(task_run_identifier=run.task_run_identifier)
            serialized_data_entries = DataEntrySerializer(data_entries, many=True)
            # Get driver files
            # It will a file template for now
            # But ultimetly a driver will step in and do some processing
            # Will need UI/task stuff for that
            equipment_files = []
            for ft in task.equipment_files.all():
                equipment_files.append({
                    'name': ft.name,
                    'id': ft.id,
                })
            output_data = {
                'tasks': run.tasks,
                'current_task': run.current_task,
                'transfers': serialized_transfers.data,
                'data': serialized_data_entries.data,
                'equipment_files': equipment_files,
            }
            # What stage is the task at? Talk to driver/equipment
            return Response(output_data)
        # Return a 204 as there is no task to monitor
        return Response(status=204)

    @detail_route(methods=['GET'], renderer_classes=(CSVRenderer,))
    def get_file(self, request, pk=None):
        file_id = request.query_params.get('file_id', None)

        run = self.get_object()
        task = run.get_task_at_index(run.current_task)

        try:
            file_template = task.equipment_files.get(pk=file_id)
        except ObjectDoesNotExist:
            raise ValidationError({'message': 'Template does not exist'})

        if run.task_in_progress and run.is_active:
            transfers = run.transfers.filter(run_identifier=run.task_run_identifier)
            serialized_transfers = ItemTransferPreviewSerializer(transfers, many=True)
            data_entries = DataEntry.objects.filter(task_run_identifier=run.task_run_identifier)
            serialized_data_entries = DataEntrySerializer(data_entries, many=True)
            output_data = task.data_to_output_file(file_template,
                                                   serialized_data_entries.data,
                                                   serialized_transfers.data)
            return Response(output_data)
        # Return a 204 as there is no task to get files for
        return Response(status=204)

    def _copy_files(self, data_entries):
        task = self.get_object().get_task_at_index(self.get_object().current_task)
        # If no choice default to the first entry in the equipment
        # for use on
        equipment_choice = data_entries[0].data.get('equipment_choice', None)
        try:
            equipment = task.capable_equipment.get(name=equipment_choice)
        except:
            # Well we can't do anything so just return
            return
        for file_to_copy in equipment.files_to_copy.filter(is_enabled=True):
            interpolate_dict = {
                'run_identifier': str(data_entries[0].task_run_identifier),
            }
            for loc in file_to_copy.locations.all():
                file_store = loc.copy(interpolate_dict)
                if file_store:
                    for d in data_entries:
                        d.data_files.add(file_store)
                        d.save()

    @detail_route(methods=['POST'])
    def finish_task(self, request, pk=None):
        """
        Finish a running task, completing run if required
        """
        # If it is manually, rather than a system, finish to the task
        # is_manual_finish = request.query_params.get('manual', False)
        # A comma seperated list of product ID's that failed the task
        product_failures = request.data.get('failures', None)
        notes = request.data.get('notes', None)
        restart_task_at = request.data.get('restart_task_at', None)

        run = self.get_object()

        if run.task_in_progress and run.is_active:

            # Now the task is complete any transfers can be marked as complete
            transfers = run.transfers.filter(run_identifier=run.task_run_identifier)
            for t in transfers:
                # We've finished so you can't put it back now
                # At this point it may or may not have everthing taken
                t.do_complete()

            all_entries = DataEntry.objects.filter(
                task_run_identifier=run.task_run_identifier,
                product__in=run.products.all())

            # Handle filepath copy stuff
            self._copy_files(all_entries)

            failed_products = []
            if product_failures:
                # If failures create new run based on current
                # and move failed products to it
                failure_ids = str(product_failures).split(',')
                failed_products = run.products.filter(id__in=failure_ids)
                if failed_products.count() != len(failed_products):
                    return Response({'message': 'Invalid Id\'s for failed products!'}, status=400)
                new_name = '{} (failed)'.format(run.name)

                # Set the task to a different task if needs to be earlier
                # Tasks are zero indexed but labelled as 1 indexed so subtract 1
                if restart_task_at is not None:
                    set_task_as = int(restart_task_at)
                else:
                    set_task_as = run.current_task

                new_run = Run(
                    name=new_name,
                    tasks=run.tasks,
                    current_task=set_task_as,
                    has_started=True,
                    started_by=request.user)
                new_run.save()
                new_run.products.add(*failed_products)

                # Update data entries state to failed
                # This variable exists for line length purposes :P
                rtri = run.task_run_identifier
                failed_entries = DataEntry.objects.filter(task_run_identifier=rtri,
                                                          product__in=failed_products)
                failed_entries.update(state='failed', notes=notes)

                # Remove the failed products from the current run
                run.products.remove(*failed_products)

            # Exclude failed products
            entries = all_entries.exclude(product__in=failed_products)

            # find and mark dataentry complete!
            entries.update(state='succeeded')

            # mark labware inactive
            active_labware = run.labware.filter(is_active=True)
            active_labware.update(is_active=False)

            # Create ouputs from the task
            runindex = 0
            for e in entries:
                for index, output in enumerate(e.data['output_fields']):
                    output_name = '{} {} {}'.format(e.product.product_identifier,
                                                    e.product.name,
                                                    output['label'])
                    measure = AmountMeasure.objects.get(symbol=output['measure'])
                    identifier = '{}/{}/{}'.format(e.product.product_identifier,
                                                   e.run.id, e.run.name)
                    runindex += 1
                    location = Location.objects.get(name='Lab')
                    item_type = ItemType.objects.get(name=output['lookup_type'])
                    new_item = Item(
                        name=output_name,
                        identifier=identifier,
                        item_type=item_type,
                        location=location,
                        amount_available=output['amount'],
                        amount_measure=measure,
                        added_by=request.user,
                    )
                    new_item.save()
                    # Get permissions from project for item
                    self.clone_group_permissions(e.product.project, new_item)

                    product_input_ids = [p for p in e.data['product_inputs']]
                    product_items = Item.objects.filter(id__in=product_input_ids)
                    new_item.created_from.add(*product_items)

                    e.product.linked_inventory.add(new_item)
                    e.save()

            run.task_in_progress = False
            if run.equipment_used:
                equipment = run.equipment_used
                equipment.status = 'idle'
                equipment.save()
                run.equipment_used = None

            # advance task by one OR end if no more tasks
            if run.current_task == len(run.get_task_list()) - 1:
                run.is_active = False
                run.date_finished = timezone.now()
            else:
                run.current_task += 1

            run.save()
            serializer = RunSerializer(run)
            return Response(serializer.data)
        # Return a 204 as there is no task to monitor
        return Response(status=204)

    @detail_route(methods=['POST'])
    def workflow_from_run(self, request, pk=None):
        """
        Take a current run and create a workflow from tasks
        """
        new_name = request.query_params.get('name', None)
        if new_name:
            run = self.get_object()
            new_workflow = Workflow(
                name=new_name,
                order=run.tasks,
                created_by=request.user)
            new_workflow.save()
            location = reverse('workflows-detail', args=[new_workflow.id])
            return Response(headers={'location': location}, status=303)
        else:
            return Response({'message': 'Please supply a name'}, status=400)


class TaskFilterSet(django_filters.FilterSet):
    """
    Filter for the TaskViewSet
    """
    id__in = ListFilter(name='id')

    class Meta:
        model = TaskTemplate
        fields = {
            'id': ['exact', 'in'],
            'name': ['exact'],
            'created_by__username': ['exact'],
        }


class TaskViewSet(AuditTrailViewMixin, ViewPermissionsMixin, viewsets.ModelViewSet):
    """
    Provide a list of TaskTemplates available
    """
    queryset = TaskTemplate.objects.all()
    serializer_class = TaskTemplateSerializer
    permission_classes = (ExtendedObjectPermissions,)
    filter_backends = (SearchFilter, DjangoFilterBackend,
                       OrderingFilter, ExtendedObjectPermissionsFilter,)
    search_fields = ('name', 'created_by__username',)
    filter_class = TaskFilterSet

    def get_serializer_class(self):
        if self.request.data.get('product_input_not_required', False):
            return TaskTemplateNoProductInputSerializer
        return TaskTemplateSerializer

    def perform_create(self, serializer):
        serializer, permissions = self.clean_serializer_of_permissions(serializer)
        instance = serializer.save(created_by=self.request.user)
        self.assign_permissions(instance, permissions)

    @detail_route(methods=["POST"])
    def recalculate(self, request, pk=None):
        """
        Given task data recalculate and return task.
        """
        obj = self.get_object()
        task_data = request.data
        if task_data:
            serializer = RecalculateTaskTemplateSerializer(data=task_data)
            if serializer.is_valid(raise_exception=True):
                return Response(serializer.data)  # Raw data, not objects
        serializer = TaskTemplateSerializer(obj)
        if serializer.is_valid(raise_exception=True):
            return Response(serializer.data)  # Raw data, not objects


class TaskFieldViewSet(AuditTrailViewMixin, ViewPermissionsMixin, viewsets.ModelViewSet):
    """
    Provides a list of all task fields
    """
    ordering_fields = ('name',)
    permission_classes = (ExtendedObjectPermissions,)
    filter_backends = (SearchFilter, DjangoFilterBackend,
                       OrderingFilter, ExtendedObjectPermissionsFilter,)

    def get_serializer_class(self):
        try:
            type_name = self.request.query_params.get('type', '').title()
            if type_name:
                serializer_name = type_name + 'FieldTemplateSerializer'
                serializer_class = globals()[serializer_name]
                return serializer_class
        except:
            pass
        return InputFieldTemplateSerializer

    def get_queryset(self):
        """
        Pick the type of field so it can be properly serialized.
        """
        type_name = self.request.query_params.get('type', '').title()
        if type_name:
            object_name = type_name + 'FieldTemplate'
            object_class = globals()[object_name]
            return object_class.objects.all()
        return InputFieldTemplate.objects.all()

    def perform_create(self, serializer):
        task_template = serializer.validated_data['template']
        if ('view_tasktemplate' in get_group_perms(self.request.user, task_template)
                or self.request.user.groups.filter(name='admin').exists()):
            if ('change_tasktemplate' in get_group_perms(self.request.user, task_template)
                    or self.request.user.groups.filter(name='admin').exists()):
                instance = serializer.save()
                self.clone_group_permissions(instance.template, instance)
            else:
                raise PermissionDenied('You do not have permission to create this')
        else:
            raise NotFound()
