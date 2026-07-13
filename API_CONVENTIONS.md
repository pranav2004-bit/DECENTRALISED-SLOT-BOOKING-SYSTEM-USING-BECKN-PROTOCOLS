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

## Pagination

List endpoints use cursor-based pagination (`?cursor=...&limit=...`), not offset-based — avoids the classic offset-pagination correctness problem under concurrent writes. Response includes `next_cursor: string | null`.
