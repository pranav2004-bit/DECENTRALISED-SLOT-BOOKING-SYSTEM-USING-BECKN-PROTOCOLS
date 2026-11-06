"""Doctor/resource availability-by-date check — customer-facing trigger for BAP's
search-results UI, which needs to know, per selected date, which items on a
provider's catalog actually have a real open slot before it renders their card
(livetracker9.md). Mirrors select_service.py's own direct-dispatch pattern exactly
(a fresh Registry lookup immediately before dispatch + `crypto.sign_outbound_request`,
never a client-supplied destination URL) — but synchronous, not trigger+poll: there's
no async gap to bridge for a read with no side effects, so the one call returns the
real answer directly (see BPP/backend/core/availability_service.py's own module
docstring for the matching BPP-side reasoning).
"""

import json
import logging

from beckn_transaction import build_context, new_message_id
from django.conf import settings
from django.utils import timezone

from . import registry_client
from .crypto import sign_outbound_request
from .metrics import record_availability_check_failed, record_availability_check_succeeded
from .models import SearchSession
from .participant_keys import get_signing_keys
from .session_authz import SessionAccessError, resolve_owned_session

logger = logging.getLogger("bap")

# Matches BPP's own per-call cap (BPP/backend/core/availability_service.py's
# MAX_RESOURCE_IDS) — batched into multiple signed calls below rather than
# silently truncated at this limit, so a provider with more items than one
# call can carry still gets a real answer for every one of them, not a
# false "unavailable" for whatever didn't fit.
MAX_RESOURCE_IDS = 50


class AvailabilityError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _find_provider_bpp(session: SearchSession, provider_id: str) -> tuple[str, str, set[str]]:
    """Mirrors select_service._find_item_provider's own discipline exactly, at
    provider granularity: resolves (bpp_id, bpp_uri) and the real set of item ids
    this session's own on_search results actually returned for that provider — never
    a client-claimed set. Raises AvailabilityError(404) if the session never saw
    this provider, same "can only act on what a real search genuinely returned"
    defense _find_item_provider already established."""
    for result in session.results:
        catalog = result.get("catalog", {})
        for provider in catalog.get("providers", []):
            if provider.get("id") == provider_id:
                item_ids = {item["id"] for item in provider.get("items", [])}
                return result["bpp_id"], result["bpp_uri"], item_ids
    raise AvailabilityError(
        f"No such provider {provider_id!r} in this search's results", status_code=404
    )


def _check_one_batch(
    *,
    session: SearchSession,
    transaction_id: str,
    bpp_id: str,
    bpp_uri: str,
    resource_ids: list[str],
    date: str,
) -> dict[str, bool]:
    """One signed round trip for at most MAX_RESOURCE_IDS ids — see
    check_doctor_availability's own docstring for why a larger request is split
    into several of these rather than truncated."""
    context = build_context(
        domain=session.domain,
        action="check_availability",
        version="1.1.0",
        bap_id=settings.SUBSCRIBER_ID,
        bap_uri=settings.SUBSCRIBER_URL,
        bpp_id=bpp_id,
        bpp_uri=bpp_uri,
        transaction_id=transaction_id,
        message_id=new_message_id(),
        location={"country": {"code": "IND"}},
        timestamp=timezone.now().isoformat(),
    )
    payload = {"context": context, "message": {"resource_ids": resource_ids, "date": date}}
    body = json.dumps(payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    try:
        bpp_url = registry_client.resolve_subscribed_bpp(bpp_id)
    except registry_client.RegistryLookupError as exc:
        record_availability_check_failed()
        raise AvailabilityError(str(exc), status_code=502) from exc

    try:
        response = registry_client.get_bpp_client(bpp_id).post(
            bpp_url.rstrip("/") + "/check_availability",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        record_availability_check_failed()
        raise AvailabilityError(f"BPP unreachable: {exc}", status_code=502) from exc

    response_json = response.json()
    ack_status = response_json.get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        record_availability_check_failed()
        error = response_json.get("error") or {}
        raise AvailabilityError(
            error.get("message", "BPP rejected the availability check (NACK)"), status_code=502
        )

    record_availability_check_succeeded()
    return response_json.get("message", {}).get("availability", {})


def check_doctor_availability(
    *,
    transaction_id: str,
    provider_id: str,
    resource_ids: list[str],
    date: str,
    customer=None,
) -> dict[str, bool]:
    """Checks which of `resource_ids` have an open slot on `date`, restricted to the
    ones that genuinely belong to `provider_id` per this session's own real
    on_search results — a client-supplied id outside that set is silently dropped
    before anything is ever sent to a BPP, not an error (never used as a probe for
    an arbitrary resource id on an arbitrary BPP). Returns `{resource_id: bool}`;
    an id that was dropped, or that BPP itself didn't answer for, simply has no
    entry — callers already treat "missing" the same as `False`.

    More than MAX_RESOURCE_IDS ids is split into several signed calls, not
    truncated — a provider with more items than one call's own cap still gets a
    real answer for every one of them. The first batch's failure is raised
    immediately (same as before); a later batch's failure is logged, not raised,
    so one bad batch doesn't erase genuine answers the earlier ones already got."""
    try:
        session = resolve_owned_session(transaction_id=transaction_id, requesting_customer=customer)
    except SessionAccessError as exc:
        raise AvailabilityError(exc.message, status_code=exc.status_code) from exc

    bpp_id, bpp_uri, valid_item_ids = _find_provider_bpp(session, provider_id)

    checked_ids = [rid for rid in dict.fromkeys(resource_ids) if rid in valid_item_ids]
    if not checked_ids:
        return {}

    batches = [
        checked_ids[i : i + MAX_RESOURCE_IDS] for i in range(0, len(checked_ids), MAX_RESOURCE_IDS)
    ]

    availability: dict[str, bool] = {}
    first_batch = _check_one_batch(
        session=session,
        transaction_id=transaction_id,
        bpp_id=bpp_id,
        bpp_uri=bpp_uri,
        resource_ids=batches[0],
        date=date,
    )
    availability.update(first_batch)

    for batch in batches[1:]:
        try:
            result = _check_one_batch(
                session=session,
                transaction_id=transaction_id,
                bpp_id=bpp_id,
                bpp_uri=bpp_uri,
                resource_ids=batch,
                date=date,
            )
            availability.update(result)
        except AvailabilityError:
            logger.exception(
                "check_doctor_availability: a later batch failed for transaction_id=%r "
                "provider_id=%r — returning the batches that did succeed rather than "
                "failing the whole request",
                transaction_id,
                provider_id,
            )

    return availability


def list_doctor_slots(
    *,
    transaction_id: str,
    provider_id: str,
    resource_id: str,
    date: str,
    customer=None,
) -> list[dict]:
    """The real bookable time windows for ONE doctor on `date` — what the
    booking page's time picker shows, so a customer can only ever request a
    time that genuinely exists (BPP/backend/core/availability_service.py's own
    list_available_slots docstring). Same anti-IDOR discipline as
    check_doctor_availability: a resource_id outside this session's own real
    results for provider_id returns an empty list, never reaching a BPP."""
    try:
        session = resolve_owned_session(transaction_id=transaction_id, requesting_customer=customer)
    except SessionAccessError as exc:
        raise AvailabilityError(exc.message, status_code=exc.status_code) from exc

    bpp_id, bpp_uri, valid_item_ids = _find_provider_bpp(session, provider_id)
    if resource_id not in valid_item_ids:
        return []

    context = build_context(
        domain=session.domain,
        action="list_available_slots",
        version="1.1.0",
        bap_id=settings.SUBSCRIBER_ID,
        bap_uri=settings.SUBSCRIBER_URL,
        bpp_id=bpp_id,
        bpp_uri=bpp_uri,
        transaction_id=transaction_id,
        message_id=new_message_id(),
        location={"country": {"code": "IND"}},
        timestamp=timezone.now().isoformat(),
    )
    payload = {"context": context, "message": {"resource_id": resource_id, "date": date}}
    body = json.dumps(payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    try:
        bpp_url = registry_client.resolve_subscribed_bpp(bpp_id)
    except registry_client.RegistryLookupError as exc:
        record_availability_check_failed()
        raise AvailabilityError(str(exc), status_code=502) from exc

    try:
        response = registry_client.get_bpp_client(bpp_id).post(
            bpp_url.rstrip("/") + "/list_available_slots",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        record_availability_check_failed()
        raise AvailabilityError(f"BPP unreachable: {exc}", status_code=502) from exc

    response_json = response.json()
    ack_status = response_json.get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        record_availability_check_failed()
        error = response_json.get("error") or {}
        raise AvailabilityError(
            error.get("message", "BPP rejected the slot list request (NACK)"), status_code=502
        )

    record_availability_check_succeeded()
    return response_json.get("message", {}).get("slots", [])
