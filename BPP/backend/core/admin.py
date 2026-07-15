from django.contrib import admin

from .models import OnboardingStatus, SiteVerification


@admin.register(OnboardingStatus)
class OnboardingStatusAdmin(admin.ModelAdmin):
    list_display = ("domain", "status", "approved_for_subscribe", "updated_at")
    list_filter = ("status", "approved_for_subscribe")
    search_fields = ("domain",)


@admin.register(SiteVerification)
class SiteVerificationAdmin(admin.ModelAdmin):
    list_display = ("request_id", "updated_at")
