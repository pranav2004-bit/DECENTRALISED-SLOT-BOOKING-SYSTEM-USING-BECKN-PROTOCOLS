# Beckn Gateway Application

**Protocol to Follow in implementation:** Beckn Protocol Specification

> **Implementation note (added post-verification, original brief unchanged above/below):** see [protocol_compliance_notes_v1.1.md](../protocol_compliance_notes_v1.1.md) for confirmed technical detail behind this brief. One callout specific to this document: when the Gateway signs its own outbound calls (§6 below, "Communication Mechanism"), it uses a **`Proxy-Authorization`** header, not `Authorization` — a distinct header from the signing convention BAP/BPP/Registry use with each other. Signing middleware built for participant-to-participant calls should not be reused unmodified for Gateway-originated calls.

## 1. Application Overview
The Beckn Gateway is the network discovery component of the Beckn ecosystem. It facilitates participant discovery by routing discovery (search) requests from Buyer App Platforms (BAPs) to relevant Beckn Provider Platforms (BPPs) and routing discovery (on_search) responses back to the requesting BAP.

## 2. Business Responsibilities / Capabilities
- Search Request Routing
- Search Response Routing

## 3. Implementation Modules

### 1. Search Request Routing Module
**Responsibilities**
- Receive search requests from BAPs.
- Validate incoming search requests.
- Communicate with the Registry (or use trusted locally cached Registry data) to obtain participant identity and public key information required for trust validation.
- Discover the appropriate BPPs.
- Route / Multicast the search requests to the discovered BPPs.

### 2. Search Response Routing Module
**Responsibilities**
- Receive on_search responses from BPPs.
- Validate incoming on_search responses.
- Communicate with the Registry (or use trusted locally cached Registry data) to obtain participant identity and public key information required for trust validation.
- Route the on_search responses back to the requesting BAP.

## 4. Data Persistence

| Storage Component | Status / Technology |
|---|---|
| Database | Not Required |
| Media Storage | Not Required |
| Cache | Optional |

The Gateway may maintain trusted locally cached Registry information (such as participant identity and public keys) to optimize trust validation and reduce Registry lookups. The Registry remains the authoritative source of this information.

## 5. Who Communicates with the Gateway
- BAP Backend
- BPP Backend
- Registry

**Note:** Human users and frontend applications do not communicate directly with the Gateway.

## 6. Communication Mechanism

| Communication Between | Communication Protocol | API Style | Communication Pattern | Data Format |
|---|---|---|---|---|
| Beckn Gateway Backend ↔ Registry | HTTP/HTTPS | RESTful APIs | Synchronous (Request → Response) | JSON |
| Beckn Gateway Backend ↔ BAP Backend | HTTP/HTTPS | RESTful APIs | Asynchronous (Request → ACK/NACK → Callback Response) | JSON |
| Beckn Gateway Backend ↔ BPP Backend | HTTP/HTTPS | RESTful APIs | Asynchronous (Request → ACK/NACK → Callback Response) | JSON |

## 7. Framework / Programming Language

| Item | Technology |
|---|---|
| Programming Language | Python |
| Backend Framework | Django |

**Note:** Project Architectural Decision.

## 8. Architecture Model

| Item | Selection |
|---|---|
| Architecture Model | Modular Monolith |

## 9. Shared Utility Services
The Gateway contains the following shared utility services, which provide reusable technical capabilities across all business modules.
- Cryptography Service
- Validation Service
- Registry Client Service
- HTTP Client Service
- Configuration Service
- Logging Service
- Cache Service
