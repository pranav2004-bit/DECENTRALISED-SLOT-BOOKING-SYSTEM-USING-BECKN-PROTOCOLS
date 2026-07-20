"""Payment Module placeholder (livetracker2.md §3.4). Both BAP_details_v1.1.md and
BPP_details_v1.1.md list a Payment Module as a named peer of Select/Init/Confirm/Cancel
in their Transaction Module breakdown — this exists so that module list isn't silently
incomplete, without doing real payment-gateway work now (explicitly deferred to a future
`livetracker3.md`, per `project_details.md`'s payment KPI being out of this tracker's scope).

Not wired to any real HTTP endpoint — the real protocol has no dedicated `/pay` action;
payment collection is out-of-band from a Beckn network's perspective. `initiate_payment()`
exists only so a caller reaching for real payment functionality gets a real, standardized
`NOT_YET_IMPLEMENTED` error instead of an `AttributeError`/`ImportError` from a module that
simply doesn't exist.
"""


class PaymentNotYetImplementedError(Exception):
    def __init__(self):
        super().__init__(
            "Payment collection is not yet implemented — deferred to a future payment "
            "gateway integration phase, out of this tracker's scope."
        )
        self.code = "NOT_YET_IMPLEMENTED"


def initiate_payment(*, booking_id: str, amount) -> None:
    """Always raises `PaymentNotYetImplementedError` — the real, standardized-error-shaped
    placeholder for a module that doesn't do real work yet, per livetracker2.md §3.4."""
    raise PaymentNotYetImplementedError()
