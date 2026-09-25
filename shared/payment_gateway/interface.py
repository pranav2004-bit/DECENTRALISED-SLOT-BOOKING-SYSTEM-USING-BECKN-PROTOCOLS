"""Vendor-agnostic payment gateway adapter interface (livetracker5.md Phase 1.1,
Design Principle 1). One interface, N vendor implementations — the exact "one generic
core, N domains" pattern shared/inventory_core/domain_adapter.py already established
for booking domains, applied here to payment vendors instead.

Deliberately framework-free (no Django imports) so this module stays importable
identically by any consuming Django project, matching the decoupling already
established for shared/beckn_crypto, shared/event_bus, shared/resilient_http, and
shared/key_rotation.

No vendor-specific imports or types leak into this file — Phase 1.1's own Test Gate
mechanically checks this (grep the module for any vendor SDK name, must find none).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class PaymentStatus(str, Enum):
    """The adapter layer's own honest read of where a charge/refund currently stands
    per the *vendor's* API — independent of this project's own `PaymentTransaction`
    row (the real system of record; see livetracker5.md Phase 0.4)."""

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


@dataclass(frozen=True)
class PaymentResult:
    """Return shape of `charge()` — success *and* failure both flow through this same
    shape, not an exception for the ordinary "card declined" case (Design Principle 5:
    a decline is an expected outcome, not a systems failure)."""

    status: PaymentStatus
    vendor_txn_id: str | None
    raw_response: dict = field(default_factory=dict)
    # e.g. "INSUFFICIENT_FUNDS" / "GATEWAY_TIMEOUT" / "DECLINED" — never a bare "failed"
    # (Design Principle 5).
    failure_reason: str | None = None


@dataclass(frozen=True)
class RefundResult:
    status: PaymentStatus
    vendor_refund_id: str | None
    refunded_amount: Decimal
    raw_response: dict = field(default_factory=dict)
    failure_reason: str | None = None


@dataclass(frozen=True)
class WebhookEvent:
    """Normalized shape `verify_webhook()` returns once a vendor's raw payload has
    been signature-verified and parsed — callers only ever touch this, never the
    vendor's raw payload shape directly."""

    vendor_txn_id: str
    status: PaymentStatus
    raw_payload: dict = field(default_factory=dict)


class WebhookVerificationError(Exception):
    """Raised by `verify_webhook()` on a missing/invalid/tampered signature. The
    caller must reject the request outright (401/400) and never process the
    payload — Design Principle 5's "fail closed on trust", mechanically enforced by
    this being the only way a caller can even obtain a `WebhookEvent`."""


class PaymentGatewayAdapter(ABC):
    """One subclass per vendor (Phase 2 builds the first real one, for the Phase 0.1
    vendor). Abstract — a vendor adapter that skips implementing part of the contract
    fails at class-definition time, not silently at runtime, the same discipline
    shared/inventory_core/domain_adapter.py's `DomainAdapter` already established.
    """

    vendor_code: str

    @abstractmethod
    def charge(
        self, *, amount: Decimal, currency: str, idempotency_key: str, metadata: dict
    ) -> PaymentResult:
        """Attempt to charge `amount` `currency`. Must be safe to call twice with the
        same `idempotency_key` (Design Principle 3) — a retry with the same key must
        never produce a second real charge, whether deduped by this adapter, by the
        vendor's own idempotency support, or both."""

    @abstractmethod
    def refund(
        self, *, vendor_txn_id: str, amount: Decimal, idempotency_key: str
    ) -> RefundResult:
        """Refund (fully or partially) a previously succeeded charge. Same
        idempotency contract as `charge()`."""

    @abstractmethod
    def verify_webhook(self, *, payload: bytes, headers: dict) -> WebhookEvent:
        """Verifies the vendor's own signature scheme against the raw payload before
        trusting any field in it. Raises `WebhookVerificationError` on a
        missing/invalid signature — never returns a `WebhookEvent` for one."""

    @abstractmethod
    def get_status(self, *, vendor_txn_id: str) -> PaymentStatus:
        """Live status lookup against the vendor's own API — used for reconciliation
        (e.g. a charge whose webhook never arrived), not the primary update path."""


_REGISTRY: dict[str, PaymentGatewayAdapter] = {}


def register_adapter(vendor_code: str, adapter: PaymentGatewayAdapter) -> None:
    """Registers `adapter` as the one to use for `vendor_code` (a short lowercase
    slug identifying the vendor, chosen by whichever module registers it — this
    file itself never names a specific vendor, by design). Overwrites any previous
    registration for the same code — same contract as `domain_adapter.register_adapter`.
    """
    _REGISTRY[vendor_code] = adapter


def get_adapter(vendor_code: str) -> PaymentGatewayAdapter:
    """Raises `LookupError` for an unregistered `vendor_code` — never silently
    returns `None` or a default adapter, so a missing vendor wiring fails loudly
    and immediately. No vendor is registered until Phase 2 builds and registers
    one, so calling this before then is expected to raise, not a bug."""
    try:
        return _REGISTRY[vendor_code]
    except KeyError:
        raise LookupError(
            f"No PaymentGatewayAdapter registered for vendor_code={vendor_code!r}. "
            f"Registered: {sorted(_REGISTRY)}"
        ) from None


def registered_vendors() -> list[str]:
    """All vendor_codes currently registered — e.g. for Phase 5's "prove a second
    real adapter" requirement to confirm both are actually wired, not just one."""
    return sorted(_REGISTRY)
