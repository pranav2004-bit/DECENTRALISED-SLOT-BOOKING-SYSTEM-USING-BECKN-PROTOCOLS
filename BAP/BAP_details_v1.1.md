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
