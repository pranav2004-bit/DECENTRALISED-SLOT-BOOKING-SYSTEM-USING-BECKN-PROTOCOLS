from django.contrib import admin

from .models import BusinessAccount, OnboardingStatus, SiteVerification


@admin.register(BusinessAccount)
class BusinessAccountAdmin(admin.ModelAdmin):
    """`is_active` here IS Provider Lifecycle Management (livetracker2.md §2.2) — the
    real deactivation mechanism, not a placeholder."""

    list_display = ("contact", "business_name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("contact", "business_name")
    readonly_fields = ("id", "created_at", "updated_at", "last_login")
    exclude = ("password",)


@admin.register(OnboardingStatus)
class OnboardingStatusAdmin(admin.ModelAdmin):
    list_display = ("domain", "status", "approved_for_subscribe", "updated_at")
    list_filter = ("status", "approved_for_subscribe")
    search_fields = ("domain",)


@admin.register(SiteVerification)
class SiteVerificationAdmin(admin.ModelAdmin):
    list_display = ("request_id", "updated_at")
