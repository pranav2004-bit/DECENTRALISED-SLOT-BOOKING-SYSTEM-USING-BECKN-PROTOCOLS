from django.contrib import admin

from .models import BookingAuditLogEntry


@admin.register(BookingAuditLogEntry)
class BookingAuditLogEntryAdmin(admin.ModelAdmin):
    """Append-only — no add/change/delete via admin, view/search only. Mirrors
    Registry's own `AuditLogEntryAdmin` (`registry/core/admin.py`) exactly."""

    list_display = ("created_at", "booking_id_text", "event_type", "correlation_id")
    list_filter = ("event_type",)
    search_fields = ("booking_id_text", "correlation_id")
    readonly_fields = [f.name for f in BookingAuditLogEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
