# BAP (Buyer App Platform)

**Protocols to follow in implementation** – Beckn protocol specifications

> **Implementation note (added post-verification, original brief unchanged above/below):** see [protocol_compliance_notes_v1.1.md](../protocol_compliance_notes_v1.1.md) for confirmed technical detail — exact Registry endpoint contracts, the dual signing/encryption key-pair model behind "Cryptography Service" and "Registry Client Service" (§10 below), and the full request-signing header format used wherever this document says requests are "digitally signed."

## 1. Application Overview
- Buyer-side Beckn participant.
- Receives requests from buyers.
- Implements the Beckn Protocol.
- Discovers providers and manages the complete buyer booking lifecycle.
- Communicates with the Registry, Gateway, BPP, and Payment Gateway.

> **Implementation note (livetracker7.md §2, 2026-08-22):** two independently-
> identified, independently-`SUBSCRIBED` deployments of this exact same codebase now
> exist — BAP-X (`bap-backend`, the original instance) and BAP-Y (`bap-y-backend`,
> new) — each its own container, database, and signing identity, both spanning all 3
> domains (differentiated purely by env file, `BAP/backend/.env` vs `.env.y`, never
> by forked code), representing two competing companies per the client's own
> requirement.

> **Implementation note (livetracker7.md §4, 2026-08-22, supersedes the "not yet done"
> line above):** each instance now has its own real, distinct brand — **OnSlot**
> (BAP-X, coral `#e11d48`, unchanged) and **GoFetch** (BAP-Y, green `#16a34a`) — own
> name, color, tagline, and real designer-delivered icon/favicon set, selected at
> build time via a `NEXT_PUBLIC_BRAND_ID` Docker build arg into a per-instance
> `lib/brand.ts` config (`BAP/web`), the same "one codebase, config-selected" pattern
> as the backend. BAP also got a real landing page for the first time in this same
> phase (previously only PWA/icon assets had ever touched `BAP/web/app/page.tsx`).

## 2. Business Responsibilities / Capabilities

**Buyer Management**
- Buyer Onboarding
- Buyer Profile Management
- Buyer Lifecycle Management
- Buyer Configuration Management

**Discovery Management**
- Service Discovery
- Catalog Management

**Buyer Transaction Management**
- Select
- Init
- Confirm
- Status
- Track
- Update
- Cancel
- Rating
- Support
- Payment

**Note:**
- Registry verification and payment handling occur within the respective business workflows.
- Inventory and resource management are handled by the BPP, while the BAP consumes provider catalog, inventory availability, and booking status through the Beckn Protocol.

## 3. Implementation Modules

### 1. Buyer Management Module
- Buyer Onboarding Module
- Buyer Profile Management Module
- Buyer Lifecycle Management Module
- Buyer Configuration Management Module

### 2. Discovery Module
- Search Module

**Responsibilities**
- Search Request Processing
- Search Response Processing (on_search)

### 3. Buyer Transaction Module
- Select Module
- Init Module
- Confirm Module
- Payment Module
- Status Module
- Track Module
- Update Module
- Cancel Module
- Rating Module
- Support Module

**Note**
- The Discovery Module orchestrates the Beckn discovery workflow by processing buyer search requests, communicating with the Beckn Gateway, and processing the corresponding on_search responses received from Beckn Provider Platforms (BPPs).
- Registry communication is handled through the Registry Client Service, which internally uses the HTTP Client Service.

> **Implementation note (2026-07-31, `livetracker4.md` §1.1/§1.4):** the Select/Init/Confirm/Status/Cancel/Update/Track/Rating/Support modules above dispatch directly to the resolved BPP's own `subscriber_url` (via `get_bpp_client()`, with a fresh Registry `SUBSCRIBED` re-check on every call — `resolve_subscribed_bpp()`) — **not** through the Beckn Gateway. Only the Discovery Module's `/search` still routes through Gateway; that's the one action the real protocol actually requires it for (`protocol_compliance_notes_v1.1.md` §P). §5's "Communication Participants" list below still names Beckn Gateway because `/search` genuinely uses it, not because every module does.

> **Implementation note (2026-09-25, `livetracker5.md` Phase 1–2):** the Payment Module above is real, not the earlier placeholder (`NOT_YET_IMPLEMENTED`) — `POST /api/v1/payment` (trigger, real CSRF-protected per the audit-identified exception documented in `SECURITY.md`) + `GET /api/v1/payment/<transaction_id>` (result poll), following the same async trigger/result-poll shape every other module here uses. The charge amount is always read server-side from `SearchSession.confirmed_order`, never accepted from the client (Design Principle 2). Vendor: Razorpay (Phase 0.1), via a vendor-agnostic `shared/payment_gateway` interface — a second vendor can be added without touching this module's own code. Hosted-checkout only (Design Principle 4): a successful trigger creates a Razorpay Order, not an immediate charge; real resolution arrives via a signature-verified webhook (`POST /on_payment/razorpay`), which is why `PENDING` is a normal, honest in-progress status here, distinct from `FAILED`. **Verification status, stated honestly:** the adapter itself (`RazorpayAdapter` — charge/refund/webhook-verification/get_status, retry-safety, circuit-breaker wiring) passes 28/28 tests against Razorpay's real documented API shapes. The BAP-side webhook receipt service/view (`record_webhook_event`, `razorpay_webhook_view`) pass all 9 of their own tests too — a local Docker/Redis outage blocked running them earlier in development; resolved, then actually run, not assumed. What remains unverified is only what genuinely requires live credentials: a real sandbox charge and a real signed webhook, cross-checked against Razorpay's own dashboard. No real sandbox credentials exist in this codebase (Rule 4) — `core/apps.py`'s `ready()` hook skips adapter registration gracefully when unconfigured.

> **Implementation note (2026-09-25, `livetracker5.md` Phase 3):** the Payment Module's customer-facing flow is now fully built — `BAP/web/app/payment/page.tsx` opens Razorpay's real hosted Checkout widget (never trusting its client-side success callback directly, per Design Principle 5 — only the server-verified webhook resolves a payment) and reaches the customer's payment status in real time over a new WebSocket channel: `core/consumers.py::PaymentStatusConsumer` + `core/realtime.py::broadcast_payment_status_changed()`, the first business-logic WebSocket consumer BAP itself has needed (mirrors BPP's own already-proven `core/consumers.py` pattern exactly, including its `CHANNEL_LAYERS` config, absent from BAP before this phase). Polling remains the fallback if the socket ever disconnects. `payment_trigger_view` now also carries `@idempotent_view()`, the web-layer double-submit guard distinct from the vendor-call-level idempotency key Phase 1 already built. **Verification status:** 27 backend tests + 9 frontend tests pass, including a real (not simulated) WebSocket round-trip test over `channels.testing.WebsocketCommunicator`. What remains genuinely unverified is the same as Phase 2 — a live sandbox charge and webhook end-to-end.

## 4. Data Storage & Persistence

| Storage Type | Technology |
|---|---|
| Primary Database | PostgreSQL |
| Media Storage | File Storage / Object Storage |
| Cache Storage | Redis |

## 5. Communication Participants
The BAP communicates with:
1. BAP Web Application (UI)
2. Registry
3. Beckn Gateway
4. BPP Backend Server Application
5. Payment Gateway

## 6. Communication Mechanism

| Communication Between | Communication Protocol | API Style | Communication Pattern | Data Format |
|---|---|---|---|---|
| BAP Web Application ↔ BAP Backend Server Application | HTTP/HTTPS, WebSockets | RESTful APIs, Bidirectional Full-Duplex | Synchronous (Request → Response), Asynchronous (Event-Driven) | JSON |
| BAP Backend → Registry (`/subscribe`, `/lookup`) | HTTP/HTTPS | RESTful APIs | Synchronous (Request → Response), BAP-initiated | JSON |
| Registry → BAP Backend (`/on_subscribe`) | HTTP/HTTPS | RESTful APIs | Synchronous, but **registry-initiated** (reverse direction — see [protocol_compliance_notes_v1.1.md](../protocol_compliance_notes_v1.1.md) §A.1) | JSON |
| BAP Backend Server Application ↔ Beckn Gateway | HTTP/HTTPS | RESTful APIs | Asynchronous (Request → ACK/NACK → Callback Response) | JSON |
| BAP Backend Server Application ↔ BPP Backend Server Application | HTTP/HTTPS | RESTful APIs | Asynchronous (Request → ACK/NACK → Callback Response) | JSON |
| BAP Backend Server Application ↔ Payment Gateway | HTTP/HTTPS | RESTful APIs | Synchronous (Request → Response) | JSON |

> **Implementation note:** the Registry also performs domain-ownership verification during Subscribe by issuing a direct, unauthenticated `GET` to the BAP's own `ondc-site-verification.html` (served by the BAP itself, distinct from the JSON `/on_subscribe` callback above) and validating the signed content before accepting the submitted key.

> **Implementation note (2026-09-06, `SECURITY.md`/`RUNBOOK.md` §"BAP received the identical rotation fix"):** BAP's signing/encryption key rotation (`onboarding_rotate_keys <domain-code>`) is now automated on the same 90-day cadence as Registry/Gateway/BPP, wired into the shared `key-rotation-scheduler` sidecar for both `bap-backend` and `bap-y-backend`. Same generate-in-memory/submit-first/persist-only-after-confirmed design as the rest of the network. See `SECURITY.md` for the full fix history, including a real signature-expiry-during-retry finding and a pre-existing Registry/disk key-drift recovery.

## 7. Framework / Programming Language

| Item | Technology |
|---|---|
| Programming Language | Python |
| Backend Framework | Django |

## 8. Architecture Model

| Item | Selection |
|---|---|
| Architecture Model | Modular Monolith |

## 9. Internal Processing Architecture
- Internal Architecture Style: Event-Driven Architecture (EDA)

**Scope:**
- Applied inside the BAP.
- Used for communication between internal business modules.
- External communication remains Beckn Protocol compliant.

## 10. Shared Utility Services
1. Cryptography Service
2. Validation Service
3. Registry Client Service
4. HTTP Client Service
5. Configuration Service
6. Logging Service
7. Authentication & Authorization Service
8. Cache Service (Redis)

## 11. Frontend Technology Stack

| Item | Technology |
|---|---|
| Frontend Framework | Next.js |
| Programming Language | TypeScript |
| Styling Framework | Tailwind CSS |
