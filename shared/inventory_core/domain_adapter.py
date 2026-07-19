"""Domain Adapter Interface — extension point only (livetracker2.md §1.5, ADR-0003). No real
domain adapter is built here: Phase 2 builds the real Beauty adapter (stylist/chair, combo-service
sequential slot chaining) on top of this interface; Phase 4 widens to Healthcare/Automotive using
the same interface. This file defines the contract only.

Domain-specific fields (consultation type, combo services, multi-resource requirements) plug into
the generic `Resource`/`Booking` core through this interface plus each model's own `domain_data`
JSONField (`models.py`) — a generic, schema-level escape hatch — rather than forking `Resource`/
`Slot`/`Booking` per domain. This is exactly the "one generic core, three domains" commitment
ADR-0003 already made; this interface is what keeps that commitment enforceable rather than
aspirational.

Grounded against the real protocol: `Fulfillment.type` (per `beckn/protocol-specifications`) is
explicitly a free-text, domain-policy-defined field, not a fixed enum — `fulfillment_type()`
mirrors that real design rather than inventing a centrally-owned list of fulfillment types.
"""

from abc import ABC, abstractmethod


class DomainAdapter(ABC):
    """One subclass per domain (Beauty in Phase 2; Healthcare/Automotive in Phase 4). Abstract —
    cannot be instantiated directly, so a domain that skips implementing part of the contract
    fails at class-definition time, not silently at runtime.
    """

    domain_code: str

    @abstractmethod
    def validate_resource_domain_data(self, domain_data: dict) -> None:
        """Validate a `Resource.domain_data` payload for this domain (e.g. Beauty's
        consultation type). Raise `django.core.exceptions.ValidationError` on invalid data."""

    @abstractmethod
    def validate_booking_domain_data(self, domain_data: dict) -> None:
        """Validate a `Booking.domain_data` payload for this domain (e.g. Beauty's combo-service
        steps). Raise `ValidationError` on invalid data."""

    @abstractmethod
    def required_resource_count(self, booking_domain_data: dict) -> int:
        """How many `Resource`s one booking under this domain's fields needs — the
        multi-resource-requirement hook (e.g. a combo service needing a stylist *and* a
        colorist). Returns `1` for the ordinary single-resource case."""

    @abstractmethod
    def fulfillment_type(self, booking_domain_data: dict) -> str:
        """This domain's own value for the real, domain-policy-defined `Fulfillment.type`
        protocol field."""


_REGISTRY: dict[str, DomainAdapter] = {}


def register_adapter(domain_code: str, adapter: DomainAdapter) -> None:
    """Registers `adapter` as the one to use for `domain_code` (e.g. the real `ONDC:RET13`
    Beauty code, Phase 2). Overwrites any previous registration for the same code."""
    _REGISTRY[domain_code] = adapter


def get_adapter(domain_code: str) -> DomainAdapter:
    """Raises `LookupError` for an unregistered `domain_code` — never silently returns `None`
    or a default adapter, so a missing domain wiring fails loudly and immediately."""
    try:
        return _REGISTRY[domain_code]
    except KeyError:
        raise LookupError(
            f"No DomainAdapter registered for domain_code={domain_code!r}. "
            f"Registered: {sorted(_REGISTRY)}"
        ) from None
