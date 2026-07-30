# API Conventions

Applies to every **non-Beckn-protocol** API surface each app exposes (internal APIs, admin/ops endpoints, BAP/BPP web-to-backend calls). It does **not** override the Beckn/ONDC protocol wire format — Registry's `/subscribe`, `/lookup`, `/on_subscribe`, and the full transaction API (`/search`, `/on_search`, etc.) follow the confirmed ACK/NACK envelope documented in [protocol_compliance_notes_v1.1.md](protocol_compliance_notes_v1.1.md) §A–D exactly as specified there, not the conventions below.

## Standardized Error Response Shape

Every non-protocol API error returns this JSON shape, with the matching HTTP status code:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "signing_public_key is required",
    "field": "signing_public_key",
    "correlation_id": "..."
  }
}
```

- `code` — a stable, machine-readable string (`VALIDATION_ERROR`, `NOT_FOUND`, `UNAUTHORIZED`, `RATE_LIMITED`, `INTERNAL_ERROR`, …). Never changes for a given failure class, even if `message` wording changes.
- `message` — human-readable, safe to show to a developer/caller. Never a raw stack trace or internal exception string (see [SECURITY.md](SECURITY.md) — debug mode / verbose errors must be off outside local/dev).
- `field` — optional, present for validation errors naming the offending field.
- `correlation_id` — always present, matches [OBSERVABILITY.md](OBSERVABILITY.md)'s `X-Correlation-Id`, so a caller can hand this to support/logs.

No endpoint returns a bare 500 with no body — every failure path produces this shape, including unhandled exceptions (caught by a global exception handler per app, per `livetracker1.md` Phase 1.x "Exception Handling").

## Idempotency

Any endpoint that creates or mutates state and could plausibly be retried (network timeout, client retry logic) accepts an `Idempotency-Key` header. The server stores the key against the resulting response for a bounded window (24h default) and replays the same response for a repeated key instead of re-executing the mutation. This is the internal-API equivalent of the idempotency behavior the Beckn protocol layer gets "for free" via `/subscribe`'s natural idempotency on `subscriber_id` (protocol_compliance_notes_v1.1.md §A.1) — our own APIs need the same discipline explicitly since they don't have that built in.

## Versioning

URL-path versioning: `/api/v1/...`. A breaking change to a non-protocol endpoint's request/response shape requires a new version path (`/api/v2/...`), not an in-place change — existing callers must not silently break. Beckn/ONDC protocol endpoints are versioned per the protocol's own scheme (e.g., Registry Lookup's `/v2.0/lookup`, per protocol_compliance_notes_v1.1.md §B.1), independent of this internal convention.

## Async Trigger + Poll (real gap found and closed, `livetracker3.md` §5.1)

Every customer-facing web-to-backend action that maps to a Beckn transaction (search, select, init, confirm, status, cancel, update, track, rating, support — `BAP/backend/core/views.py`'s `*_trigger_view`s) follows the same two-endpoint shape, used identically by all 10 and never previously named as its own convention here despite being this project's single most common non-protocol API pattern:

1. **Trigger** (`POST`) — builds and sends the real signed Beckn request to Gateway synchronously (so a Gateway/BPP rejection at the ACK/NACK level surfaces immediately as an error), then returns `202 Accepted` with `{"transaction_id": "..."}` the moment the ACK is confirmed — it does **not** wait for the real business result (`on_confirm`, `on_cancel`, etc.), since that arrives later, asynchronously, as its own separate wire callback.
2. **Result** (`GET`) — a separate, matching `*_result` endpoint the client polls with the same `transaction_id`, returning the current state: the real result once the callback has landed, or a normal in-progress shape (e.g. `{"confirmed_order": null, "confirm_error": null}`) beforehand — an honest "not yet" is not an error.

This mirrors the Beckn protocol's own ACK-now/callback-later shape (protocol_compliance_notes_v1.1.md §D) one layer up, at the browser-facing boundary, rather than making the browser hold a connection open across a real multi-hop BAP→Gateway→BPP→Gateway→BAP round trip.

## Pagination

List endpoints use cursor-based pagination (`?cursor=...&limit=...`), not offset-based — avoids the classic offset-pagination correctness problem under concurrent writes. Response includes `next_cursor: string | null`.
