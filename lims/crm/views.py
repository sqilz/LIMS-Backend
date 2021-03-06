import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist

from rest_framework.views import APIView
from rest_framework.response import Response

from simple_salesforce import Salesforce
from django_countries import countries

from lims.users.serializers import UserSerializer
from lims.pricebook.models import Price, PriceBook
from lims.projects.models import Project, ProjectStatus
from lims.permissions.permissions import ExtendedObjectPermissions
from .models import CRMAccount, CRMProject, CRMQuote


class CRMUserView(APIView):
    """
    Deals with the creation and upkeep of SalesForce account and
    contact information.
    """
    permission_classes = (ExtendedObjectPermissions,)
    queryset = CRMAccount.objects.none()

    def post(self, request, format=None):
        """
        Adds the necessary account and contact data to Salesforce if they do not exist.
        """

        if settings.ENABLE_CRM:
            sf = Salesforce(instance_url=settings.SALESFORCE_URL,
                            username=settings.SALESFORCE_USERNAME,
                            password=settings.SALESFORCE_PASSWORD,
                            security_token=settings.SALESFORCE_TOKEN)

            contact_id = ''
            account_id = ''

            add_only = request.data.get('add_only', False)

            contacts_query = ("SELECT Id,AccountId,Account.name,FirstName,LastName,Email "
                              "FROM Contact WHERE Email = '{}'").format(request.data['email'])
            contacts = sf.query(contacts_query)
            if contacts['totalSize'] > 0:
                contact_id = contacts['records'][0]['Id']
                account_id = contacts['records'][0]['AccountId']
                account_name = contacts['records'][0]['Account']['Name']
            elif add_only is False:
                account_query = "SELECT Id,Name FROM Account WHERE Name = '{}'".format(
                    request.data['institution_name'])
                accounts = sf.query(account_query)
                if accounts['totalSize'] == 0:
                    result = sf.Account.create({'Name': request.data['institution_name']})
                    account_id = result['id']
                else:
                    account_id = accounts['records'][0]['Id']

                country_codes = dict((value, key) for key, value in dict(countries).items())

                contact = sf.Contact.create({
                    'FirstName': request.data['first_name'],
                    'LastName': request.data['last_name'],
                    'AccountId': account_id,
                    'Email': request.data['email'],
                    'MailingStreet':
                        request.data['address_1'] + '\n ' + request.data.get('address_2', ''),
                    'MailingCity': request.data['city'],
                    'MailingPostalCode': request.data['postcode'],
                    'MailingCountryCode': country_codes[request.data['country']],
                })
                contact_id = contact['id']

            if contact_id and account_id:
                user = User.objects.get(email=request.data['email'])

                details = CRMAccount(contact_identifier=contact_id,
                                     account_identifier=account_id,
                                     account_name=account_name,
                                     user=user)
                details.save()

                user = User.objects.get(email=request.data['email'])

                s = UserSerializer(user)
                return Response(s.data)
            return Response({'No CRM data added to account'}, status=204)
        return Response({'message': 'CRM is currently disabled'}, status=501)

    def delete(self, request, format=None):
        if settings.ENABLE_CRM:
            email = request.query_params.get('email', None)
            if email:
                try:
                    user = User.objects.get(email=email)
                    crm_account = CRMAccount.objects.get(user=user)
                except:
                    return Response({'message': 'User or account not found'}, status=404)
                else:
                    crm_account.delete()
                    return Response({'message': 'CRM account removed'})
            return Response({'message': 'Please provide an email address'}, status=400)
        return Response({'message': 'CRM is currently disabled'}, status=501)


class CRMProjectView(APIView):
    """
    Fetch project information from CRM and create CRM objects
    """
    permission_classes = (ExtendedObjectPermissions,)
    queryset = CRMProject.objects.none()

    def get(self, request, format=None):
        """
        Lists all projects on Salesforce.
        """

        if settings.ENABLE_CRM:
            search = request.query_params.get('search', '')
            pk = request.query_params.get('id', None)
            sf = Salesforce(instance_url=settings.SALESFORCE_URL,
                            username=settings.SALESFORCE_USERNAME,
                            password=settings.SALESFORCE_PASSWORD,
                            security_token=settings.SALESFORCE_TOKEN)

            if pk is not None:
                projects_query = ("SELECT Id,Name,Description,Project_Status__c, CreatedDate "
                                  "FROM Opportunity WHERE Id = '{}'").format(pk)
            else:
                projects_query = ("SELECT Id,Name,Description,Project_Status__c, CreatedDate "
                                  "FROM Opportunity WHERE Name LIKE '%{}%'").format(search)
            projects = sf.query(projects_query)
            return Response({'results': projects['records'],
                             'meta': {'count': len(projects['records'])}})
        return Response({'message': 'CRM is currently disabled'}, status=501)

    def post(self, request, format=None):
        """
        Adds a project to Salesforce and creates references on system.
        """
        if settings.ENABLE_CRM:
            sf = Salesforce(instance_url=settings.SALESFORCE_URL,
                            username=settings.SALESFORCE_USERNAME,
                            password=settings.SALESFORCE_PASSWORD,
                            security_token=settings.SALESFORCE_TOKEN)

            stage = 'Proposal/Price Quote'
            now = datetime.date.today()
            close_date = now + datetime.timedelta(days=30)

            name = request.data['name']
            account = request.data['account_id']

            Price.objects.all()
            prices = {item.code: {'id': item.identifier, 'price': item.price}
                      for item in Price.objects.all()}

            pricebook_name = request.data['services'][0]['pricebook']
            pricebook = PriceBook.objects.get(name=pricebook_name)

            crm_project_data = sf.Opportunity.create({
                'Name': name,
                'AccountId': account,
                'StageName': stage,
                'CloseDate': close_date.isoformat(),
                'VAT_Exempt__c': request.data['vat_exempt'],
                'Pricebook2Id': pricebook.identifier,
            })

            crm_project = CRMProject(project_identifier=crm_project_data['id'])
            crm_project.save()

            quote_created = sf.Quote.create({
                'OpportunityId': crm_project_data['id'],
                'Name': name + ' quote',
                'Pricebook2Id': pricebook.identifier,
            })

            request.data['services'].sort(key=lambda s: s['sample'])

            for item in request.data['services']:
                item_id = prices[item['code']]['id']

                sf.QuoteLineItem.create({
                    'QuoteId': quote_created['id'],
                    'PricebookEntryId': item_id,
                    'Description': item['sample'],
                    'Quantity': item['quantity'],
                    'UnitPrice': prices[item['code']]['price']
                })

            quote_data = sf.Quote.get(quote_created['id'])

            crm_quote = CRMQuote(project=crm_project,
                                 quote_identifier=quote_data['Id'],
                                 quote_number=quote_data['QuoteNumber'],
                                 quote_name=quote_data['Name'],
                                 subtotal=quote_data['Subtotal'],
                                 discount=quote_data['Discount'],
                                 total=quote_data['TotalPrice'])
            crm_quote.save()

            return Response({'message': 'Project and quote created'})
        return Response({'message': 'CRM is currently disabled'}, status=501)


class CRMUpdateAccountView(APIView):
    """
    Update a CRM account with the latest information from the CRM itself
    """
    permission_classes = (ExtendedObjectPermissions,)
    queryset = CRMAccount.objects.none()

    def post(self, request, format=None):
        if settings.ENABLE_CRM:
            crm_account_ids = request.data.get('crm_ids', None)

            if crm_account_ids:
                accounts = CRMAccount.objects.filter(id__in=crm_account_ids)

                crm_identifiers = ["'" + p.contact_identifier + "'" for p in accounts.all()]

                sf = Salesforce(instance_url=settings.SALESFORCE_URL,
                                username=settings.SALESFORCE_USERNAME,
                                password=settings.SALESFORCE_PASSWORD,
                                security_token=settings.SALESFORCE_TOKEN)

                contacts_query = ("SELECT c.Id,c.AccountId,c.FirstName,c.LastName,c.Email,a.Name "
                                  "FROM Contact c, c.Account a WHERE c.Id IN ({})").format(
                                 ",".join(crm_identifiers))

                contacts = sf.query(contacts_query)
                if contacts['totalSize'] > 0:
                    records = contacts['records']
                    for record in records:
                        try:
                            account = CRMAccount.objects.get(contact_identifier=record['Id'])
                        except:
                            pass
                        else:
                            account.account_identifier = record['AccountId']
                            account.contact_identifier = record['Id']
                            if record['Account']:
                                account.account_name = record['Account']['Name']
                            account.save()
                    return Response({'message': 'CRM accounts updated'})
                return Response({'message': 'No accounts found on CRM system'}, status=404)
            return Response({'message': 'Please provide a list of CRM account IDs'}, status=400)
        return Response({'message': 'CRM is currently disabled'}, status=501)


class CRMUpdateProjectView(APIView):
    """
    Update CRM projects with the latest information from the database.
    """
    permission_classes = (ExtendedObjectPermissions,)
    queryset = CRMProject.objects.none()

    def post(self, request, format=None):
        if settings.ENABLE_CRM:
            crm_project_ids = request.data.get('crm_ids', None)

            if crm_project_ids:
                projects = CRMProject.objects.filter(id__in=crm_project_ids)

                crm_identifiers = ["'" + p.project_identifier + "'" for p in projects.all()]

                sf = Salesforce(instance_url=settings.SALESFORCE_URL,
                                username=settings.SALESFORCE_USERNAME,
                                password=settings.SALESFORCE_PASSWORD,
                                security_token=settings.SALESFORCE_TOKEN)
                crm_project_query = ("SELECT o.Id,o.Name,o.Description,o.CreatedDate,"
                                     "o.Project_Status__c "
                                     "FROM Opportunity o "
                                     "WHERE o.Id IN ({})").format(", ".join(crm_identifiers))
                crm_project_data = sf.query(crm_project_query)
                if crm_project_data['totalSize'] > 0:
                    records = crm_project_data['records']
                    for record in records:
                        try:
                            proj = CRMProject.objects.get(project_identifier=record['Id'])
                        except:
                            pass
                        else:
                            proj.name = record['Name']
                            proj.description = record['Description']
                            proj.status = record.get('Project_Status__c', '')
                            proj.save()
                            # Get project and update status
                            projects = proj.project_set.all()
                            for p in projects:
                                if record.get('Project_Status__c', '') != '':
                                    ps = record.get('Project_Status__c')
                                    try:
                                        project_status = ProjectStatus.objects.get(name=ps)
                                    except ObjectDoesNotExist:
                                        project_status = ProjectStatus.objects.create(name=ps)
                                    p.status = project_status
                                    p.save()
                    return Response({'message': 'Projects updated'})
                return Response({'message': 'No projects found on CRM system'}, status=404)
            return Response({'message': 'Please provide a list of CRM project IDs'}, status=400)
        return Response({'message': 'CRM is currently disabled'}, status=501)


class CRMLinkView(APIView):
    """
    Link CRM objects to another relevant object
    """
    permission_classes = (ExtendedObjectPermissions,)
    queryset = CRMProject.objects.none()

    def post(self, request, format=None):
        """
        Links a CRMProject (creating it if not exists) to Project
        """

        if settings.ENABLE_CRM:
            crm_identifier = request.data.get('identifier', None)
            project_id = request.data.get('id', None)
            record = None

            if crm_identifier and project_id:

                try:
                    crm_project = CRMProject.objects.get(project_identifier=crm_identifier)
                except ObjectDoesNotExist:
                    sf = Salesforce(instance_url=settings.SALESFORCE_URL,
                                    username=settings.SALESFORCE_USERNAME,
                                    password=settings.SALESFORCE_PASSWORD,
                                    security_token=settings.SALESFORCE_TOKEN)
                    crm_project_query = ("SELECT o.Id,o.Name,o.Description,o.CreatedDate,a.id,"
                                         "o.Project_Status__c,a.name,"
                                         "(SELECT Id,ContactId,c.name,c.email "
                                         "FROM OpportunityContactRoles cr, "
                                         "cr.Contact c WHERE IsPrimary=True) "
                                         "FROM Opportunity o, o.Account a "
                                         "WHERE o.Id = '{}'").format(crm_identifier)
                    crm_project_data = sf.query(crm_project_query)
                    if crm_project_data['totalSize'] > 0:
                        record = crm_project_data['records'][0]

                        try:
                            contact_identifier = record['OpportunityContactRoles'][
                                'records'][0]['ContactId']
                        except:
                            return Response(
                                {'message':
                                 'CRM Project does not have an associated contact role'},
                                status=400)
                        else:
                            try:
                                crm_account = CRMAccount.objects.get(
                                    user__email=record['OpportunityContactRoles'][
                                        'records'][0]['Contact']['Email'],
                                    account_identifier=record['Account']['Id'])
                            except ObjectDoesNotExist:

                                contact_name = record['OpportunityContactRoles'][
                                    'records'][0]['Contact']['Name']
                                contact_email = record['OpportunityContactRoles'][
                                    'records'][0]['Contact']['Email']

                                first_name, last_name = contact_name.rsplit(' ', 1)
                                username = first_name[0] + last_name

                                try:
                                    u = User.objects.get(email=contact_email)
                                except User.DoesNotExist:
                                    u = User.objects.create_user(
                                        username,
                                        email=contact_email
                                    )

                                    u.first_name = first_name
                                    u.last_name = last_name
                                    u.save()

                                crm_account = CRMAccount(
                                    contact_identifier=contact_identifier,
                                    account_identifier=record['Account']['Id'],
                                    account_name=record['Account']['Name'],
                                    user=u
                                )
                                crm_account.save()

                        crm_project = CRMProject(
                            project_identifier=crm_identifier,
                            name=record['Name'],
                            description=record['Description'],
                            date_created=record['CreatedDate'],
                            status=record.get('Project_Status__c', ''),
                            account=crm_account
                        )
                        crm_project.save()
                    else:
                        return Response(
                            {'message': 'Project on CRM with the identifier {} does no exist'.
                                format(crm_identifier)}, status=404)
                try:
                    project = Project.objects.get(pk=project_id)
                except ObjectDoesNotExist:
                    return Response(
                        {'message': 'Project with ID {} does not exist'.format(project_id)},
                        status=404)

                project.crm_project = crm_project

                if record and record.get('Project_Status__c', '') != '' and \
                   record.get('Project_Status__c') is not None:
                    ps = record.get('Project_Status__c')
                    try:
                        project_status = ProjectStatus.objects.get(name=ps)
                    except ObjectDoesNotExist:
                        project_status = ProjectStatus.objects.create(name=ps)
                    project.status = project_status

                project.save()
            return Response({'message': 'CRM Project linked to Project {}'.format(project_id)})
        return Response({'message': 'CRM is currently disabled'}, status=501)

    def delete(self, request, format=None):
        """
        Links a CRMProject (creating it if not exists) to Project
        """

        if settings.ENABLE_CRM:
            project_id = request.query_params.get('project_id', None)
            try:
                project = Project.objects.get(id=project_id)
            except ObjectDoesNotExist:
                return Response(
                    {'message': 'Project with ID {} does not exist'.format(project_id)},
                    status=404)
            project.crm_project = None
            project.save()
            return Response({'message': 'Project unlinked from CRM'}, status=200)
        return Response({'message': 'CRM is currently disabled'}, status=501)
