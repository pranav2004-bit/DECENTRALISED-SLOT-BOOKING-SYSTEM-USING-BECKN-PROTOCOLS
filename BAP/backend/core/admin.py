from django.contrib import admin

from .models import Customer, OnboardingStatus, SiteVerification


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    """`is_active` here IS Buyer Lifecycle Management (livetracker2.md §2.1) — toggling it
    in admin is the real, deliberate deactivation mechanism, not a placeholder."""

    list_display = ("contact", "name", "is_active", "notify_by_email", "created_at")
    list_filter = ("is_active", "notify_by_email")
    search_fields = ("contact", "name")
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
