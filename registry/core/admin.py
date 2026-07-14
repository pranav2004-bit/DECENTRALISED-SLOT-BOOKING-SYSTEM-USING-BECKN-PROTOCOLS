from django.contrib import admin

from .models import AuditLogEntry, Challenge, Participant


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = ("subscriber_id", "domain", "participant_type", "status", "updated_at")
    list_filter = ("status", "participant_type", "domain")
    search_fields = ("subscriber_id",)


@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    list_display = ("id", "participant", "created_at", "expires_at", "used_at")
    readonly_fields = [f.name for f in Challenge._meta.fields]

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    """Append-only — no add/change/delete via admin, view/search only."""

    list_display = ("created_at", "subscriber_id", "event_type", "correlation_id")
    list_filter = ("event_type",)
    search_fields = ("subscriber_id", "correlation_id")
    readonly_fields = [f.name for f in AuditLogEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
