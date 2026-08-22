# Beckn Provider Platform (BPP)

**Protocol to Follow in implementation:** Beckn Protocol Specification

> **Implementation note (added post-verification, original brief unchanged above/below):** see [protocol_compliance_notes_v1.1.md](../protocol_compliance_notes_v1.1.md) for confirmed technical detail — Registry endpoint contracts, dual key-pair model, and request-signing format behind "Cryptography Service" and "Registry Client Service" (§10 below). One item specific to "Supported Service Domains" above: this project's own domain taxonomy for **Healthcare** and **Automotive** service-booking uses the real ONDC `domain` code naming as a reference/design source only (see [livetracker1.md](../livetracker1.md)'s scope declaration — this project is a private network and does not engage ONDC's actual domain-onboarding process). Beauty maps reasonably to the real ONDC Beauty & Personal Care domain code's naming pattern; Healthcare and Automotive were mapped to the nearest adjacent pattern for this project's own Registry (tracked in livetracker1.md Phase 3.2).

## 1. Application Overview
The Beckn Provider Platform (BPP) is the provider-side application of the Beckn ecosystem. It implements the Beckn Protocol by exposing provider services, managing provider operations, maintaining catalogs, inventory, resources, and fulfillment, and processing Beckn transaction requests received from Buyer App Platforms (BAPs).

**Supported Service Domains**
- Healthcare
- Automotive
- Beauty

> **Implementation note (livetracker7.md §1.1, 2026-08-21):** which of these three
> domains a *given deployed instance* actually serves is now a real, enforced
> setting — `SUPPORTED_DOMAINS` (`BPP/backend/bpp/settings.py`), env-driven,
> defaulting to all three (today's single combined instance, unchanged). Every
> `/action` entry point (`core/domain_scope.py`) checks the incoming request's
> `context.domain` against it and returns a real NACK for a mismatch — a second,
> independent enforcement layer alongside Registry's per-domain `Participant`
> subscriptions and Gateway's own domain-filtered dispatch, so a single-domain
> deployment (e.g. a future Healthcare-only instance) genuinely refuses an
> out-of-scope request that reaches it directly, not merely one that Gateway
> never happened to route to it.

> **Implementation note (livetracker7.md §2, 2026-08-22):** "a future Healthcare-only
> instance" above is no longer future — three independently-identified, independently-
> `SUBSCRIBED` deployments of this exact same codebase now exist: BPP-Beauty
> (`bpp-backend`, the original instance, narrowed to `ONDC:RET13` only),
> BPP-Medical (`bpp-medical-backend`, `ONDC:SRV13` only), and BPP-Automotive
> (`bpp-automotive-backend`, `BECKN:AUTO01` only) — each its own container, database,
> and signing identity, differentiated purely by env file (`BPP/backend/.env`,
> `.env.medical`, `.env.automotive`), never by forked code.

> **Implementation note (livetracker7.md §4, 2026-08-22, supersedes the "not yet done"
> line above):** each of the 3 instances now has its own real, distinct brand —
> **StyleNest** (BPP-Beauty, amber `#d97706`, unchanged), **CareNest** (BPP-Medical,
> teal `#0d9488`), **AutoCare** (BPP-Automotive, blue `#2563eb`) — own name, color,
> tagline, and real designer-delivered icon/favicon set, selected at build time via a
> `NEXT_PUBLIC_BRAND_ID` Docker build arg into a per-instance `lib/brand.ts` config
> (`BPP/web`), the same "one codebase, config-selected" pattern as the backend.

## 2. Business Responsibilities / Capabilities
- Provider Management
- Provider Discovery Management
- Provider Transaction Management
- Inventory & Resource Management
- Fulfillment Management

## 3. Implementation Modules

### 1. Provider Management Module
Contains the following individual modules:
- Provider Onboarding Module
- Provider Profile Management Module
- Provider Lifecycle Management Module
- Provider Configuration Management Module

**Note:** The Provider Onboarding Module internally handles provider registration and provider verification as part of the onboarding workflow.

### 2. Provider Discovery Module
Contains the following individual module:
- Catalog Module

**Responsibilities**
- Search Request Processing
- Search Response Management (on_search)

**Note:** The Provider Discovery Module orchestrates the Beckn discovery workflow by processing incoming search requests, retrieving catalog information from the Catalog Module, and generating the corresponding on_search response.

### 3. Provider Transaction Module
Contains the following individual workflow modules:
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

**Note:** The Provider Transaction Module acts as a parent module that groups all Beckn transaction workflow modules.

> **Implementation note (2026-07-31, `livetracker4.md` §1.2/§1.4):** every module above receives its request directly from the BAP and sends its own `on_X` callback directly back to `context["bap_uri"]` (via `get_bap_client()`) — **not** through the Beckn Gateway relay. Only the Provider Discovery Module's `/search`/`on_search` still round-trips through Gateway, per the real protocol's own scope (`protocol_compliance_notes_v1.1.md` §P).

### 4. Inventory & Resource Management Module
Contains the following individual modules:
- Inventory Module
- Availability Module
- Slot Module
- Resource Module
- Capacity Module

### 5. Fulfillment Management Module
Contains the following individual modules:
- Booking Module
- Order Module
- Order Lifecycle Module
- Fulfillment Module

## 4. Data Storage & Persistence

| Storage Component | Technology |
|---|---|
| Database | PostgreSQL |
| Media Storage | File Storage / Object Storage |
| Cache | Redis |

## 5. Communication Participants
The Beckn Provider Platform (BPP) communicates directly with the following components:
- Provider Web Application
- Registry
- Beckn Gateway
- Buyer App Platform (BAP) Backend

**Note:** Human users do not communicate directly with the BPP Backend. All communication is performed through the Provider Web Application or other backend applications.

## 6. External Communication Mechanism

| Communication Between | Communication Protocol | API Style | Communication Pattern | Data Format |
|---|---|---|---|---|
| BPP Backend ↔ Provider Web Application | HTTP/HTTPS + WebSockets | RESTful APIs | Synchronous (HTTP) + Full-Duplex Real-Time Communication (WebSockets) | JSON |
| BPP Backend → Registry (`/subscribe`, `/lookup`) | HTTP/HTTPS | RESTful APIs | Synchronous (Request → Response), BPP-initiated | JSON |
| Registry → BPP Backend (`/on_subscribe`) | HTTP/HTTPS | RESTful APIs | Synchronous, but **registry-initiated** (reverse direction — see [protocol_compliance_notes_v1.1.md](../protocol_compliance_notes_v1.1.md) §A.1) | JSON |
| BPP Backend ↔ Beckn Gateway | HTTP/HTTPS | RESTful APIs | Asynchronous (Request → ACK/NACK → Callback Response) | JSON |
| BPP Backend ↔ BAP Backend | HTTP/HTTPS | RESTful APIs | Asynchronous (Request → ACK/NACK → Callback Response) | JSON |

> **Implementation note:** the Registry also performs domain-ownership verification during Subscribe by issuing a direct, unauthenticated `GET` to the BPP's own `ondc-site-verification.html` (served by the BPP itself, distinct from the JSON `/on_subscribe` callback above) and validating the signed content before accepting the submitted key.

## 7. Internal Communication Mechanism

| Communication Between | Communication Mechanism |
|---|---|
| Business Module ↔ Business Module | Direct Service Invocation (Synchronous) |
| Business Module ↔ Business Module | Domain Events (Asynchronous / Event-Driven Architecture) |

**Note:** Internal communication between business modules is performed using Direct Service Invocation for synchronous operations and Domain Events (Event-Driven Architecture) for asynchronous operations where loose coupling, scalability, and event propagation are required. This communication remains entirely within the Modular Monolithic Architecture and is independent of the external Beckn Protocol communication.

## 8. Framework / Programming Language

| Item | Technology |
|---|---|
| Programming Language | Python |
| Backend Framework | Django |

**Note:** Project Architectural Decision.

## 9. Architecture Model

| Item | Selection |
|---|---|
| Architecture Model | Modular Monolithic Architecture |

**Note:** Event-Driven Architecture is used internally where appropriate for communication between business modules. External communication with Beckn participants continues to follow the Beckn Protocol over HTTP/HTTPS.

## 10. Shared Utility Services (SUS)
The Beckn Provider Platform (BPP) uses the following shared utility services across multiple business modules:
1. Authentication Service
2. Authorization Service
3. Validation Service
4. Cryptography Service
5. Registry Client Service
6. HTTP Client Service
7. Configuration Service
8. Logging Service

## 11. BPP Web Application (UI)

| Item | Technology |
|---|---|
| Frontend Framework | Next.js |
| Programming Language | TypeScript |
| UI Framework | Tailwind CSS |

**Note:** Project Architectural Decision.
