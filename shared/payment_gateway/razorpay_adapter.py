"""Razorpay adapter (livetracker5.md Phase 2.1) — the first real `PaymentGatewayAdapter`
implementation, for the vendor picked in Phase 0.1. Sandbox-only per this tracker's own
Rule 4 until explicitly authorized otherwise — this file never touches production
credentials or fires a real charge on its own.

API shapes confirmed directly against Razorpay's own documentation (Tier A sources,
not guessed), 2026-09-25:
- Orders Create: https://razorpay.com/docs/api/orders/create/
- Refunds (idempotent): https://razorpay.com/docs/api/refunds/normal-refunds-idempotent/
- Fetch Payments for an Order: https://razorpay.com/docs/api/orders/fetch-payments/
- Webhook payment payloads: https://razorpay.com/docs/webhooks/payloads/payments/

**Hosted-checkout architecture (Design Principle 4):** `charge()` does not synchronously
capture money — it creates a Razorpay Order, which the frontend completes via Razorpay's
own hosted Checkout widget (no card data ever reaches this codebase). Real resolution
(`captured`/`failed`) arrives later via the signature-verified webhook (`verify_webhook`
below), which is why a successful `charge()` call legitimately returns
`PaymentStatus.PENDING`, not `SUCCEEDED` — `BAP/backend/core/payment_service.py`'s
`_run_charge()` (Phase 1.3) already handles a `PENDING` result correctly by leaving the
`PaymentTransaction` row as-is for the webhook to resolve.

**Idempotency, confirmed per-vendor as Phase 2.3 requires:** Razorpay supports real
request-level idempotency for both calls this adapter makes — Orders Create treats
`receipt` as an idempotency key (a second create with the same value is rejected, not
duplicated) and Refunds has a dedicated `X-Refund-Idempotency` header. Both are wired to
this project's own `idempotency_key` below, which means `shared/resilient_http`'s default
urllib3-level auto-retry (enabled, not disabled) is safe to use as-is for this vendor: a
transport-level retry of the identical POST carries the identical
`receipt`/`X-Refund-Idempotency` value, so Razorpay's own API treats it as a no-op replay,
never a second charge/refund. The `max_retries=0` fallback Phase 2.3 names for vendors
*without* idempotency support does not apply here.
"""

import hashlib
import hmac
import json
from decimal import Decimal

import requests
from resilient_http import CircuitOpenError

from .interface import (
    PaymentGatewayAdapter,
    PaymentResult,
    PaymentStatus,
    RefundResult,
    WebhookEvent,
    WebhookVerificationError,
)

_DEFAULT_BASE_URL = "https://api.razorpay.com/v1"

# Real, documented status mappings (Tier A, not invented).
_PAYMENT_STATUS_MAP = {
    "created": PaymentStatus.PENDING,
    "authorized": PaymentStatus.PENDING,
    "captured": PaymentStatus.SUCCEEDED,
    "failed": PaymentStatus.FAILED,
    "refunded": PaymentStatus.REFUNDED,
}
_REFUND_STATUS_MAP = {
    "pending": PaymentStatus.PENDING,
    "processed": PaymentStatus.REFUNDED,
    "failed": PaymentStatus.FAILED,
}


def _to_paise(amount: Decimal) -> int:
    """Razorpay's own documented convention: amount is the smallest currency
    subunit (paise for INR), a plain integer — never a decimal or float."""
    return int((amount * 100).to_integral_value())


def _transport_failure_reason(exc: Exception) -> str:
    """Maps a raw transport-level exception to one of Design Principle 5's
    named, honest failure reasons — never the exception's own raw message
    (which could contain internal detail not meant for a customer-facing
    error), and never a bare "something went wrong"."""
    if isinstance(exc, CircuitOpenError):
        return "GATEWAY_UNAVAILABLE"
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.RetryError)):
        return "GATEWAY_TIMEOUT"
    return "GATEWAY_UNAVAILABLE"


def _sanitize_refund_idempotency_key(key: str) -> str:
    """`X-Refund-Idempotency` (Razorpay's own documented constraint): minimum 10
    characters, alphanumeric + hyphen + underscore only. This project's own
    `idempotency_key` format uses colons as separators (`refund:{transaction_id}:
    {n}`), which Razorpay's header would reject outright. The transformation is
    deterministic — the same logical key always produces the same sanitized
    value, so retries still dedupe correctly."""
    sanitized = "".join(c if c.isalnum() or c in "-_" else "-" for c in key)
    return sanitized.ljust(10, "0")


class RazorpayAdapter(PaymentGatewayAdapter):
    vendor_code = "razorpay"

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        webhook_secret: str,
        http_client,
        base_url: str = _DEFAULT_BASE_URL,
    ):
        """`http_client`: a `shared/resilient_http.ResilientHttpClient` instance
        (Phase 2.3) — this adapter never constructs its own `requests` session,
        so timeout/retry/circuit-breaker behavior is always this project's
        shared, already-proven implementation, not a bespoke copy.

        `base_url`: honors the pre-existing `PAYMENT_GATEWAY_BASE_URL` env var
        (reserved in `.env.example` since before this tracker existed, never
        wired to real code until now) — defaults to Razorpay's real API host,
        overridable for a sandbox-specific host if the vendor ever uses a
        different one, without a code change."""
        self._key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        self._http = http_client
        self._base_url = base_url.rstrip("/") if base_url else _DEFAULT_BASE_URL

    def charge(
        self, *, amount: Decimal, currency: str, idempotency_key: str, metadata: dict
    ) -> PaymentResult:
        try:
            response = self._http.post(
                f"{self._base_url}/orders",
                auth=(self._key_id, self._key_secret),
                json={
                    "amount": _to_paise(amount),
                    "currency": currency,
                    "receipt": idempotency_key[:40],
                    "notes": {k: str(v) for k, v in metadata.items()},
                },
            )
        except (requests.exceptions.RequestException, CircuitOpenError) as exc:
            # Phase 2.3's own Test Gate: a real timeout/retries-exhausted/open-
            # circuit failure must never leave the caller with an unhandled
            # exception — an honest FAILED result with a specific reason
            # (Design Principle 5), same discipline as a real vendor decline.
            # The PaymentTransaction this becomes stays retryable (Phase 1.3's
            # attempt-numbering) since it's a fresh row, never a duplicate charge.
            return PaymentResult(
                status=PaymentStatus.FAILED,
                vendor_txn_id=None,
                raw_response={},
                failure_reason=_transport_failure_reason(exc),
            )
        body = response.json()
        if response.status_code >= 400:
            return PaymentResult(
                status=PaymentStatus.FAILED,
                vendor_txn_id=None,
                raw_response=body,
                failure_reason=body.get("error", {}).get("description", "DECLINED"),
            )
        # A successfully CREATED order is not yet a captured payment — the
        # customer still has to complete Razorpay's hosted checkout widget.
        return PaymentResult(status=PaymentStatus.PENDING, vendor_txn_id=body["id"], raw_response=body)

    def refund(
        self, *, vendor_txn_id: str, amount: Decimal, idempotency_key: str
    ) -> RefundResult:
        try:
            response = self._http.post(
                f"{self._base_url}/payments/{vendor_txn_id}/refund",
                auth=(self._key_id, self._key_secret),
                headers={
                    "X-Refund-Idempotency": _sanitize_refund_idempotency_key(idempotency_key)
                },
                json={"amount": _to_paise(amount)},
            )
        except (requests.exceptions.RequestException, CircuitOpenError) as exc:
            return RefundResult(
                status=PaymentStatus.FAILED,
                vendor_refund_id=None,
                refunded_amount=Decimal("0.00"),
                raw_response={},
                failure_reason=_transport_failure_reason(exc),
            )
        body = response.json()
        if response.status_code >= 400:
            return RefundResult(
                status=PaymentStatus.FAILED,
                vendor_refund_id=None,
                refunded_amount=Decimal("0.00"),
                raw_response=body,
                failure_reason=body.get("error", {}).get("description", "REFUND_FAILED"),
            )
        status = _REFUND_STATUS_MAP.get(body.get("status"), PaymentStatus.PENDING)
        return RefundResult(
            status=status,
            vendor_refund_id=body["id"],
            refunded_amount=Decimal(body["amount"]) / 100,
            raw_response=body,
        )

    def verify_webhook(self, *, payload: bytes, headers: dict) -> WebhookEvent:
        signature = headers.get("X-Razorpay-Signature", "")
        expected = hmac.new(self._webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
        # Constant-time comparison (Design Principle 5: fail closed on trust) — a
        # naive `==` would leak timing information about how much of the
        # signature matched, a real (if narrow) side-channel for forging one.
        if not signature or not hmac.compare_digest(signature, expected):
            raise WebhookVerificationError("invalid or missing X-Razorpay-Signature")

        try:
            body = json.loads(payload)
            entity = body["payload"]["payment"]["entity"]
            order_id = entity["order_id"]
            raw_status = entity["status"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise WebhookVerificationError(f"malformed webhook payload: {exc}") from exc

        return WebhookEvent(
            vendor_txn_id=order_id,
            status=_PAYMENT_STATUS_MAP.get(raw_status, PaymentStatus.PENDING),
            raw_payload=body,
        )

    def get_status(self, *, vendor_txn_id: str) -> PaymentStatus:
        """`vendor_txn_id` here is a Razorpay Order id (what `charge()` stored),
        not a Payment id — "Fetch Payment by ID" would be the wrong endpoint.
        Orders Create's own docs confirm "an Order ID maps 1:1 to a payment
        attempt", so this project's usage never expects more than one real item
        back for an order it created."""
        try:
            response = self._http.get(
                f"{self._base_url}/orders/{vendor_txn_id}/payments",
                auth=(self._key_id, self._key_secret),
            )
        except (requests.exceptions.RequestException, CircuitOpenError) as exc:
            # Unlike charge()/refund(), there is no honest PaymentStatus value
            # for "the vendor is unreachable right now" — raising lets a
            # reconciliation caller (Phase 6.3) distinguish "try again later"
            # from "this order genuinely doesn't exist" (LookupError below).
            raise ConnectionError(f"Razorpay unreachable: {_transport_failure_reason(exc)}") from exc
        if response.status_code >= 400:
            raise LookupError(f"Razorpay order {vendor_txn_id!r} not found")
        items = response.json().get("items", [])
        if not items:
            # Order created, no payment attempt against it yet — still PENDING,
            # not an error (the customer hasn't completed checkout).
            return PaymentStatus.PENDING
        return _PAYMENT_STATUS_MAP.get(items[0].get("status"), PaymentStatus.PENDING)
