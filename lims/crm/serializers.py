from rest_framework import serializers

from .models import CRMAccount, CRMProject, CRMQuote


class CreateCRMAccountSerializer(serializers.Serializer):
    email = serializers.EmailField()
    institution_name = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    address_1 = serializers.CharField()
    address_2 = serializers.CharField(allow_null=True, required=False)
    city = serializers.CharField()
    postcode = serializers.CharField()
    country = serializers.CharField()


class CRMAccountSerializer(serializers.ModelSerializer):
    account_url = serializers.CharField(read_only=True)
    contact_url = serializers.CharField(read_only=True)
    account_details = serializers.CharField(read_only=True)

    class Meta:
        model = CRMAccount
        fields = '__all__'
        depth = 1


class CRMQuoteSerializer(serializers.ModelSerializer):
    quote_url = serializers.ReadOnlyField()

    class Meta:
        model = CRMQuote
        fields = '__all__'


class CRMProjectSerializer(serializers.ModelSerializer):
    account = CRMAccountSerializer()
    quotes = CRMQuoteSerializer(many=True, read_only=True)
    project_url = serializers.CharField(read_only=True)

    class Meta:
        model = CRMProject
        fields = '__all__'
