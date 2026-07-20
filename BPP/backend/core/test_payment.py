"""Phase 3.4 Test Gate (livetracker2.md §3.4) — the minimal Payment Module
placeholder, confirming it returns the real, standardized NOT_YET_IMPLEMENTED
error shape rather than not existing at all."""

import pytest

from core.payment_service import PaymentNotYetImplementedError, initiate_payment


def test_initiate_payment_raises_the_standardized_not_yet_implemented_error():
    with pytest.raises(PaymentNotYetImplementedError) as exc_info:
        initiate_payment(booking_id="booking-1", amount="899.00")

    assert exc_info.value.code == "NOT_YET_IMPLEMENTED"
