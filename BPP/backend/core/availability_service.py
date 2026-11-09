"""Doctor/resource availability-by-date check — BAP's search-results UI needs to know,
per selected date, which items on a provider's catalog actually have a real open slot
*before* it renders their card (livetracker9.md). No real Beckn schema counterpart —
the protocol has no "list available dates" action, same "project-defined, not
protocol-confirmed" territory as the `Slot` model itself (inventory_core/models.py's
own docstring).

Shaped and verified exactly like the other direct BAP->BPP calls (payment_status_
service.py's own module docstring: a real registry-trust signature check, not a
bespoke REST auth scheme) — but deliberately answers in the SAME response as the ack,
not a later `on_check_availability` callback. select/init/confirm defer to a callback
because the actual business outcome can genuinely take time (contention on a hold,
provider-side processing); this is a read-only, no-side-effect query that's already
fully resolved by the time an ack would be sent, so forcing it through an async
ack-then-callback round trip would only add latency for no benefit. check_availability
isn't a real Beckn action either way, so there's no protocol-compliance rule being
bent by skipping the callback — this project's own established shape for a genuinely
synchronous direct call (see `status_view`'s synchronous DB read for the same
reasoning, just inline in the ack here instead of a second response).
"""

import uuid
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from beckn_transaction import (
    PayloadValidationError,
    build_ack_response,
    build_nack_response,
    validate_context,
)
from django.utils import timezone
from inventory_core.models import Slot

from . import trust
from .catalog import visible_resources
from .metrics import record_availability_check_rejected, record_availability_check_succeeded

# India-only platform (ALL_INDIA_STATES is this project's entire location model) —
# BPP/backend/bpp/settings.py's own TIME_ZONE is UTC for storage, but "today"/"this
# date" for a hospital and its customers means the IST calendar day, not UTC's.
INDIA_TZ = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# Defense-in-depth ceilings, not derived from real production data (none exists
# yet) — generous enough for any real browse session (BAP's own date strip only
# ever shows 15 days and one provider's doctors at a time, both far under these).
MAX_RESOURCE_IDS = 50
MAX_DAYS_AHEAD = 60


class AvailabilityCheckError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _parse_date(raw) -> date_cls:
    if not isinstance(raw, str):
        raise AvailabilityCheckError("date is required")
    try:
        return date_cls.fromisoformat(raw)
    except ValueError as exc:
        raise AvailabilityCheckError(f"Invalid date {raw!r} — expected YYYY-MM-DD") from exc


def _day_bounds_utc(day: date_cls) -> tuple[datetime, datetime]:
    """The `[start, end)` of `day` as an IST calendar day, converted to UTC — matching
    how `Slot.start_time`/`end_time` are actually stored (`USE_TZ=True`, UTC)."""
    start_local = datetime.combine(day, time.min, tzinfo=INDIA_TZ)
    end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=INDIA_TZ)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def check_availability(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Validates + verifies the calling BAP, then answers, per `resource_id` in the
    request, whether it has at least one genuinely bookable slot on `date` — `status
    == AVAILABLE`, real remaining capacity, not already ended, and belonging to a
    resource whose business is currently active and accepting bookings in this
    domain. Every requested `resource_id` gets an entry in the response, including
    ones that turned out invalid, outside this domain, or belonging to a paused
    business — all uniformly `False`, never omitted (a predictable response shape
    for the caller) and never distinguished from a genuine "no slots today" (a
    uniform "no" leaks nothing about *why*, and a malformed id in an otherwise-valid
    batch must never fail the whole request)."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        record_availability_check_rejected()
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "AVAILABILITY_ERROR", "message": f"Invalid context: {exc}"},
            ),
            400,
        )

    try:
        trust.verify_bap_and_gateway(
            context=context,
            authorization_header=authorization_header,
            gateway_authorization_header=gateway_authorization_header,
            body=body,
            require_gateway=False,
        )
    except trust.TrustEstablishmentError as exc:
        record_availability_check_rejected()
        return (
            build_nack_response(
                context=context, error={"code": "AVAILABILITY_ERROR", "message": str(exc)}
            ),
            401,
        )

    message = payload.get("message", {})
    resource_ids = message.get("resource_ids")

    try:
        if not isinstance(resource_ids, list) or not resource_ids:
            raise AvailabilityCheckError("resource_ids must be a non-empty list")
        if len(resource_ids) > MAX_RESOURCE_IDS:
            raise AvailabilityCheckError(
                f"resource_ids exceeds the {MAX_RESOURCE_IDS}-item limit"
            )
        if not all(isinstance(r, str) for r in resource_ids):
            raise AvailabilityCheckError("resource_ids must be strings")

        day = _parse_date(message.get("date"))
        today_ist = timezone.now().astimezone(INDIA_TZ).date()
        if day < today_ist:
            raise AvailabilityCheckError("date is in the past")
        if day > today_ist + timedelta(days=MAX_DAYS_AHEAD):
            raise AvailabilityCheckError(f"date is more than {MAX_DAYS_AHEAD} days out")
    except AvailabilityCheckError as exc:
        record_availability_check_rejected()
        return (
            build_nack_response(
                context=context, error={"code": "AVAILABILITY_ERROR", "message": exc.message}
            ),
            400,
        )

    # A malformed (non-UUID) id can never match a real Resource — excluded from the
    # DB query entirely rather than letting the queryset evaluation raise on it.
    well_formed_ids = []
    for raw_id in resource_ids:
        try:
            uuid.UUID(raw_id)
        except ValueError:
            continue
        well_formed_ids.append(raw_id)

    visible_ids = set(
        visible_resources(context["domain"])
        .filter(id__in=well_formed_ids)
        .values_list("id", flat=True)
    )
    visible_ids = {str(rid) for rid in visible_ids}

    day_start_utc, day_end_utc = _day_bounds_utc(day)
    now_utc = timezone.now()
    lower_bound = max(day_start_utc, now_utc)  # today: excludes slots that already ended

    available_ids = set(
        Slot.objects.filter(
            resource_id__in=visible_ids,
            status=Slot.Status.AVAILABLE,
            capacity_remaining__gt=0,
            start_time__lt=day_end_utc,
            end_time__gt=lower_bound,
        )
        .values_list("resource_id", flat=True)
        .distinct()
    )
    available_ids = {str(rid) for rid in available_ids}

    response = build_ack_response(context=context)
    response["message"]["availability"] = {
        raw_id: (raw_id in available_ids) for raw_id in resource_ids
    }
    record_availability_check_succeeded()
    return response, 200


# A real day never has more than a handful of dozen bookable slots for one
# resource — this is a defensive ceiling, not a limit anyone should ever hit.
MAX_SLOTS_RETURNED = 200


def list_available_slots(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """The real bookable time windows for ONE resource on `date` — what the
    booking UI's time picker shows, so a customer only ever sees times that
    genuinely exist instead of typing an arbitrary one and finding out only at
    /select that nothing was actually open then. Kept as its own call rather
    than a "give me full slot detail" flag on check_availability: that one's
    own batch-of-up-to-50-ids shape should never have to carry full slot lists
    for every one of them at once, just a yes/no per id.

    Same uniform-non-leaking answer discipline as check_availability: an
    invalid, out-of-domain, or paused-business resource_id gets an empty slot
    list back (200), not a 404/400 that would reveal *which* of those it was."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        record_availability_check_rejected()
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "AVAILABILITY_ERROR", "message": f"Invalid context: {exc}"},
            ),
            400,
        )

    try:
        trust.verify_bap_and_gateway(
            context=context,
            authorization_header=authorization_header,
            gateway_authorization_header=gateway_authorization_header,
            body=body,
            require_gateway=False,
        )
    except trust.TrustEstablishmentError as exc:
        record_availability_check_rejected()
        return (
            build_nack_response(
                context=context, error={"code": "AVAILABILITY_ERROR", "message": str(exc)}
            ),
            401,
        )

    message = payload.get("message", {})
    resource_id = message.get("resource_id")

    try:
        if not isinstance(resource_id, str) or not resource_id:
            raise AvailabilityCheckError("resource_id is required")
        day = _parse_date(message.get("date"))
        today_ist = timezone.now().astimezone(INDIA_TZ).date()
        if day < today_ist:
            raise AvailabilityCheckError("date is in the past")
        if day > today_ist + timedelta(days=MAX_DAYS_AHEAD):
            raise AvailabilityCheckError(f"date is more than {MAX_DAYS_AHEAD} days out")
    except AvailabilityCheckError as exc:
        record_availability_check_rejected()
        return (
            build_nack_response(
                context=context, error={"code": "AVAILABILITY_ERROR", "message": exc.message}
            ),
            400,
        )

    response = build_ack_response(context=context)
    try:
        uuid.UUID(resource_id)
    except ValueError:
        response["message"]["slots"] = []
        record_availability_check_succeeded()
        return response, 200

    visible_ids = set(
        visible_resources(context["domain"]).filter(id=resource_id).values_list("id", flat=True)
    )
    if not visible_ids:
        response["message"]["slots"] = []
        record_availability_check_succeeded()
        return response, 200

    day_start_utc, day_end_utc = _day_bounds_utc(day)
    now_utc = timezone.now()
    lower_bound = max(day_start_utc, now_utc)

    # Real gap found and closed live 2026-11-05: this used to filter to AVAILABLE-
    # with-capacity only, so an already-booked (or currently-held) slot simply
    # vanished from the response — the customer-facing time picker had no way to
    # show "this one's taken" versus "this time doesn't exist at all" (BAP's own
    # UI wants to render every real slot in the day, greying out the ones it can't
    # offer, not silently omit them). CANCELLED is the one status still excluded —
    # that slot was pulled from the provider's own schedule entirely, not taken by
    # another customer, so there is no real time window left to show at all.
    slots = (
        Slot.objects.filter(
            resource_id=resource_id,
            start_time__lt=day_end_utc,
            end_time__gt=lower_bound,
        )
        .exclude(status=Slot.Status.CANCELLED)
        # `id` is the tiebreaker, not just `start_time` — AutoCare's own
        # token/queue mode (slot_count) creates many Slot rows sharing one
        # identical start_time, and a tie with no secondary sort key has no
        # guaranteed stable order in Postgres, which would make the numbered
        # (1..N) display BAP shows for them reshuffle between requests.
        .order_by("start_time", "id")[:MAX_SLOTS_RETURNED]
    )
    response["message"]["slots"] = [
        {
            # Project-defined, not a real Schedule/Time field (no such concept
            # exists in the real protocol schema) — exists only so a BAP can
            # disambiguate which *specific* Slot a customer picked when more
            # than one shares the identical start_time (AutoCare's token/queue
            # mode); every other domain's own UI can and does ignore it, same
            # "additive, ignorable by existing callers" precedent as
            # trigger_select's own hold_seconds.
            "id": str(s.id),
            "start_time": s.start_time.isoformat(),
            "end_time": s.end_time.isoformat(),
            "status": (
                "available"
                if s.status == Slot.Status.AVAILABLE and s.capacity_remaining > 0
                else "booked"
            ),
        }
        for s in slots
    ]
    record_availability_check_succeeded()
    return response, 200
