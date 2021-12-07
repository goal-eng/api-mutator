from django.contrib import admin
from src.core.models import ApiCredentials, HubstaffAccessInfo


@admin.register(ApiCredentials)
class ApiCredentialsAdmin(admin.ModelAdmin):
    list_display = 'version', 'email',
    readonly_fields = 'user',

    def version(self, instance):
        return instance.user.pk

    def email(self, instance):
        return instance.user.email


@admin.register(HubstaffAccessInfo)
class HubstaffAccessInfoAdmin(admin.ModelAdmin):
    list_display = 'created_at', 'expires_at',
    ordering = '-expires_at',
