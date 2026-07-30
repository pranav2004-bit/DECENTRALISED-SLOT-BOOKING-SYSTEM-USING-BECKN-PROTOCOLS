"""livetracker3.md §4.1 Test Gate — the real transactional email send logic in
`notifications.py`. Tests call the synchronous `notify_booking_*` functions
directly (not the `_in_background` wrappers) to avoid racing a background
thread, matching this codebase's own established dispatch_on_X test pattern
(e.g. `test_search.py`'s `dispatch_on_search` calls). Integration coverage
proving `record_on_confirm_result`/`record_on_cancel_result`/
`record_on_update_result` actually trigger these lives in each action's own
`test_confirm.py`/`test_cancel.py`/`test_update.py`.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from core import notifications
from core.models import SearchSession

Customer = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret

ORDER = {
    "id": "booking-1",
    "quote": {"breakup": [{"title": "Stylist A"}]},
    "fulfillments": [{"stops": [{"time": {"timestamp": "2026-08-05T09:00:00+00:00"}}]}],
}


def _session_with_customer(*, notify_by_email=True, contact="customer@example.com"):
    customer = Customer.objects.create_user(
        contact=contact, name="Test Customer", password=TEST_PASSWORD,
        notify_by_email=notify_by_email,
    )
    return SearchSession.objects.create(
        transaction_id="txn-1", domain="ONDC:RET13", customer=customer
    )


@pytest.mark.django_db
def test_notify_booking_confirmed_sends_a_real_email_with_correct_details():
    session = _session_with_customer()

    notifications.notify_booking_confirmed(session=session, order=ORDER)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["customer@example.com"]
    assert "confirmed" in sent.subject.lower()
    assert "Stylist A" in sent.body
    assert "booking-1" in sent.body
    assert "2026-08-05T09:00:00+00:00" in sent.body


@pytest.mark.django_db
def test_notify_booking_cancelled_sends_a_real_email_with_correct_details():
    session = _session_with_customer()

    notifications.notify_booking_cancelled(session=session, order=ORDER)

    assert len(mail.outbox) == 1
    assert "cancelled" in mail.outbox[0].subject.lower()
    assert "booking-1" in mail.outbox[0].body


@pytest.mark.django_db
def test_notify_booking_rescheduled_sends_a_real_email_with_correct_details():
    """Real gap found and fixed via this tracker's own re-verification pass:
    the real /on_update order (confirmed by reading `update_service.py`'s BPP
    dispatch directly) never carries `quote`/`breakup`, only a fresh `stops` —
    the same gap `notify_booking_cancelled` had for `quote` entirely. The item
    name must come from `session.confirmed_order` instead; the new time still
    comes from the real (sparse) updated order, since that's the one field
    this action genuinely changed."""
    session = _session_with_customer()
    session.confirmed_order = ORDER
    session.save()
    sparse_updated_order = {
        "id": "booking-1",
        "status": "ACTIVE",
        "fulfillments": [
            {"id": "booking-1", "stops": [{"type": "start", "time": {"timestamp": "2026-08-06T14:00:00+00:00"}}]}
        ],
    }
    assert "quote" not in sparse_updated_order

    notifications.notify_booking_rescheduled(session=session, order=sparse_updated_order)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert "rescheduled" in sent.subject.lower()
    assert "Stylist A" in sent.body  # from confirmed_order, not the sparse updated order
    assert "2026-08-06T14:00:00+00:00" in sent.body  # the real NEW time


@pytest.mark.django_db
def test_notify_booking_confirmed_respects_notify_by_email_opt_out():
    """NEG — §4.1's own Test Gate wording: an opted-out customer produces no email."""
    session = _session_with_customer(notify_by_email=False)

    notifications.notify_booking_confirmed(session=session, order=ORDER)

    assert mail.outbox == []


@pytest.mark.django_db
def test_notify_booking_confirmed_does_not_email_a_phone_only_contact():
    """A `Customer.contact` isn't always email-shaped (email-or-phone, one
    field) — a phone number must not be treated as a recipient address."""
    session = _session_with_customer(contact="+91-9876543210")

    notifications.notify_booking_confirmed(session=session, order=ORDER)

    assert mail.outbox == []


@pytest.mark.django_db
def test_notify_booking_confirmed_is_a_real_noop_for_an_anonymous_booking():
    """Search never requires login — a real anonymous booking has no customer
    to notify, and this must not crash on the missing FK."""
    session = SearchSession.objects.create(
        transaction_id="txn-1", domain="ONDC:RET13", customer=None
    )

    notifications.notify_booking_confirmed(session=session, order=ORDER)

    assert mail.outbox == []


@pytest.mark.django_db
def test_notify_booking_confirmed_send_failure_is_logged_not_raised():
    """§4.1's own Test Gate: a mail-send failure is logged and does not roll
    back or fail the underlying booking action — proven by confirming this
    never raises, even when the real send itself blows up."""
    session = _session_with_customer()

    with patch("core.notifications.send_mail", side_effect=RuntimeError("smtp down")):
        notifications.notify_booking_confirmed(session=session, order=ORDER)  # must not raise

    assert mail.outbox == []


@pytest.mark.django_db
def test_notify_booking_confirmed_handles_a_missing_order_gracefully():
    """A `None` order (defensively handled by `_item_name`/`_stop_timestamp`/
    `_order_id` now treating it as `{}`) still sends a real, honest email —
    with the generic fallback text, not a crash."""
    session = _session_with_customer()

    notifications.notify_booking_confirmed(session=session, order=None)

    assert len(mail.outbox) == 1
    assert "your service" in mail.outbox[0].body


@pytest.mark.django_db
def test_notify_booking_confirmed_malformed_order_is_logged_not_raised():
    """Real gap found and fixed via this tracker's own re-verification pass:
    the original try/except only wrapped the actual send_mail() call, not the
    message-formatting logic before it — a genuinely malformed order shape
    (not just a missing one) would have raised uncaught inside the
    background thread. Proven fixed here: `quote` is present but isn't a
    dict, so `.get("breakup")` on it would raise `AttributeError` without
    the fix — confirmed this still never raises and never sends."""
    session = _session_with_customer()

    notifications.notify_booking_confirmed(
        session=session, order={"quote": "not-a-dict", "id": "booking-1"}
    )  # must not raise

    assert mail.outbox == []


@pytest.mark.django_db
def test_notify_booking_rescheduled_malformed_confirmed_order_is_logged_not_raised():
    """Same defensive coverage as above, for the reschedule path's own extra
    session.confirmed_order lookup specifically."""
    session = _session_with_customer()
    session.confirmed_order = {"quote": "not-a-dict"}
    session.save()

    notifications.notify_booking_rescheduled(session=session, order=ORDER)  # must not raise

    assert mail.outbox == []
