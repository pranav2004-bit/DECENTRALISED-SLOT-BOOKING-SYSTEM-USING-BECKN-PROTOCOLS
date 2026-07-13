# Beckn Provider Platform (BPP)

**Protocol to Follow in implementation:** Beckn Protocol Specification

> **Implementation note (added post-verification, original brief unchanged above/below):** see [protocol_compliance_notes_v1.1.md](../protocol_compliance_notes_v1.1.md) for confirmed technical detail — Registry endpoint contracts, dual key-pair model, and request-signing format behind "Cryptography Service" and "Registry Client Service" (§10 below). One open item specific to "Supported Service Domains" above: exact ONDC `domain` codes for **Healthcare** and **Automotive** service-booking (as opposed to retail-of-goods) are not yet confirmed — Beauty maps reasonably to an existing ONDC Beauty & Personal Care domain code, but Healthcare and Automotive may require mapping to an adjacent domain or engaging ONDC's domain-onboarding process. Do not assume a domain code without confirming it first (tracked in livetracker1.md Phase 3.2).

## 1. Application Overview
The Beckn Provider Platform (BPP) is the provider-side application of the Beckn ecosystem. It implements the Beckn Protocol by exposing provider services, managing provider operations, maintaining catalogs, inventory, resources, and fulfillment, and processing Beckn transaction requests received from Buyer App Platforms (BAPs).

**Supported Service Domains**
- Healthcare
- Automotive
- Beauty

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
