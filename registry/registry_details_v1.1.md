# Registry Application

**Protocols to Follow in implementation:** Beckn Protocol Specification

> **Implementation note (added post-verification, original brief unchanged above/below):** technical specifics referenced generically in this brief (exact API endpoints, key formats, status values) have been verified against the official Beckn/ONDC specification and reference implementations — see [protocol_compliance_notes_v1.1.md](../protocol_compliance_notes_v1.1.md) for the confirmed, sourced detail. Two callouts relevant to this document specifically:
> - "Cryptographic key management" (§2, §3.3, §12 below) involves **two distinct key pairs** per participant — a signing key pair (Ed25519) and a separate encryption key pair (X25519) — not one.
> - "Lifecycle operations (create, update, activate, deactivate)" (§3.2 below) are **not separate API endpoints** in the verified protocol — the confirmed spec has exactly three Registry endpoints (`/subscribe`, `/on_subscribe`, `/lookup`); all lifecycle/status transitions and key rotation happen by calling `/subscribe` again, not via dedicated update/activate/deactivate calls.

## 1. Application Overview
The Registry is the trust and identity component of the Beckn ecosystem. It maintains verified participant identities, cryptographic public keys, and participant metadata required for participant discovery, authentication, and trust verification across the network.

## 2. Business Responsibilities / Capabilities
- Participant onboarding (registration)
- Participant lifecycle management
- Participant identity management
- Cryptographic key management
- Participant verification and trust validation

## 3. Implementation Modules
1. **Participant Registration Module**
   Responsible for registering new participants into the Registry.
2. **Participant Lifecycle & Identity Management Module**
   Responsible for managing participant identities, participant lifecycle operations (create, update, activate, deactivate), and participant verification & trust validation.

   > **Implementation note:** Subscribe now performs real domain-ownership verification — the Registry issues a direct `GET` to the participant's own `ondc-site-verification.html` and validates the signed content before accepting the submitted key, not just accepting keys at face value. `/subscribe` and `/lookup` also enforce real server-side `Authorization` header verification: first-time Subscribe is checked via proof-of-possession of the newly submitted key, while a re-Subscribe (key rotation) is checked against the CURRENTLY REGISTERED key, not the new one. This asymmetry is a deliberate security design choice for this project, not confirmed from an official ONDC source.
3. **Cryptographic Key Management Module**
   Responsible for storing, updating, rotating, and managing participants' public keys. This module also supports participant verification by providing the cryptographic keys required for secure authentication and signature validation.

   > **Implementation note:** the Registry also exposes a `GET /identity` endpoint returning its own public keys, so participants can decrypt `on_subscribe` challenges. This is a fourth endpoint beyond the three-endpoint spec noted above — real ONDC publishes registry keys out-of-band, but this project's network needed an in-band mechanism, so this is a pragmatic, non-spec addition specific to this deployment.

## 4. Who Communicates with the Registry
The following backend systems communicate directly with the Registry:
- BAP Backend
- BPP Backend
- Beckn Gateway Backend

**Note:** Human users never communicate directly with the Registry. All communication is performed through backend applications.

## 5. What is Communicated
The following information is exchanged with the Registry:
- Participant registration information
- Participant identity information
- Participant metadata
- Public key information
- Participant verification requests
- Participant lookup requests
- Trust-related metadata

The Registry does **not** exchange business data such as catalogs, inventory, slots, orders, payments, or customer information.

## 6. Communication Mechanism

| Communication Between | Communication Protocol | API Style | Communication Pattern | Data Format |
|---|---|---|---|---|
| BAP Backend → Registry (`/subscribe`, `/lookup`) | HTTP/HTTPS | RESTful APIs | Synchronous (Request → Response), participant-initiated | JSON |
| Registry → BAP Backend (`/on_subscribe`) | HTTP/HTTPS | RESTful APIs | Synchronous, but **registry-initiated** — reverse direction from the row above; Registry calls the participant's callback URL with a challenge and awaits an immediate answer | JSON |
| BPP Backend → Registry (`/subscribe`, `/lookup`) | HTTP/HTTPS | RESTful APIs | Synchronous (Request → Response), participant-initiated | JSON |
| Registry → BPP Backend (`/on_subscribe`) | HTTP/HTTPS | RESTful APIs | Synchronous, registry-initiated (reverse direction) | JSON |
| Beckn Gateway Backend → Registry (`/subscribe`, `/lookup`) | HTTP/HTTPS | RESTful APIs | Synchronous (Request → Response), participant-initiated | JSON |
| Registry → Beckn Gateway Backend (`/on_subscribe`) | HTTP/HTTPS | RESTful APIs | Synchronous, registry-initiated (reverse direction) | JSON |

> Corrected from the original brief's single row per participant, which implied all Registry traffic is participant-initiated. Per [protocol_compliance_notes_v1.1.md](../protocol_compliance_notes_v1.1.md) §A.1, `/on_subscribe` is the one exception — the Registry calls the participant, not the other way around.

## 7. Data Flow

| Incoming Data Flow | Outgoing Data Flow |
|---|---|
| Beckn Gateway Backend → Registry | Registry → Beckn Gateway Backend |
| BAP Backend → Registry | Registry → BAP Backend |
| BPP Backend → Registry | Registry → BPP Backend |

The Registry is a passive component. Participants push registration and update information to the Registry, while other participants query the Registry for participant lookup and verification.

## 8. Data Ownership
The Registry stores only the minimum participant information required to perform its responsibilities.

Examples include:
- Participant identity
- Subscriber information
- Network identifiers
- Domain information
- Public keys
- Verification status
- Registry metadata

The Registry does **not** store:
- Catalogs
- Inventory
- Slots
- Orders
- Payments
- Customer information
- Provider business information

## 9. Database

| Database Type | Relational Database |
|---|---|
| Database Technology | PostgreSQL |

## 10. Framework / Programming Language

| Item | Technology |
|---|---|
| Programming Language | Python |
| Backend Framework | Django |

**Note:** Project Architectural Decision.

## 11. Architecture Model

| Item | Selection |
|---|---|
| Architecture Model | Modular Monolith |

## 12. Shared Utility Services
The Registry contains the following shared utility services, which provide reusable technical capabilities across all business modules.
- Cryptography Service
- Validation Service
- Configuration Service
- Logging Service

**Note:** These shared utility services provide common technical capabilities that are reused across all Registry business modules. They are not business modules themselves but support the Registry's core operations through centralized, reusable functionality.
