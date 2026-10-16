MVP SOLUTION DESIGN DOCUMENT

# Document Control

### Document Metadata

| Field | Value |
| --- | --- |
| Author(s) |  |
| Date |  |
| Status | v2.0 — Reference-Grade GCP & ADK 2.0 Solution Design (Ready for Architecture Sign-Off) |
| Target Audience | Enterprise Architecture, HR Technology, ITSM & AI Governance Teams |

### Revision History

| Version | Date | Author | Description of Change |
| --- | --- | --- | --- |
| 0.1 |  |  | v2.0 Reference-Grade GCP & ADK 2.0 Solution Design (Vertex AI Agent Engine, ADK 2.0, Gemini 3.5 Flash, GCP Agent Gateway, Agent Registry, Concurrent Model Armor + Cloud DLP, HITL Resumability & 31-Row Traceability Matrix) |
|  |  |  |  |

## 1. Executive Summary & Scope Boundaries

### 1.1. Business Overview & Context

Enterprise Human Resources (HR) and Information Technology (IT) service desks currently face high volumes of repetitive Tier-1 inquiries and manual, fragmented self-service workflows. Employees seeking answers to corporate policies (e.g., bereavement leave, remote work equipment allowances, relocation benefits) or attempting routine transactions (checking accrued PTO, submitting leave requests, updating personal contact details, or opening/tracking IT support tickets) must navigate multiple disparate portals—specifically the corporate **Policy Repository**, **WorkWeek (HCM)**, and **ServiceImmediately (ITSM/HRSD)**. Complex life and work events—such as onboarding remote equipment (`UC-2.1`), initiating short-term medical leave (`UC-2.2`), or relocating to a new office (`UC-2.3`)—require employees to manually correlate policy rules with sequential updates across both WorkWeek and ServiceImmediately, resulting in high administrative friction, inconsistent compliance, and elevated helpdesk ticket volumes.

The **Enterprise HR Agentic Solution (MVP 1)** addresses these challenges by introducing a unified, zero-trust conversational AI assistant built natively on the **Google Cloud Gemini Enterprise Agent Platform**: **Vertex AI Agent Engine (Agent Runtime)**, the **Google Agent Development Kit (ADK 2.0)**, **Gemini 3.5 Flash (`gemini-3.5-flash`)**, **Google Cloud Agent Gateway**, **Google Cloud Agent Registry**, **Vertex AI Search**, **Vertex AI Model Armor**, and **Cloud Data Loss Prevention (Sensitive Data Protection)**.

- **Deflect Tier-1 HR & IT Inquiries (`BRD Obj 1`):** Reduce routine HR and IT helpdesk ticket volume by **>= 40% within the first 6 months** by automating responses to common policy and ticket status queries (`UC-1.1`, `UC-1.3`).
- **Streamline Self-Service Transactions (`BRD Obj 2`):** Enable employees to execute core read/write self-service operations in **WorkWeek (HCM)** and **ServiceImmediately (ITSM/HRSD)** conversationally with built-in Human-in-the-Loop (HITL) confirmation (`UC-1.2`, `UC-1.3`).
- **Validate Cross-System Orchestration (`BRD Obj 3`):** Prove deterministic multi-step chaining across Policy Documents, WorkWeek, and ServiceImmediately (`UC-2.1`, `UC-2.2`, `UC-2.3`) with asynchronous parallel read fan-out (`NFR-2.3`) and automated Saga failure compensation (`NFR-4.3`).
- **Enhance Employee Experience (`BRD Obj 4`):** Deliver a single conversational entry point with `< 10.0s` time-to-first-response (`NFR-2.1`), natural language typo/synonym robustness (`FR-2.1`), and isolated multi-turn context (`FR-2.2`).
- **Ensure 100% Enterprise AI Governance (`BRD Obj 5`, `FR-1.1`, `FR-1.2`):** Maintain complete visibility over agent ownership, version lineage, and strict OpenAPI tool allowlists via **Google Cloud Agent Registry** (`google_agent_registry_service`), **Agent Gateway** (`CLIENT_TO_AGENT` and `AGENT_TO_ANYWHERE`), and **Agent Runtime Identity** (`--agent-identity`).
- **Mitigate AI & Privacy Risks (`BRD Obj 6`, `FR-1.3`, `FR-1.4`, `NFR-1.1`):** Achieve **0 policy violations and 0 data leaks** by intercepting and validating all user inputs and model outputs concurrently in **< 220ms p95** (`< 300ms` SLA ceiling, `NFR-2.1`) via **Vertex AI Model Armor**, **Semantic Governance Policies (SGP)**, and **Cloud DLP**.

### 1.2. Scope Boundaries

To prevent scope creep during MVP 1 delivery and maintain 100% alignment with the Business Requirements Document ([SS HR Agentic Solution BRD](https://docs.google.com/document/d/1cG9-VYQ6ZyzelAZQ3owdbI63woCqTgdi6DBI3qQO_-Y/edit?resourcekey=0-KFzd6xP_wVJHKSxtHEr3pw&tab=t.tpcr3esq94y)), the explicit system boundaries are defined below.


| **Domain / Dimension** | **In-Scope for MVP 1 (`BRD Sec 2.1 & 2.2`)** | **Out-of-Scope for MVP 1 (`BRD Sec 2.3 & Sec 6`)** |
| --- | --- | --- |
| **User Interface & Modality** | Standard web-based conversational chat UI (React/SSE) or enterprise chat client integration supporting text-based multi-turn dialog (`FR-2.1`, `FR-2.2`) and HITL confirmation cards. | Voice/telephony interactions (`run_live`), mobile native apps, and multi-lingual capabilities (English-only for MVP 1). |
| **Knowledge Base (Policy Q&A)** | Curated static HR policy documents (PDF/Markdown/Text) covering Leave Policies, Expense Guidelines, Remote Work Policy, Relocation, and Code of Conduct (`FR-5.1`–`FR-5.5`). | Unstructured emails, Slack/Chat threads, external web search (`google_search`), or non-HR/IT repositories. |
| **WorkWeek (HCM) Data & Actions** | **Read (`FR-3.2`, `FR-3.4`):** Live fetch of *Employee Profile* (Employee ID, Name, Email, Department, Role, Manager, Hire Date), *Contact Info* (Personal Address, Personal Phone Number), and *Leave Balances* (Accrued, Used, Remaining for `Vacation` and `Sick`). **Write (`FR-3.2`, `FR-3.3`):** Updating personal address/phone; submitting `Vacation`/`Sick` leave requests. | Payroll processing, compensation/salary details, tax withholding forms, performance reviews, and organizational hierarchy changes. |
| **ServiceImmediately (ITSM/HRSD)** | **Read (`FR-4.2`):** Querying *Incident Record* (Ticket ID, Short Description, Detailed Description, Category, Priority, State/Status, Assignee, Comments/Notes timeline). **Write (`FR-4.1`–`FR-4.3`):** Creating incidents (`1 - Critical` to `4 - Low`), posting comments, and updating ticket status (e.g., to `Resolved` or `Closed`). | Change management CAB approvals, CMDB hardware asset discovery, problem management root-cause workflows, and automated software installation. |
| **Cross-System Orchestration** | Chained 3-system execution across Policy Repo, WorkWeek, and ServiceImmediately for Equipment Procurement (`UC-2.1`), Medical Leave (`UC-2.2`), and Office Relocation (`UC-2.3`). | Arbitrary user-defined workflow scripting or integrations with external ERP/Finance systems (e.g., SAP, Coupa, Workday Financials). |
| **Identity, Auth & Tenancy** | **Single-tenant GCP environment** using functional test credentials in **Secret Manager** paired with composite employee-scoped tokens (`FR-3.1`) and verified automation origin headers (`FR-1.2`, `FR-4.1`). | Live enterprise IdP integration (Active Directory, Okta, corporate SSO) and multi-tenant isolation (`BRD Section 6`). |

### 1.3. Target Architecture Overview

The MVP 1 Target Architecture implements a cloud-native, defense-in-depth **Hierarchical Multi-Agent Pattern** on Google Cloud Platform. Every user turn traverses a governed **Agent Gateway & Concurrent AI Safety Perimeter** before reaching the **Vertex AI Agent Engine (Agent Runtime)** hosting **Google ADK 2.0** and **Gemini 3.5 Flash (`gemini-3.5-flash`)**.

*Figure 1.1: High-Level System Architecture for Enterprise HR Agentic Solution (MVP 1)*


#### Core Architectural Layers & Google Cloud Components

1. **Layer 1 — Client & Governed Ingress Layer:**
    1. **Conversational Web UI / Chat Client:** React/TypeScript web frontend establishing low-latency Server-Sent Events (`/run_sse`) streaming connections.
    1. **Cloud Load Balancing & Cloud Armor WAF:** Terminates TLS 1.3, enforces DDoS protection, per-user rate limiting, and OWASP Top-10 WAF rules.
    1. **Cloud Run Session Coordinator & JWT Minter:** Validates the caller's test session and mints a cryptographically signed **Composite Employee Context Token** (`X-Delegated-Employee-Token`, `FR-3.1`) binding the authenticated `employee_id`.
    1. **Google Cloud Agent Gateway (Ingress — `CLIENT_TO_AGENT`):** Centralizes inbound access control, identity verification, and governed routing into **Vertex AI Agent Engine**.
1. **Layer 2 — Concurrent AI Safety, Privacy & Semantic Governance Perimeter (`< 220ms` p95 / `< 300ms` SLA):**
    1. **ADK App-Level `SecurityGovernancePlugin` (`BasePlugin`):** Registered on the root ADK `App` so it executes prior to every model call and tool invocation across all agents in the hierarchy—making safety bypasses architecturally impossible.
    1. **Concurrent Input/Output Scanning (`asyncio.gather`):** Executes **Vertex AI Model Armor** (`sanitizeUserPrompt` / `sanitizeModelResponse` for prompt injection, jailbreak, toxicity, malicious URI, and off-topic detection, `FR-1.3`, `NFR-1.1`) **in parallel** with **Cloud DLP / Sensitive Data Protection** (`content.deidentify` for SSN, credit card, passport, IBAN, and medical diagnosis redaction, `FR-1.4`). Running both checks concurrently bounds per-pass latency to `max(~110ms, ~40ms) = ~110ms` (`~220ms` combined input + output overhead, well under the `< 300ms` budget in `NFR-2.1`).
    1. **Semantic Governance Policies (SGP):** Platform-level policy enforcement ensuring tool calls remain strictly aligned with user intent and enterprise boundaries (`FR-1.1`).
1. **Layer 3 — Agentic Orchestration Layer (Vertex AI Agent Engine + Google ADK 2.0):**
    1. **`hr_supervisor_agent` (`Gemini 3.5 Flash`, `temp=0.0`):** Deployed on managed **Vertex AI Agent Engine** with native **Agent Identity (`--agent-identity`)**. Parses multi-turn intent (`FR-2.1`), coordinates parallel cross-system reads (`NFR-2.3`), enforces Human-in-the-Loop (HITL) confirmations via ADK `ResumabilityConfig(is_resumable=True)`, and manages Saga compensation (`NFR-4.3`).
    1. **Native `VertexAiSessionService` + `temp:` State Zero-Caching (`FR-2.2`, `FR-3.4`):** Persists sanitized multi-turn dialog history per `employee_id` + `session_id` with automatic token-based compaction (`EventsCompactionConfig(token_threshold=32000, event_retention_size=5)`). All real-time WorkWeek employee profile and PTO balance payloads are written exclusively to ADK's invocation-scoped **`tool_context.state["temp:workweek_profile"]`** and **`tool_context.state["temp:pto_balances"]`**, which ADK automatically purges upon turn completion—guaranteeing zero dynamic HCM caching (`FR-3.4`).
    1. **Specialized Domain Sub-Agents (`Gemini 3.5 Flash`, `temp=0.0`):**
        1. **`policy_qa_agent` (`mode="task"`):** Isolated sub-agent wrapping **`VertexAiSearchTool`** (`FR-5.1`–`FR-5.4`). Isolating `VertexAiSearchTool` in its own sub-agent prevents ADK's known conflict where mixing built-in grounding tools with custom `FunctionTool`s disables Automatic Function Calling (AFC).
        1. **`workweek_hcm_agent`:** Executes live HCM reads and HITL-confirmed writes via OpenAPI-typed ADK `FunctionTool`s (`FR-3.1`–`FR-3.4`).
        1. **`service_immediately_agent`:** Executes ticket queries, incident creation, comments, and lifecycle transitions via OpenAPI-typed ADK `FunctionTool`s (`FR-4.1`–`FR-4.3`).
1. **Layer 4 — Governed Egress & Deterministic Tool Proxy Layer:**
    1. **Google Cloud Agent Gateway (Egress — `AGENT_TO_ANYWHERE`):** Inspects and governs all outbound agent-to-tool traffic (`--agent-gateway-egress`).
    1. **WorkWeek & ServiceImmediately Cloud Run Tool Proxies:** Stateless microservices enforcing deterministic, code-level business guardrails (`FR-3.3` balance/date/format rules; `FR-4.3` state-machine transitions, 24h cosine-similarity `>= 0.85` duplicate suppression, and priority validation), exponential backoff retries (`NFR-4.2`), and verified automation origin headers (`FR-1.2`, `FR-4.1`) using credentials from **Cloud Secret Manager**.
    1. **Vertex AI Search (Enterprise Edition) & Cloud Storage:** Hosts the chunked HR Policy corpus (`500-token` layout-aware chunks) with **Eventarc** incremental sync (`<= 15 min`, `FR-5.5`).
1. **Layer 5 — Privacy-Safe Observability, Audit & Fleet Governance Layer:**
    1. **Google Cloud Agent Registry (`google_agent_registry_service`):** Tracks agent ownership, deployment versions, and approved tool specs (`FR-1.1`).
    1. **`BigQueryAgentAnalyticsPlugin` & Cloud Audit Logs (`NFR-1.2`):** Streams 100% of allowed/blocked turns, tool calls, safety verdicts, and automation origin metadata into an immutable **BigQuery** compliance dataset.
    1. **Privacy-Safe OpenTelemetry & Cloud Trace (`FR-1.4`):** Configured with `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT` and `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false` so distributed latency spans are captured without leaking raw user PII into trace attributes.

### 1.4. Alternatives Considered


| **Architectural Dimension** | **Selected GCP Design (MVP 1)** | **Alternative A** | **Alternative B** | **Trade-Off Analysis & Architectural Rationale** |
| --- | --- | --- | --- | --- |
| **Foundation Model** | **Gemini 3.5 Flash (`gemini-3.5-flash`)** on Vertex AI | Gemini Pro / Ultra Tier | Self-Hosted Open-Weight LLM on GKE (vLLM / Llama) | **Gemini 3.5 Flash** provides sub-second time-to-first-token (`~450ms`), easily meeting the `< 10.0s` total response SLA (`NFR-2.1`) even across multi-step tool chains (`UC-2.x`), while delivering high tool-calling precision at \~70% lower token cost than Pro models and zero GPU cluster management overhead. |
| **Agent Orchestration & Runtime** | **Google ADK 2.0 on Vertex AI Agent Engine (`agent_runtime`)** | LangGraph on Self-Managed GKE Autopilot | Dialogflow CX Deterministic State Machines | **ADK 2.0 on Agent Engine** provides native `VertexAiSessionService`, `BasePlugin` global guardrails, `ParallelAgent` fan-out, HITL `ResumabilityConfig`, Agent Identity, and Agent Gateway bindings out of the box. Dialogflow CX is too rigid for dynamic 3-system Saga chaining (`UC-2.x`), while GKE adds unnecessary cluster operations for MVP 1. |
| **Session & Zero-Caching State (`FR-2.2`, `FR-3.4`)** | **Native `VertexAiSessionService` + ADK `temp:` State Prefix** | Custom Firestore / Redis Cache Middleware | Stateless Client-Side History Passing | Passing history from the client violates zero-trust integrity. Custom Redis/Firestore caches risk accidental retention of sensitive HCM data. **Native `VertexAiSessionService`** paired with ADK's `state["temp:*"]` prefix deterministically purges WorkWeek profile/PTO data at the end of every turn (`FR-3.4`). |
| **Policy RAG Engine (`FR-5.1`–`FR-5.5`)** | **Vertex AI Search (Enterprise Edition + Layout Parser)** | Custom AlloyDB `pgvector` RAG Pipeline | Full-Corpus Long-Context Prompt Stuffing | **Vertex AI Search** provides managed Document AI Layout Parser chunking, hybrid semantic + BM25 retrieval, native extractive grounding scores (`>= 0.75`), and automatic passage/section deep-link metadata (`FR-5.3`). Full-corpus stuffing violates `< 10s` latency (`NFR-2.1`) and FinOps budgets. |
| **AI Safety & SPII Redaction (`FR-1.3`, `FR-1.4`, `NFR-2.1`)** | **Concurrent Vertex AI Model Armor + Cloud DLP (`asyncio.gather`) + SGP** | Secondary LLM-as-a-Judge Guardrail Call | Regex-Only Custom Input Filters | Secondary LLM judge calls add `1,000–2,500ms` per turn, breaching the strict `< 300ms` safety scanning SLA (`NFR-2.1`). Regex filters fail on semantic prompt injections. Running **Model Armor + Cloud DLP concurrently** completes in **`~110ms` per pass (`~220ms` round-trip)**. |
| **Tool Governance & Backend Connectivity (`FR-1.1`, `FR-3.3`, `FR-4.3`)** | **Agent Gateway + Cloud Run OpenAPI Tool Proxies + Agent Registry** | Direct In-Agent Python HTTP Calls to Backends | Unmediated 3rd-Party MCP Servers | Direct in-agent HTTP calls couple LLM prompts to raw backend schemas and weaken RBAC isolation. **Agent Gateway + Cloud Run Tool Proxies** enforce deterministic code-level guardrails (balance, dates, 24h dedupe, state transitions) and attach Secret Manager credentials outside the LLM process. |

## 2. Production-Ready Future State Design

While MVP 1 operates in a single-tenant environment using functional test credentials (`BRD Section 6`), the architecture is designed so that promoting MVP 1 to an enterprise-wide, multi-region production deployment requires **zero re-architecture of the core ADK agent graph**.

*Figure 2.1: Production-Ready Multi-Region Future State Architecture*


### 2.1. Architectural Evolution from MVP 1 to Enterprise Production


| **Capability Dimension** | **MVP 1 Implementation (Single-Tenant Pilot)** | **Enterprise Production Future-State Design** |
| --- | --- | --- |
| **Authentication & End-User Identity (`FR-1.2`, `FR-1.5`, `FR-3.1`)** | Functional test credentials stored in **Secret Manager** + Session Gateway-minted Composite Employee JWT scoping requests to the test `employee_id`. | **Enterprise SSO & Workforce Identity Federation:** Full OIDC/SAML integration with corporate **Okta / Active Directory**. End-user tokens undergo **OAuth 2.0 Token Exchange (RFC 8693)** at **Apigee X + Agent Gateway** to mint short-lived, cryptographically bound On-Behalf-Of (OBO) tokens paired with **Agent Runtime Identity mTLS** (`--agent-identity`). |
| **Omnichannel Publishing & UI (`FR-2.1`)** | Standalone React/SSE Web Chat UI + REST/SSE API endpoint. | **Gemini Enterprise Native Registration & A2UI:** Published directly into **Gemini Enterprise** via `agents-cli publish gemini --registration-type adk` (native `:streamQuery` invocation), plus **A2A Protocol** federation and declarative **A2UI** interactive approval cards in Google Chat / Microsoft Teams. |
| **High Availability & Scale (`NFR-2.2 99.9%` SLA)** | Single-region deployment (`us-central1`) on managed serverless services (`min_instances = 1` on Agent Engine and Cloud Run during UAT). | **Multi-Region Active-Active Deployment:** Global External Application Load Balancer routing across `us-central1` and `us-east4` (plus `europe-west1` for EU), **Gemini 3.5 Flash Provisioned Throughput (PT)** with automated cross-region spillover/failover, and multi-region **Cloud Spanner / VertexAiSessionService**. |
| **Network Isolation & Zero-Trust Perimeter** | IAM-authenticated Cloud Run services, **Agent Gateway** ingress/egress, and internal VPC connector. | **VPC Service Controls (VPC-SC) & Private Service Connect Interface (PSC-I):** All Vertex AI, Cloud DLP, Secret Manager, and BigQuery APIs enclosed inside an enforced **VPC-SC perimeter**; Agent Engine connects privately to enterprise networks via `--network-attachment` (**PSC-I**) and `--dns-peering-domain` to reach WorkWeek and ServiceImmediately over **Cloud Interconnect** with zero public internet traversal. |
| **Cross-System Saga Orchestration (`NFR-2.3`, `NFR-4.3`)** | In-process ADK Supervisor orchestration with parallel read fan-out, structured Saga state logging, and synchronous/HITL compensating actions. | **Durable Event-Driven Saga Engine:** Integration with **Cloud Workflows** and **Cloud Pub/Sub** Dead-Letter Queues (DLQ) for asynchronous retry orchestration across prolonged backend maintenance windows, with automated push notifications upon completion. |
| **Policy Knowledge Base Sync (`FR-5.5`)** | Cloud Storage `OBJECT_FINALIZE` Eventarc trigger into Vertex AI Search (`<= 15 min` incremental index sync). | **Live Enterprise CMS Connectors & Document ACLs:** Native **Vertex AI Search** connectors for SharePoint / Google Drive / Confluence with real-time webhook ingestion and document-level ACL inheritance (filtering policies by employee country, band, and union status). |
| **Multi-Tenancy, Data Residency & CMEK (`NFR-1.3`)** | Single-tenant GCP project (`BRD Section 6` constraint) with Google-managed encryption. | **Tenant-Isolated Regional Data Planes & CMEK:** Regional deployments (`us`, `eu`, `asia`) enforcing GDPR and local labor law residency, tenant-scoped Vertex AI Search data stores, and **Customer-Managed Encryption Keys (`--key` Cloud KMS CMEK)** across Agent Runtime, Cloud Storage, and BigQuery. |

## 3. System Flows, Sequence Diagrams & Agent Design

### 3.1. End-to-End Request Lifecycle & Pre/Post-Processing Latency Budget (NFR-2.1, NFR-2.3)

Every conversational turn executes a deterministic 6-stage pipeline engineered to keep total safety scanning overhead **< 300ms** (`~220ms` p95) and time-to-first-response **< 10.0 seconds** (`~1.8s` for single-domain queries; `~3.8s` for 3-system orchestration):

1. **Stage 1 — Governed Ingress & Session Resolution (`~15ms`):** The employee's message passes through **Cloud Armor WAF**, the **Cloud Run Session Coordinator** (which attaches the signed Composite Employee Context JWT), and the **Inbound Agent Gateway (`CLIENT_TO_AGENT`)** into **Vertex AI Agent Engine**, loading sanitized conversation history from **`VertexAiSessionService`** (`FR-2.2`).
1. **Stage 2 — Concurrent Pre-Inference Input Guardrail & SPII Redaction (`~110ms` p95):**
    1. The ADK App-level **`SecurityGovernancePlugin.before_model_callback`** invokes **Vertex AI Model Armor (`sanitizeUserPrompt`)** and **Cloud DLP (`content.deidentify`) concurrently via `asyncio.gather()`**.
    1. **Model Armor** checks for Prompt Injection, Jailbreaks, Malicious URLs, Toxicity/Harassment, and Off-Topic domain violations (`FR-1.3`, `FR-5.4`, `NFR-1.1`).
    1. **Cloud DLP** masks SPII (`US_SOCIAL_SECURITY_NUMBER`, `CREDIT_CARD_NUMBER`, `PASSPORT`, `IBAN_CODE`, `MEDICAL_RECORD_NUMBER`, and sensitive health descriptors) into deterministic tokens (`[REDACTED_SSN]`) before any text enters LLM context or logs (`FR-1.4`).
    1. If Model Armor flags a violation, the plugin short-circuits the turn immediately, logs `SECURITY_BLOCK_INPUT` via **`BigQueryAgentAnalyticsPlugin`** (`NFR-1.2`), and returns a standardized refusal.
1. **Stage 3 — Intent Classification & Parallel Task Planning (`~550–750ms`): `hr_supervisor_agent`** (**Gemini 3.5 Flash**, `temperature=0.0`, backed by **Vertex AI Context Caching**) resolves multi-turn coreferences (`FR-2.1`, `FR-2.2`) and selects either a single domain sub-agent (`UC-1.1`–`UC-1.3`) or a parallel-read + sequential-write cross-system plan (`UC-2.1`–`UC-2.3`).
1. **Stage 4 — Bounded Sub-Agent & Deterministic Tool Execution (`~800–2,400ms`):**
    1. For cross-system flows (`UC-2.1`–`UC-2.3`), **`policy_qa_agent`** and **`workweek_hcm_agent` (read)** execute **concurrently** (`NFR-2.3`), cutting read latency nearly in half.
    1. Outbound tool calls traverse the **Outbound Agent Gateway (`AGENT_TO_ANYWHERE`)** to the **Cloud Run Tool Proxies**, which validate business guardrails (`FR-3.3`, `FR-4.3`), enforce HITL confirmation (`require_confirmation=True`) for state-changing writes, and store live WorkWeek payloads exclusively in **`tool_context.state["temp:*"]`** (`FR-3.4`).
1. **Stage 5 — Grounded Synthesis & Concurrent Output Guardrail (`~110ms` p95): Gemini 3.5 Flash** synthesizes the final response with mandatory clickable citations (`FR-5.3`). **`SecurityGovernancePlugin.after_model_callback`** runs **Model Armor (`sanitizeModelResponse`)** and **Cloud DLP** concurrently (`~110ms`) alongside a deterministic citation/grounding verifier (`score >= 0.75`, `FR-5.2`).
1. **Stage 6 — SSE Streaming Delivery, `temp:` State Purge & Async Audit (`Non-Blocking`):** ADK purges all `temp:*` HCM keys before saving session state (`FR-3.4`), streams the response over SSE to the employee UI, and asynchronously flushes OpenTelemetry spans (`NO_CONTENT`) to **Cloud Trace** and structured audit records to **BigQuery** and **Cloud Audit Logs** (`FR-1.2`, `NFR-1.2`, `NFR-2.3`).

#### End-to-End Millisecond Latency Budget (NFR-2.1 Verification)


| **Pipeline Stage** | **Execution Mode** | **Single-Domain p50 / p95 (`UC-1.x`)** | **Cross-System 3-Step p50 / p95 (`UC-2.x`)** | **SLA / Requirement Mapping** |
| --- | --- | --- | --- | --- |
| **1. Ingress, JWT Mint & Session Load** | Synchronous (In-Region) | `12ms` / `25ms` | `12ms` / `25ms` | `FR-2.2`, `FR-3.1` |
| **2. Input Guardrail (Model Armor + DLP)** | **Concurrent (`asyncio.gather`)** | **`85ms` / `110ms`** | **`85ms` / `110ms`** | **`NFR-2.1` (Part 1 of `< 300ms` Safety Budget)** |
| **3. Supervisor Routing (`Gemini 3.5 Flash`)** | Context-Cached Inference | `480ms` / `700ms` | `550ms` / `800ms` | `FR-2.1`, `NFR-2.1` |
| **4a. Read Phase (Policy RAG / WorkWeek Read)** | **Parallel Fan-Out (`NFR-2.3`)** | `650ms` / `1,100ms` | `750ms` / `1,250ms` (Parallel) | `FR-3.4`, `FR-5.2`, `NFR-2.3` |
| **4b. Write Phase (WorkWeek / ServiceImmediately)** | Sequential + Guardrail Proxy | `0ms` – `900ms` | `1,100ms` / `1,950ms` | `FR-3.3`, `FR-4.3`, `NFR-4.2` |
| **5. Output Synthesis + Output Guardrail** | **Concurrent (`asyncio.gather`)** | `450ms` + **`90ms` / `110ms`** | `550ms` + **`90ms` / `110ms`** | **`NFR-2.1` (Part 2 of `< 300ms` Safety Budget)** |
| **Total Safety Scanning Overhead (Stage 2 + 5)** | **Parallelized Pre + Post Scan** | **`175ms` p50 / `220ms` p95** | **`175ms` p50 / `220ms` p95** | **PASS (`< 300ms` Hard Ceiling — `NFR-2.1`)** |
| **Total End-to-End Turn Latency** | **SSE Streaming to First Token / Full** | **`1.77s` p50 / `2.85s` p95** | **`3.14s` p50 / `4.80s` p95** | **PASS (`< 10.0s` Hard Ceiling — `NFR-2.1`)** |

*Figure 3.1: Single-Domain Request, Concurrent Guardrails & HITL Confirmation Sequence Diagram (UC-1.1, UC-1.2, UC-1.3)*


*Figure 3.2: Cross-System Parallel Read, Transactional Write & Saga Compensation Sequence Diagram (UC-2.1, UC-2.2, UC-2.3)*


### 3.2. Hierarchical Multi-Agent Topology & ADK 2.0 Implementation Patterns

The solution implements a 4-agent hierarchy using **Google ADK 2.0** on **Vertex AI Agent Engine**.


| **Agent Identifier** | **ADK Class & Mode** | **Model & Config** | **Authorized Tool Allowlist (`FR-1.1`)** | **Enforced Guardrails, Callbacks & State Rules** |
| --- | --- | --- | --- | --- |
| **`hr_supervisor_agent`** | `LlmAgent` (Root Coordinator) | `gemini-3.5-flash` (`temp=0.0`, `top_p=0.95`, Context Cached) | Sub-agent delegation only: `policy_qa_agent` (`mode="task"`), `workweek_hcm_agent`, `service_immediately_agent`. Zero direct external HTTP tools. | App-level `SecurityGovernancePlugin` (`Model Armor` + `Cloud DLP`), `BigQueryAgentAnalyticsPlugin`, `EventsCompactionConfig(token_threshold=32000)`, Saga state tracking. |
| **`policy_qa_agent`** | `LlmAgent` (`mode="task"`, `output_schema=PolicyGroundedAnswer`) | `gemini-3.5-flash` (`temp=0.0`, Grounding `>= 0.75`) | **`VertexAiSearchTool`** bound exclusively to `projects/{P}/locations/global/collections/default_collection/dataStores/hr-policies-mvp1`. | Isolated in its own `task` sub-agent so `VertexAiSearchTool` never conflicts with custom `FunctionTool` Automatic Function Calling (AFC). Enforces `grounding_score >= 0.75` (`FR-5.2`) and clickable `[Title - Section](url)` citations (`FR-5.3`). |
| **`workweek_hcm_agent`** | `LlmAgent` (Domain Specialist) | `gemini-3.5-flash` (`temp=0.0`) | `get_employee_profile`, `get_pto_balances`, `update_contact_info` (`require_confirmation=True`), `submit_leave_request` (`require_confirmation=True`). | `before_tool_callback`: Injects `employee_id` from `tool_context.state["auth:employee_id"]` (never from LLM arguments, `FR-1.5`, `FR-3.1`). `after_tool_callback`: Writes dynamic profile/balances only to `state["temp:*"]` (`FR-3.4`). |
| **`service_immediately_agent`** | `LlmAgent` (Domain Specialist) | `gemini-3.5-flash` (`temp=0.0`) | `get_ticket_details`, `create_incident_ticket`, `post_ticket_comment`, `update_ticket_status` (`require_confirmation=True`). | `before_tool_callback`: Injects `X-Automation-Origin: hr-agentic-mvp1` (`FR-4.1`), enforces caller ticket ownership (`FR-1.5`), 24h duplicate check (`>= 0.85` similarity), priority verification, and state transition matrix (`FR-4.3`). |

#### Reference ADK 2.0 Application & Plugin Wiring (app/agent.py)

```
import asyncio
from google.adk.agents import Agent
from google.adk.apps import App, ResumabilityConfig
from google.adk.apps.app import EventsCompactionConfig
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools import FunctionTool, VertexAiSearchTool
class SecurityGovernancePlugin(BasePlugin):
    """App-wide plugin executing Model Armor + Cloud DLP concurrently (<220ms round-trip)."""
    async def before_model_callback(self, *, callback_context, llm_request):
        armor_res, dlp_res = await asyncio.gather(
            model_armor_client.sanitize_user_prompt(llm_request),
            dlp_client.deidentify_content(llm_request),
        )
        if armor_res.blocked:
            callback_context.state["temp:security_block"] = armor_res.reason
            return armor_res.to_safe_refusal_response()
        llm_request.replace_sanitized_text(dlp_res.redacted_text)
        return None
    async def after_agent_callback(self, *, callback_context):
        # FR-3.4 Deterministic Zero-Caching: purge all dynamic WorkWeek keys before session save
        for k in list(callback_context.state.keys()):
            if k.startswith("temp:"):
                del callback_context.state[k]
        return None
policy_qa_agent = Agent(
    name="policy_qa_agent",
    model="gemini-3.5-flash",
    mode="task",
    description="Answers HR policy, leave, expense, remote work, and relocation questions with citations.",
    instruction="Answer strictly from Vertex AI Search results. Refuse if grounding < 0.75. Always cite [Doc Title - Section](url).",
    tools=[VertexAiSearchTool(data_store_id=HR_POLICY_DATASTORE_ID)],
)
workweek_hcm_agent = Agent(
    name="workweek_hcm_agent",
    model="gemini-3.5-flash",
    description="Executes live WorkWeek employee profile reads, PTO balance queries, contact updates, and leave requests.",
    tools=[
        FunctionTool(get_employee_profile),
        FunctionTool(get_pto_balances),
        FunctionTool(update_contact_info, require_confirmation=True),
        FunctionTool(submit_leave_request, require_confirmation=True),
    ],
)
service_immediately_agent = Agent(
    name="service_immediately_agent",
    model="gemini-3.5-flash",
    description="Manages ServiceImmediately support tickets, comments, and status transitions.",
    tools=[
        FunctionTool(get_ticket_details),
        FunctionTool(create_incident_ticket),
        FunctionTool(post_ticket_comment),
        FunctionTool(update_ticket_status, require_confirmation=True),
    ],
)
hr_supervisor_agent = Agent(
    name="hr_supervisor_agent",
    model="gemini-3.5-flash",
    instruction="Coordinate HR Policy, WorkWeek HCM, and ServiceImmediately tasks. Execute independent reads in parallel.",
    sub_agents=[policy_qa_agent, workweek_hcm_agent, service_immediately_agent],
)
app = App(
    name="app",  # Must match 'app/' directory for agents-cli eval & Agent Runtime
    root_agent=hr_supervisor_agent,
    plugins=[SecurityGovernancePlugin(), ReflectAndRetryToolPlugin(), BigQueryAgentAnalyticsPlugin()],
    resumability_config=ResumabilityConfig(is_resumable=True),
    context_cache_config=ContextCacheConfig(min_tokens=1024, ttl_seconds=1800, cache_intervals=10),
    events_compaction_config=EventsCompactionConfig(token_threshold=32000, event_retention_size=5),
)
```
### 3.3. Detailed Execution Flows for All 6 MVP 1 Use Cases (UC-1.1 – UC-2.3)

- **`UC-1.1` — Policy Q&A (e.g., Bereavement Leave / Noise-Canceling Headphones Expense):**
    - `hr_supervisor_agent` delegates a structured task to `policy_qa_agent`.
    - `policy_qa_agent` queries `VertexAiSearchTool` over the static HR Policy Data Store (`FR-5.1`), retrieves top-ranked 500-token layout chunks, verifies extractive grounding confidence `>= 0.75` (`FR-5.2`, `FR-5.4`), and returns a grounded response with clickable section citations (`[Expense Policy - Section 3.4](https://...)`, `FR-5.3`).
- **`UC-1.2` — HR Self-Service in WorkWeek (PTO Balance Lookup & Leave Submission):**
    - For balance inquiries, `workweek_hcm_agent` calls `get_pto_balances` using the Gateway-minted Composite Employee Token (`FR-3.1`) with `Cache-Control: no-store` (`FR-3.4`), storing results in `state["temp:pto_balances"]`.
    - For time-off submission (*"Submit a time-off request for this coming Thursday and Friday"*), `workweek_hcm_agent` validates chronological dates (`start_date >= today`, `start_date <= end_date`) and verifies `requested_days <= remaining_balance` (`FR-3.3`), triggers ADK's HITL confirmation prompt, and dispatches `POST /api/v1/employees/{emp_id}/leaves/requests`.
- **`UC-1.3` — IT Incident Management in ServiceImmediately (Ticket Status & VPN Ticket Creation):**
    - For status checks (`INC123456`), `service_immediately_agent` calls `get_ticket_details`, verifying `ticket.caller_id == authenticated_employee_id` (`FR-1.5`), and summarizes status, priority, assignee, and comment history (`FR-4.2`).
    - For VPN ticket creation, the ServiceImmediately Tool Proxy performs a 24-hour sliding-window duplicate scan (`cosine_similarity < 0.85`), validates priority alignment (`3 - Moderate`), and injects `X-Automation-Origin: hr-agentic-mvp1` (`FR-4.1`, `FR-4.3`).
- **`UC-2.1` — Cross-System: Remote Work Equipment Procurement:**
    - **Parallel Read Fan-Out (`NFR-2.3`):** `hr_supervisor_agent` concurrently invokes `policy_qa_agent` (to verify Home Office Monitor eligibility and stipend rules in the *Remote Work Policy*, `FR-5.2`, `FR-5.3`) **and** `workweek_hcm_agent.get_employee_profile` (live fetch of the employee's remote work status and verified home shipping address into `state["temp:workweek_profile"]`, `FR-3.4`).
    - **Guarded Write:** Once both parallel branches succeed and confirm remote eligibility, `hr_supervisor_agent` invokes `service_immediately_agent.create_incident_ticket` (`category="Hardware Procurement"`, `priority="4 - Low"`, shipping address from live WorkWeek profile, and `created_by_automation=true`, `FR-4.1`).
- **`UC-2.2` — Cross-System: Short-Term Medical Leave Setup:**
    - **Parallel Read Fan-Out (`NFR-2.3`):** Concurrently retrieves the *Short-Term Medical Leave Policy* procedure/notice requirements (`policy_qa_agent`) **and** the employee's live Sick/Medical leave balance and assigned Manager ID (`workweek_hcm_agent.get_employee_profile` + `get_pto_balances`, `FR-3.4`).
    - **Step 2 (WorkWeek Leave Submission):** After HITL confirmation and balance/date guardrail verification (`FR-3.3`), `workweek_hcm_agent` submits the Leave of Absence in WorkWeek and records `workweek_leave_id` in the Saga log.
    - **Step 3 (ServiceImmediately Email Routing Ticket + Saga Protection):** `service_immediately_agent` creates an IT ticket to route the employee's email access to their verified manager (`FR-4.1`). If Step 3 fails after 3 exponential-backoff retries (`NFR-4.2`), Saga compensation (`NFR-4.3`) preserves the critical medical leave, logs `SAGA_PARTIAL_FAILURE`, and provides a one-click retry or manual reference code.
- **`UC-2.3` — Cross-System: Office Relocation (London Transfer):**
    - **Parallel Read Fan-Out (`NFR-2.3`):** Concurrently retrieves the *Global Mobility & Relocation Policy* allowance limits for London (`policy_qa_agent`) **and** the employee's current profile (`workweek_hcm_agent`).
    - **Step 2 (WorkWeek Address Update):** Prompts the employee for their new London address (if not yet provided), validates postal and E.164 phone syntax (`FR-3.3`), obtains HITL confirmation, and executes `update_contact_info` in WorkWeek.
    - **Step 3 (Facilities Badge Ticket):** `service_immediately_agent` opens a Facilities ticket (`category="Facilities / Building Access"`, `priority="3 - Moderate"`, `created_by_automation=true`) to provision London office badge access.

## 4. Security, Governance & Identity

The security architecture enforces zero-trust principles across every layer, mapping directly to `FR-1.1`–`FR-1.5` and `NFR-1.1`–`NFR-1.3`.

### 4.1. Capability, Fleet Registry & Lifecycle Governance (FR-1.1)

- **Google Cloud Agent Registry (`google_agent_registry_service`):** The deployed Agent Engine resource and its Tool Proxy services are registered in **Google Cloud Agent Registry**, providing a centralized governance catalog that records `owner_team`, `git_commit_sha`, `model_version="gemini-3.5-flash"`, and the cryptographically hashed `toolspec.json` / OpenAPI 3.0 manifest.
- **Strict Tool Allowlisting & Egress Governance:** Each ADK sub-agent is statically compiled with only its domain-specific `FunctionTool`s or `VertexAiSearchTool`. Dynamic code execution (`BuiltInCodeExecutor`), arbitrary web browsing (`load_web_page`), and unverified external endpoints are disabled in code and blocked at the network layer by the **Outbound Agent Gateway (`governedAccessPath=AGENT_TO_ANYWHERE`)**.
- **Semantic Governance Policies (SGP):** Platform-level Semantic Governance rules inspect tool call intents prior to execution, ensuring no tool can be invoked outside its declared business purpose.

### 4.2. Authentication Boundaries, RBAC & Request Origin Verification (FR-1.2, FR-1.5, FR-3.1, FR-4.1)

1. **MVP 1 Functional Credential Isolation (`BRD Section 6`):** External corporate SSO (Okta/Active Directory) is excluded from MVP 1. Backend API credentials for WorkWeek and ServiceImmediately are stored in **Google Cloud Secret Manager** and injected at deploy time (`agents-cli deploy --secrets`) exclusively into the isolated Cloud Run Tool Proxy service accounts (`sa-workweek-proxy`, `sa-si-proxy`) with `roles/secretmanager.secretAccessor`.
1. **Delegated Employee-Scoped Authorization & IDOR Prevention (`FR-1.5`, `FR-3.1`):**
    1. To make Horizontal Privilege Escalation (Cross-User Data Access) impossible even under sophisticated prompt injection, **`employee_id` is completely omitted from all LLM-visible tool parameter schemas**.
    1. Instead, the Cloud Run Session Coordinator mints a short-lived (`15-minute` TTL), HMAC/RSA-signed **Composite Employee Context JWT (`X-Delegated-Employee-Token`)** containing `sub: <authenticated_employee_id>`, `session_id`, and `iat`/`exp`.
    1. ADK's `header_provider` forwards this composite token to the Tool Proxies, which cryptographically verify the signature and scope all WorkWeek (`/api/v1/employees/{sub}/...`) and ServiceImmediately (`caller_id == sub`) operations strictly to the authenticated caller.
1. **Verified Automation Origin Attribution (`FR-1.2`, `FR-4.1`):**
    1. **Vertex AI Agent Engine** runs with **Agent Identity (`--agent-identity`)** enabled.
    1. Every downstream request from the Tool Proxies to WorkWeek and ServiceImmediately injects immutable machine-attribution headers and payload fields:
        1. HTTP Headers: `X-Automation-Origin: google-vertex-agent-engine/hr-agentic-mvp1`, `X-Automation-Actor-SA: sa-hr-agent-proxy@<project>.iam.gserviceaccount.com`, `X-On-Behalf-Of-Employee: <employee_id>`.
        1. ServiceImmediately Audit Payload (`FR-4.1`): `"created_by_automation": true`, `"u_automation_source": "hr-agentic-mvp1"`, `"u_agent_version_sha": "<git_sha>"`, `"u_initiating_employee_id": "<employee_id>"`.

### 4.3. Conversation Safety & Concurrent Guardrail Architecture (FR-1.3, NFR-1.1, NFR-2.1)


| **Guardrail Layer** | **Enforcement Component** | **Security & Policy Checks Performed** | **Latency Budget** | **Block / Remediation Action** |
| --- | --- | --- | --- | --- |
| **1. Pre-Inference Input Safety Scan** | **Vertex AI Model Armor** (`sanitizeUserPrompt` via `SecurityGovernancePlugin`) | Prompt Injection, Jailbreak attacks, Malicious URLs, CSAM, Hate Speech, Harassment, Sexually Explicit, and Dangerous Content (`FR-1.3`, `NFR-1.1`). | **`<= 110ms`** (Concurrent with DLP) | Halt execution before LLM call; emit `SECURITY_BLOCK_INPUT` to BigQuery & Cloud Audit Logs (`NFR-1.2`); return standardized refusal. |
| **2. Pre-Inference SPII Redaction** | **Cloud DLP / Sensitive Data Protection** (`content.deidentify`) | Inspects & masks `US_SOCIAL_SECURITY_NUMBER`, `CREDIT_CARD_NUMBER`, `PASSPORT`, `IBAN_CODE`, `MEDICAL_RECORD_NUMBER`, and health condition spans (`FR-1.4`). | **`<= 40ms`** (Hidden inside `110ms` parallel window) | Replace sensitive spans with deterministic tokens (`[REDACTED_SSN]`) prior to LLM prompt construction, session storage, and logging. |
| **3. Domain Containment Gate** | **Model Armor Topic Filter + Supervisor System Instruction** | Rejects off-topic prompts outside HR policies, WorkWeek, and ServiceImmediately (e.g., general coding, math, trivia, political/financial advice per `FR-5.4`). | `0ms` extra (Part of Input Scan + Router) | Return polite boundary notice: *"I can only assist with corporate HR policies, WorkWeek self-service, and ServiceImmediately support tickets."* |
| **4. Pre-Tool Semantic & Business Gate** | **Semantic Governance (SGP) + ADK `before_tool_callback` + Tool Proxy** | Verifies tool allowlist (`FR-1.1`), composite token RBAC (`FR-1.5`), WorkWeek balance/date/format rules (`FR-3.3`), and ServiceImmediately dedupe/priority/state rules (`FR-4.3`). | `<= 25ms` | Block invalid API call; return structured validation message to agent to guide user correction. |
| **5. Post-Inference Output Scan & Grounding** | **Vertex AI Model Armor** (`sanitizeModelResponse`) + **Cloud DLP** + **Grounding Verifier** | Scans model output for toxicity, accidental SPII egress, unverified URLs (`FR-5.4`), and verifies Policy Q&A extractive grounding score `>= 0.75` (`FR-5.2`). | **`<= 110ms`** (Concurrent with DLP) | Suppress unsafe/ungrounded output; log `SECURITY_BLOCK_OUTPUT`; return safe fallback message. |

### 4.4. Sensitive Data Handling, Privacy-Safe Telemetry & Zero-Caching Policy (FR-1.4, FR-2.2, FR-3.4, NFR-1.3)

- **Deterministic Zero Dynamic HCM Caching (`FR-3.4`):** WorkWeek Tool Proxy responses carry `Cache-Control: no-store, no-cache, must-revalidate`. Inside ADK, `workweek_hcm_agent` writes profile and balance results solely to `tool_context.state["temp:workweek_profile"]` and `tool_context.state["temp:pto_balances"]`. Because ADK never persists `temp:` state keys to `VertexAiSessionService` (and `SecurityGovernancePlugin.after_agent_callback` explicitly purges them), every subsequent query fetches live data directly from WorkWeek.
- **Strict Session Isolation (`FR-2.2`):** `VertexAiSessionService` scopes every conversation thread by `(app_name, user_id=employee_id, session_id)`. Cross-session or cross-employee memory access is prohibited.
- **Privacy-Safe OpenTelemetry Configuration (`FR-1.4`):** To prevent raw prompts or unredacted tool payloads from leaking into distributed trace spans, the Agent Runtime deployment sets `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT` and `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false`. Only Cloud DLP-redacted payloads are written to the KMS-encrypted audit sink.
- **GDPR & Labor Law Compliance (`NFR-1.3`):** All storage, session, and logging resources are pinned to the designated regional boundary (`us-central1` for MVP 1; regional EU/APAC/US planes in production) with AES-256 CMEK support, 30-day session TTLs, and 365-day immutable audit retention.

## 5. Integration Details & Error Handling

### 5.1. Third-Party & Enterprise Tool Integration Methodology (https://mock-saas.aishprabhat.demo.altostrat.com/docs)

In MVP 1, both **WorkWeek (HCM)** and **ServiceImmediately (ITSM/HRSD)** are served by the unified **Mock SaaS Platform** hosted at **`https://mock-saas.aishprabhat.demo.altostrat.com`** (interactive Swagger/MCP specification at **`https://mock-saas.aishprabhat.demo.altostrat.com/docs`** and OpenAPI 3.1.0 contract at **`https://mock-saas.aishprabhat.demo.altostrat.com/openapi.json`**). The upstream service is hosted in GCP project `332638604308` (`org.aishprabhat.altostrat.com`) and fronted by **Google Cloud Identity-Aware Proxy (IAP)** (`x-goog-iap-generated-response: true`) with OAuth2 Client ID / JWT Audience **`332638604308-825bkuk2cjp8h06t1g41o2lvk8oq9cnk.apps.googleusercontent.com`**.

To decouple the LLM sub-agents from upstream transport details and enforce zero-trust security boundaries (`FR-1.1`–`FR-1.5`, `FR-3.3`, `FR-4.3`), all tool calls are mediated through dedicated **Domain MCP / Tool Proxy microservices (`workweek-mcp` and `servicedesk-mcp`)** on Cloud Run behind the **Outbound Agent Gateway**, using a shared **`UpstreamMockSaasClient`** adapter:

- **Multi-Transport Upstream Adapter (`MOCK_SAAS_TRANSPORT_MODE`):**
    - **`mcp_http` (Upstream MCP Streamable HTTP / SSE Mode):** Connects as an MCP client to the upstream MCP server mounted on `https://mock-saas.aishprabhat.demo.altostrat.com` (documented at `/docs`).
    - **`rest_openapi` (Upstream FastAPI OpenAPI 3.1.0 REST/JSON Mode):** Invokes the canonical REST endpoints (`/workweek/*` and `/servicedesk/*`) defined in `https://mock-saas.aishprabhat.demo.altostrat.com/openapi.json` over HTTPS JSON using `httpx.AsyncClient` with connection pooling and deadline propagation.
    - **`local_mock` (Hermetic Cloudtop & CI Loopback Harness):** Invokes a deterministic local Mock SaaS harness (`http://127.0.0.1:18090`, serving `/openapi.json` and SQLite-backed state with chaos fault-injection hooks) so unit, guardrail, and E2E suites run 100% deterministically in CI/CD.
- **IAP OIDC Token Minting & Dual-Header Auth (`MOCK_SAAS_IAP_CLIENT_ID`):** Each proxy mints a Google-signed OIDC ID token from the Cloud Run metadata server with audience `332638604308-825bkuk2cjp8h06t1g41o2lvk8oq9cnk.apps.googleusercontent.com` (cached for 50 minutes of its 60-minute lifetime and auto-refreshed on HTTP `401`). Supports both `Authorization: Bearer <iap-oidc-jwt>` (`iap_header_mode="authorization"`) and `Proxy-Authorization: Bearer <iap-oidc-jwt>` paired with an application-level `Authorization: Bearer <backend_app_token>` from Secret Manager (`iap_header_mode="proxy_authorization"`).
- **Mandatory Outbound Governance Headers:** Every upstream request injects `X-Automation-Origin: hr-agent-mvp1` (`FR-1.2`, `FR-4.1`), `X-User-Context-Assertion: <KMS-signed UCT JWT>` (`FR-3.1`), W3C `traceparent`, `Idempotency-Key: <UUIDv7>` (on all mutating `POST`/`PATCH` calls), and `X-Request-Deadline: <epoch_ms>` (`NFR-4.2`).

#### 5.1.1. WorkWeek (HCM) Integration, Mock SaaS Endpoints & Guardrails (FR-3.1 – FR-3.4)


| **Tool Operation (`workweek-mcp`)** | **Mock SaaS OpenAPI 3.1.0 Endpoint (`https://mock-saas.aishprabhat.demo.altostrat.com`)** | **BRD Data Elements (`BRD Sec 2.2`)** | **Deterministic Pre-Execution Guardrails (`FR-3.3`) & Caching (`FR-3.4`)** |  |
| --- | --- | --- | --- | --- |
| `get_my_profile` (`get_employee_profile`) | `GET /workweek/profile` | `employee_id`, `full_name`, `work_email`, `department`, `job_title`, `manager_id`, `manager_name`, `hire_date`, `work_location_type`, `home_address`, `personal_phone`. | **1. RBAC (`FR-3.1`):** `employee_id` derived strictly from verified UCT JWT `sub` (never accepted as an LLM argument). Excludes `home_address`/`personal_phone` unless `include_contact_details=true`. **2. Zero Cache (`FR-3.4`):** Live fetch (`Cache-Control: no-store`); stored only in `state["temp:workweek_profile"]`. |  |
| `get_my_leave_balances` (`get_pto_balances`) | `GET /workweek/leave-balances` | `VACATION`: `{accrued_days, used_days, pending_days, remaining_days}`; `SICK`: `{accrued_days, used_days, pending_days, remaining_days}`. | **1. RBAC (`FR-3.1`):** Scoped to UCT `sub`. **2. Zero Cache (`FR-3.4`):** Live fetch on every query (`force_live_fetch=True`, `no-store`); stored only in `state["temp:pto_balances"]`. |  |
| `validate_contact_update` & `update_my_contact_info` (`update_contact_info`) | `PATCH /workweek/profile` | Mutable fields only: `home_address` (`street`, `city`, `postal_code`, `country`), `personal_phone`, `email`. | **1. Format Restrictions (`FR-3.3`):** Validates `personal_phone` against E.164 regex (`^\+[1-9][0-9]{6,14}$`), validates `email` against RFC 5322, and verifies non-empty postal address fields. **2. HITL Gate:** Computes SHA-256 `preview_digest` and requires KMS-signed single-use confirmation token (`require_confirmation=True`). |  |
| `validate_leave_request` & `submit_my_leave_request` (`submit_leave_request`) | `POST /workweek/leave-requests` (and `GET /workweek/leave-requests` for `list_my_leave_requests` / overlap check) | Input: `leave_type` (`VACATION` \ | `SICK`), `start_date` (`YYYY-MM-DD`), `end_date` (`YYYY-MM-DD`), `expected_work_days` (`float`). Output: `leave_request_id` (`LR-2026-*`), `status="SUBMITTED"`. | **1. Temporal & Weekday Validity (`FR-3.3`):** Rejects `start_date < today_local` (`START_IN_PAST`), `end_date < start_date` (`END_BEFORE_START`), `> 365d` horizon, and `work_days <= 0` (excluding weekends). **2. Live Balance & Overlap Check (`FR-3.3`):** Re-queries `/workweek/leave-balances` and `/workweek/leave-requests` immediately prior to `POST`; rejects if `work_days > remaining_days` (`INSUFFICIENT_BALANCE`), overlaps existing requests (`OVERLAPPING_LEAVE`), or matches an in-flight write (`IN_FLIGHT_DUPLICATE`). **3. HITL Gate:** Requires explicit confirmation (`require_confirmation=True`). |

#### 5.1.2. ServiceImmediately (ITSM/HRSD) Integration, Mock SaaS Endpoints & Guardrails (FR-4.1 – FR-4.3)


| **Tool Operation (`servicedesk-mcp`)** | **Mock SaaS OpenAPI 3.1.0 Endpoint (`https://mock-saas.aishprabhat.demo.altostrat.com`)** | **BRD Data Elements (`BRD Sec 2.2`)** | **Deterministic Guardrails (`FR-4.3`) & Audit Attribution (`FR-4.1`)** |  |  |  |
| --- | --- | --- | --- | --- | --- | --- |
| `get_my_ticket` & `list_my_open_tickets` (`get_ticket_details`) | `GET /servicedesk/incidents` | `ticket_id` (`^INC[0-9]{6,10}$`), `category`, `short_description`, `detailed_description`, `priority`, `status`, `assignee`, `comments[]`. | **1. Format & RBAC Ownership Check (`FR-1.5`):** Validates `^INC[0-9]{6,10}$` before any backend call (`INVALID_TICKET_ID`); verifies `requester_employee_id == UCT.sub` (`CROSS_USER_ACCESS_DENIED`). **2. Indirect Injection Shield:** Strips untrusted free-text comments into screened `display` payloads. |  |  |  |
| `validate_incident` & `create_incident` (`create_incident_ticket`) | `POST /servicedesk/incidents` | Input: `category`, `short_description` (`1..160 chars`), `detailed_description` (`1..4000 chars`), `priority` (`1 - Critical`..`4 - Low`), `impact` facts. Output: `ticket_id` (`INC*`), `status="New"`. | **1. Duplication Mitigation (`FR-4.3`):** Queries open tickets for `{employee_id}` within a recent sliding window; blocks creation if token-set/embedding similarity `>= 0.80` (`IN_FLIGHT_DUPLICATE`, returning existing `ticket_id`). **2. Priority Decision Matrix (`FR-4.3`, Rules `P-01`..`P-13`):** Evaluates structured `impact` facts (`users_affected`, `can_work`, `workaround_available`, `security_incident_suspected`, `safety_risk`); auto-lowers unsupported `1 - Critical` / `2 - High` requests. **3. Origin & Privacy (`FR-4.1`):** Stamps `automation_origin="hr-agent-mvp1"` and enforces medical-term stripping on `UC-2.2` mailbox delegation tickets. |  |  |  |
| `add_ticket_comment` (`post_ticket_comment`) | `POST /servicedesk/incidents/{ticket_id}/comments` | Input: `ticket_id`, `comment` (`1..4000 chars`), `idempotency_key`. Output: `ticket_id`, `comment_count`, `status`. | Verifies caller ownership (`requester_employee_id == UCT.sub`), rejects comments on `Closed` tickets, runs Cloud DLP SPII redaction on `comment`, and stamps `automation_origin="hr-agent-mvp1"` plus idempotency reference (`FR-4.1`). |  |  |  |
| `validate_ticket_update` & `update_ticket_status` | `PATCH /servicedesk/incidents/{ticket_id}/status` | Input: `ticket_id`, `new_status` (`In Progress` \ | `On Hold` \ | `Resolved` \ | `Closed`), `resolution_notes`. | **Transition Constraints (`FR-4.3`):** Enforces the strict state-machine matrix below (`blocking New -> Closed` with `TRANSITION_NOT_ALLOWED`); requires HITL confirmation (`require_confirmation=True`). |

##### ServiceImmediately Ticket Lifecycle State Transition Matrix (FR-4.3)


| **Current Ticket State** | **Allowed Target State(s)** | **Blocked Target State(s)** | **Enforced Proxy Validation Rule (`FR-4.3`)** |
| --- | --- | --- | --- |
| **`New`** | `In Progress`, `Resolved` (with `resolution_notes`) | **`Closed`** (Direct `New -> Closed` jump is strictly blocked) | Proxy rejects `New -> Closed` with HTTP `422 Unprocessable Entity`: *"Tickets in 'New' status cannot be transitioned directly to 'Closed'. Please transition to 'Resolved' with resolution notes first."* |
| **`In Progress`** | `Resolved` (with `resolution_notes`) | **`New`**, **`Closed`** (Must pass through `Resolved` before `Closed`) | Proxy verifies `resolution_notes` length `>= 10 chars` before allowing transition to `Resolved`. |
| **`Resolved`** | `Closed` (Employee confirms fix) or `In Progress` (Reopen if issue persists) | **`New`** | Transition to `Closed` records final employee sign-off and automation origin timestamp. |
| **`Closed`** | *None (Terminal State)* | **`New`**, **`In Progress`**, **`Resolved`** | Closed tickets are immutable; proxy instructs the agent to offer creating a new linked follow-up incident instead. |

#### 5.1.3. HR Policy Repository & Vertex AI Search RAG Architecture (FR-5.1 – FR-5.5)

- **Document Ingestion & Layout-Aware Chunking (`FR-5.1`):** Approved PDF, Markdown, and Text policy documents stored in **Cloud Storage (`gs://hr-agent-mvp1-policies/`)** are ingested into a **Vertex AI Search** data store configured with **Document AI Layout Parser** (`chunk_size = 500 tokens`, `chunk_overlap = 100 tokens`, `include_ancestor_headings = true`). This preserves table structures, bulleted eligibility lists, and section numbers (`Section 4.2`).
- **Event-Driven Incremental Sync (`<= 15 Minutes`, `FR-5.5`):** A **Cloud Storage `OBJECT_FINALIZE` / `OBJECT_DELETE`** notification triggers an **Eventarc → Cloud Run** ingestion worker that invokes `DiscoveryEngine.ImportDocuments` (`INCREMENTAL` reconciliation mode), guaranteeing that policy updates appear in the Knowledge Base within **<= 15 minutes** (well inside the BRD `[X]` sync window).
- **Strict Grounding, Domain Containment & Clickable Citations (`FR-5.2`–`FR-5.4`):**
    - `policy_qa_agent` queries `VertexAiSearchTool` with extractive segments enabled and enforces a hard grounding attribution threshold of **`>= 0.75`**.
    - If `grounding_score < 0.75` or zero chunks match, the agent deterministically abstains (`FR-5.2`, `FR-5.4`).
    - Every chunk carries verified metadata (`document_title`, `section_id`, `page_number`, `canonical_uri`), which the agent renders as an active, clickable link (`[Leave Policy - Section 2.1: Bereavement Leave](https://...)`, `FR-5.3`). Before rendering, the post-processor verifies that `canonical_uri` belongs to the allowlisted policy repository (`FR-5.4`).

#### 5.1.4. Canonical Data Model, BigQuery Audit/Saga Schema & API Versioning Strategy

##### Entity Definitions, Data Lifecycle & Retention Matrix


| **Entity / Schema Name** | **Storage Plane & Format** | **Key Attributes & Relationships** | **Data Lifecycle, Zero-Caching & Privacy Rules (`FR-1.4`, `FR-3.4`, `NFR-1.3`)** |
| --- | --- | --- | --- |
| **`EmployeeSessionContext`** | **Vertex AI Agent Engine (`VertexAiSessionService`)** | `app_name`, `user_id` (`employee_id`), `session_id`, `events[]` (Cloud DLP-redacted turns), `compacted_summary` (`EventsCompactionConfig` at `32k` tokens). | **Created:** On session start. **Read/Updated:** Every turn (scoped strictly to `user_id`). **Deleted/Archived:** Auto-expired after **30-day TTL** (`NFR-1.3`). Never stores raw SPII or dynamic WorkWeek payloads. |
| **`EphemeralHCMState` (`temp:*`)** | **ADK In-Memory Invocation State (`tool_context.state`)** | `temp:workweek_profile` (`employee_id`, `department`, `role`, `manager_id`, `personal_address`, `personal_phone`), `temp:pto_balances` (`vacation: {accrued, used, remaining}`, `sick: {accrued, used, remaining}`). | **Created:** On live WorkWeek tool fetch (`Cache-Control: no-store`). **Read:** Within the active turn (`UC-1.2`, `UC-2.x`). **Deleted: Purged deterministically at turn end (`0s` persistence)** via ADK `temp:` prefix and `SecurityGovernancePlugin.after_agent_callback` (`FR-3.4`). |
| **`PolicyDocumentChunk`** | **Vertex AI Search Data Store + Cloud Storage (`gs://`)** | `chunk_id`, `document_id`, `document_title`, `section_heading`, `page_number`, `canonical_uri`, `content_text` (`500-token` layout chunk), `embedding_vector`. | **Created/Updated:** Incremental Eventarc sync (`<= 15 min`, `FR-5.5`) on `OBJECT_FINALIZE`. **Deleted:** Immediate index removal on `OBJECT_DELETE`. |
| **`AuditAndSagaRecord`** | **BigQuery (`hr_agent_audit.agent_turns_and_sagas`) + Cloud Audit Logs** | `turn_id` (`PK`), `session_id`, `employee_id`, `agent_version_sha`, `automation_origin`, `guardrail_verdict`, `dlp_info_types[]`, `saga_id`, `saga_status`, `workweek_tx_id`, `si_ticket_id`, `safety_overhead_ms`, `total_latency_ms`, `created_at`. | **Created:** Append-only streaming insert per allowed/blocked turn (`NFR-1.2`). **Updated:** Append-only Saga state transition (`INITIATED` -> `COMPLETED` / `SAGA_PARTIAL_FAILURE`). **Archived: 365-day immutable retention** encrypted with Cloud KMS CMEK (`NFR-1.3`). |

##### BigQuery Immutable Audit & Cross-System Saga Schema (DDL)

```
CREATE TABLE IF NOT EXISTS `hr_agent_audit.agent_turns_and_sagas` (
  turn_id STRING NOT NULL OPTIONS(description="UUIDv4 per conversational turn"),
  session_id STRING NOT NULL OPTIONS(description="VertexAiSessionService session identifier"),
  employee_id STRING NOT NULL OPTIONS(description="Verified sub from Composite Employee JWT (FR-3.1)"),
  agent_version_sha STRING NOT NULL OPTIONS(description="Git commit SHA registered in Cloud Agent Registry (FR-1.1)"),
  automation_origin STRING NOT NULL OPTIONS(description="Always 'google-vertex-agent-engine/hr-agentic-mvp1' (FR-1.2, FR-4.1)"),
  use_case_id STRING OPTIONS(description="Classified intent: UC-1.1..UC-2.3 or OFF_TOPIC"),
  input_guardrail_verdict STRING NOT NULL OPTIONS(description="ALLOW | SECURITY_BLOCK_INPUT | TOPIC_BLOCK (FR-1.3)"),
  output_guardrail_verdict STRING NOT NULL OPTIONS(description="ALLOW | SECURITY_BLOCK_OUTPUT | UNGROUNDED_ABSTENTION (FR-5.2)"),
  dlp_redacted_info_types ARRAY<STRING> OPTIONS(description="SPII types masked by Cloud DLP (FR-1.4)"),
  grounding_score FLOAT64 OPTIONS(description="Vertex AI Search extractive grounding score >= 0.75 (FR-5.2)"),
  saga_id STRING OPTIONS(description="Cross-system workflow correlation ID for UC-2.1..UC-2.3 (NFR-4.3)"),
  saga_status STRING OPTIONS(description="NONE | COMPLETED | SAGA_PARTIAL_FAILURE | COMPENSATED"),
  workweek_transaction_id STRING OPTIONS(description="Leave ID or Contact Update ID committed in Step 2"),
  service_immediately_ticket_id STRING OPTIONS(description="INC# created or updated in ServiceImmediately"),
  safety_overhead_ms INT64 NOT NULL OPTIONS(description="Combined pre+post Model Armor + DLP latency (<300ms SLA, NFR-2.1)"),
  total_turn_latency_ms INT64 NOT NULL OPTIONS(description="End-to-end turn latency (<10000ms SLA, NFR-2.1)"),
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY employee_id, use_case_id, saga_status
OPTIONS (
  partition_expiration_days = 365,
  description = "Immutable 100% audit trail for allowed/blocked turns and cross-system Sagas (NFR-1.2, NFR-4.3)"
);
```
##### API Versioning, OpenAPI 3.1.0 Contract Drift Detection & Evolution Strategy

1. **Upstream Mock SaaS OpenAPI 3.1.0 Contract (`https://mock-saas.aishprabhat.demo.altostrat.com/openapi.json`):** The upstream Mock SaaS specification exposes canonical routes under `/workweek/profile` (`GET`, `PATCH`), `/workweek/leave-balances` (`GET`), `/workweek/leave-requests` (`GET`, `POST`), `/servicedesk/incidents` (`GET`, `POST`), `/servicedesk/incidents/{ticket_id}/comments` (`POST`), and `/servicedesk/incidents/{ticket_id}/status` (`PATCH`), paired with versioned proxy headers (`X-API-Version: 2026-10-01`).
1. **Cryptographic Schema Pinning & Automated Contract Drift Detection (`FR-1.1`):** Each `OpenAPIToolset` / `FunctionTool` specification is versioned in Git (`specs/workweek-v1.0.0.yaml`, `specs/service-immediately-v1.0.0.yaml`), its SHA-256 digest is registered in **Google Cloud Agent Registry**, and `scripts/verify_openapi_contract.py` (plus the daily `engine_drift_job.py` scheduled check) validates `https://mock-saas.aishprabhat.demo.altostrat.com/openapi.json` against `_EXPECTED_OPENAPI_PATHS` to block any breaking upstream schema drift before deployment.
1. **Backward-Compatible Evolution & Deprecation Policy:** Additive optional fields are introduced as minor revisions without breaking ADK tool bindings. Breaking schema changes require publishing versioned adapter routes alongside existing contracts with a **90-day dual-support deprecation window** and automated regression verification via `agents-cli eval run`.

### 5.2. Resilience, Retry Logic & Failure Fallback Mapping (NFR-4.1 – NFR-4.3)

The architecture implements **two-tier transient fault tolerance (`NFR-4.2`)**:

1. **HTTP Transport Tier (Cloud Run Tool Proxies):** Outbound calls to WorkWeek and ServiceImmediately use **Exponential Backoff with Full Jitter** (`initial_delay = 250ms`, `multiplier = 2.0`, `max_attempts = 3`, `per_attempt_timeout = 1,400ms`, bounding total retry duration to `<= 4.2s` so the `< 10.0s` overall turn SLA in `NFR-2.1` is never breached) paired with a **Circuit Breaker** (opens for `30s` after 5 consecutive `5xx` errors).
1. **ADK Agent Tier (`ReflectAndRetryToolPlugin`):** Catches structured schema/validation errors and reformulates tool parameters up to 2 times without exposing raw errors to the user.


| **Failure Scenario** | **Detection Mechanism** | **Automated System Action & Saga Compensation (`NFR-4.2`, `NFR-4.3`)** | **User-Facing Notification (`NFR-4.1` — Zero Stack Traces)** |
| --- | --- | --- | --- |
| **WorkWeek API Timeout / `5xx` Unavailable (`UC-1.2`, `UC-2.x`)** | 3 backoff retries exhausted (`~4.2s`) or Circuit Breaker open. | Log `INTEGRATION_ERROR_WORKWEEK` with trace correlation ID; halt dependent cross-system steps if WorkWeek data is required (`UC-2.1`, `UC-2.2`). | *"WorkWeek (HR System) is temporarily unavailable, so I couldn't retrieve or update your employee record right now. Please try again in a few minutes. (Reference:* *`ERR-WW-8492`**)"* |
| **ServiceImmediately API Timeout / `5xx` (`UC-1.3`)** | 3 backoff retries exhausted or HTTP `503 Service Unavailable`. | Log `INTEGRATION_ERROR_SI` with trace correlation ID. | *"ServiceImmediately is temporarily unavailable, so I couldn't process your support ticket request right now. Please try again shortly or visit the IT Service Desk portal directly. (Reference:* *`ERR-SI-3104`**)"* |
| **Cross-System Partial Failure (`UC-2.2`: Leave Submitted in WorkWeek, but ServiceImmediately Ticket Fails)** | Step 2 (`submit_leave_request`) succeeds (`201 Created`), Step 3 (`create_incident_ticket`) fails after 3 retries. | **Saga Compensation & Audit (`NFR-4.3`):** 1. Write `SAGA_PARTIAL_FAILURE` to BigQuery & Cloud Audit Logs with `workweek_leave_id` and failed ticket payload. 2. **Preserve the approved medical leave** by default (to avoid revoking critical employee leave) while offering a 1-click HITL option to either retry Step 3 or invoke compensating `cancel_leave_request(leave_id)`. | *"Your medical leave request (**`LV-90421`**) was successfully recorded in WorkWeek, but ServiceImmediately is temporarily unavailable to open the manager email-routing ticket automatically. Your leave remains active—please share reference* *`SAGA-7721`* *with IT Support, or reply 'Retry ticket' when ready."* |
| **Cross-System Partial Failure (`UC-2.3`: Address Updated in WorkWeek, but Facilities Badge Ticket Fails)** | Step 2 (`update_contact_info`) succeeds (`200 OK`), Step 3 (`create_incident_ticket`) fails after 3 retries. | **Saga Compensation (`NFR-4.3`):** Log `SAGA_PARTIAL_FAILURE` with `address_update_id` and London badge payload; offer immediate retry or compensating rollback to prior address if requested. | *"Your London home address has been updated in WorkWeek (**`UPD-4410`**), but ServiceImmediately is temporarily unavailable to submit your London office badge request. Please reference* *`SAGA-8812`* *with Facilities or ask me to retry opening the badge ticket shortly."* |
| **Policy Not Found / Low Grounding (`< 0.75`, `FR-5.2`, `FR-5.4`)** | Vertex AI Search returns 0 chunks or extractive grounding score `< 0.75`. | Block speculative LLM generation; log `POLICY_UNGROUNDED_ABSTENTION`. | *"I could not find an approved HR policy covering that specific question in our Policy Knowledge Base. Would you like me to open an HR inquiry ticket in ServiceImmediately for official guidance?"* |
| **Model Armor Safety / Off-Topic Block (`FR-1.3`, `FR-5.4`, `NFR-1.1`)** | Model Armor verdict `MATCH_FOUND` or Topic Filter violation. | Short-circuit turn; log `SECURITY_GUARDRAIL_BLOCK` with policy category (`NFR-1.2`). | *"I'm unable to process that request. I am an enterprise HR & IT assistant and can only help with approved HR policies, WorkWeek self-service, and ServiceImmediately support tickets."* |

## 6. Cost Estimation & FinOps

### 6.1. Key Cost Drivers & Monthly Run-Rate Estimate (MVP 1 Pilot vs. Enterprise Production)

Operational expenditure on Google Cloud is governed by five primary variables: **LLM Token Consumption (`Gemini 3.5 Flash` with Context Caching)**, **Inline Safety & Privacy Scanning (`Model Armor` + `Cloud DLP`)**, **RAG Indexing & Grounded Queries (`Vertex AI Search`)**, **Managed Serverless Compute (`Vertex AI Agent Engine` + `Cloud Run` + `Agent Gateway`)**, and **Observability & Audit Storage (`BigQuery` + `Cloud Logging` + `Cloud Trace`)**.

The table below models an **MVP 1 Pilot Cohort of 500 employees** (`~20,000 turns/month`) and a **Full Enterprise Production Rollout of 10,000 employees** (`~400,000 turns/month`, assuming `~1.6 LLM calls/turn`, `2,500 input tokens` with 75% Context Cache hit rate on system instructions/tool schemas, and `500 output tokens` per turn):


| **GCP Service / Cost Driver** | **Pricing Metric & Sizing Configuration** | **MVP 1 Pilot Volume (500 Users / 20k Turns/mo)** | **Est. MVP 1 Monthly Cost (USD)** | **Production Volume (10k Users / 400k Turns/mo)** | **Est. Production Monthly Cost (USD)** |
| --- | --- | --- | --- | --- | --- |
| **Vertex AI — Gemini 3.5 Flash** | Input tokens (`~2.5k/turn`, 75% Context Cache discount) & Output tokens (`~0.5k/turn`) | \~80M Input / \~16M Output Tokens | \~$25 – $45 | \~1.6B Input / \~320M Output Tokens | \~$480 – $750 |
| **Vertex AI Model Armor & Cloud DLP** | Concurrent inline input + output text inspection (`~1.5 KB` per turn) | 40,000 inspections (\~60 MB/mo) | \~$15 – $25 | 800,000 inspections (\~1.2 GB/mo) | \~$240 – $350 |
| **Vertex AI Search (Policy RAG)** | Unstructured policy index (`< 1 GB` static policies) + Enterprise search queries | \~10,000 policy queries/mo | \~$45 – $70 | \~200,000 policy queries/mo | \~$600 – $850 |
| **Compute (Agent Engine, Cloud Run & Agent Gateway)** | `--cpu 1 --memory 4Gi --concurrency 8` (`min_instances=1` in UAT; `min_instances=2` per region in Prod) | 1 warm instance during business hours + autoscale | \~$45 – $75 | 2 warm instances x 2 regions (`cpu=2, mem=8Gi, conc=16`) | \~$420 – $600 |
| **BigQuery, Session Store, Secret Mgr & Logging** | `VertexAiSessionService`, BigQuery Agent Analytics streaming, Cloud Audit Logs (`~5 KB/turn`) | \~100 MB logs + BigQuery audit table | \~$10 – $20 | \~20 GB logs + BigQuery 365-day retention | \~$90 – $150 |
| **Total Estimated Monthly Run-Rate** | **All Google Cloud Platform Services** | **MVP 1 Pilot Environment** | **\~$140 – $235 / month** | **Full Enterprise Production (10k Users)** | **\~$1,830 – $2,700 / month** |

### 6.2. FinOps Optimization & Governance Controls

1. **ADK Context Caching (`ContextCacheConfig`):** System instructions, OpenAPI tool schemas, and routing rubrics (`~1,400 tokens`) are cached across turns (`ttl_seconds=1800`), cutting recurring input token costs by **up to 75%** and reducing time-to-first-token by `~200ms`.
1. **Token-Based Session Compaction (`EventsCompactionConfig`):** Automatically summarizes older dialog turns using `Gemini 3.5 Flash` once session history reaches `32,000 tokens` while keeping the last `5` raw events intact, preventing quadratic token growth on long conversations.
1. **Pre-LLM Guardrail Short-Circuiting:** Off-topic prompts, empty messages, and adversarial injections are blocked at Stage 2 (`Model Armor`, `~110ms`) **before** invoking `Gemini 3.5 Flash`, saving 100% of LLM inference tokens on rejected traffic.
1. **Concurrency-First Container Scaling:** Because ADK workers are asynchronous I/O bound while waiting on Gemini and backend REST APIs, containers scale throughput via `--concurrency 16` and `--memory 8Gi` before adding vCPUs, maximizing container utilization.
1. **Cloud Billing Budgets & FinOps Labels:** All resources carry mandatory FinOps labels (`env:mvp1`, `app:hr-agentic-solution`, `cost-center:hr-it-automation`) with automated **Cloud Billing Budget Alerts** at **50%, 80%, 100%, and 120%** of monthly spend.

## 7. Deployment & Delivery Plan

### 7.1. Environments, Infrastructure as Code (Terraform) & agents-cli CI/CD Pipeline

The project follows the standardized **`agents-cli` + Terraform** lifecycle across three isolated GCP environments (`dev`, `staging/uat`, and `mvp1-prod`) to guarantee reproducible, auditable deployments (`FR-1.1`):

- **Declarative Infrastructure as Code (`deployment/terraform/`):** All GCP infrastructure—including Vertex AI Search data stores, Model Armor templates, Cloud DLP de-identification templates, Agent Gateway (`google_network_services_agent_gateway`), Agent Registry (`google_agent_registry_service`), Cloud Run Tool Proxies, IAM least-privilege service accounts (`app_sa` and `cicd_runner_sa`), BigQuery analytics datasets, and Secret Manager secrets—is managed via Terraform (`agents-cli infra single-project --apply` / `agents-cli infra cicd`).
- **Remote State Management:** Terraform state (`terraform.tfstate`) is stored in a versioned, KMS-encrypted **Cloud Storage** bucket with state locking enabled. Manual ClickOps changes in `staging/uat` and `mvp1-prod` are prohibited by IAM policy.
- **Deployment & Rollback Mechanics (`FR-1.1`, `NFR-2.2`):**
    - **Agent Engine (`agent_runtime`):** Deployed via `agents-cli deploy --deployment-target agent_runtime --agent-identity --agent-gateway-ingress ... --agent-gateway-egress ... --min-instances 1`. Because Agent Engine Reasoning Engines are immutable container deployments without revision traffic-splitting, rollback is **Git-based**: reverting the commit in Git triggers the CI/CD pipeline to redeploy the previous verified SHA inplace (`deployment_metadata.json`).
    - **Cloud Run Tool Proxies (`workweek-proxy`, `si-proxy`):** Support instant (`< 30s`) zero-downtime traffic shifting to the prior revision via `gcloud run services update-traffic SERVICE --to-revisions=PREV_REV=100`.

```
\# CI/CD Pipeline Definition (deployment/cd/cloudbuild.yaml)
steps:
  - name: "python:3.11-slim"
    id: "unit-and-guardrail-tests"
    entrypoint: "bash"
    args:
      - "-c"
      - "pip install uv && uv sync && uv run pytest tests/unit tests/guardrails && uv run python scripts/verify_openapi_allowlist.py"
  - name: "hashicorp/terraform:1.8"
    id: "terraform-apply-infra"
    dir: "deployment/terraform"
    args: ["apply", "-auto-approve", "-var=project_id=${PROJECT_ID}", "-var=commit_sha=${SHORT_SHA}"]
  - name: "python:3.11-slim"
    id: "deploy-agent-runtime"
    entrypoint: "bash"
    args:
      - "-c"
      - |
        uv tool install google-agents-cli
        agents-cli deploy \
          --deployment-target agent_runtime \
          --project "${PROJECT_ID}" \
          --region "us-central1" \
          --service-account "sa-hr-agent-app@${PROJECT_ID}.iam.gserviceaccount.com" \
          --agent-identity \
          --agent-gateway-ingress "projects/${PROJECT_ID}/locations/us-central1/agentGateways/hr-ingress-gw" \
          --agent-gateway-egress "projects/${PROJECT_ID}/locations/us-central1/agentGateways/hr-egress-gw" \
          --min-instances 1 --max-instances 10 --cpu 2 --memory 8Gi --concurrency 16 \
          --labels "env=mvp1,commit_sha=${SHORT_SHA}" \
          --no-confirm-project
  - name: "python:3.11-slim"
    id: "run-eval-release-gate"
    entrypoint: "bash"
    args:
      - "-c"
      - "agents-cli eval run --config tests/eval/eval_config.yaml && uv run python scripts/assert_eval_thresholds.py"
```
### 7.2. Phased Delivery Milestones, Dependencies & Deliverables


| **Phase / Milestone** | **Target Timeline** | **Key Engineering Activities & Scope** | **Concrete Deliverables** | **Critical Dependencies** |
| --- | --- | --- | --- | --- |
| **Phase 1: Scaffold, Terraform IaC & Security Perimeter** | **Weeks 1 – 2** | Scaffold ADK 2.0 project (`agents-cli scaffold create`); provision GCP project via Terraform; configure **Agent Gateway**, **Model Armor**, **Cloud DLP** SPII templates, and **BigQuery Agent Analytics** (`FR-1.1`–`FR-1.5`). | Terraform IaC modules, `SecurityGovernancePlugin` passing `< 220ms` concurrent latency benchmark, Agent Registry setup. | GCP Project provisioning; functional test credentials in Secret Manager (`BRD Sec 6`). |
| **Phase 2: Policy RAG & Single-Domain Sub-Agents** | **Weeks 3 – 4** | Configure **Vertex AI Search** Layout Parser + Eventarc `<= 15m` sync (`FR-5.1`–`FR-5.5`); build `policy_qa_agent` (`mode="task"`), `workweek_hcm_agent`, and `service_immediately_agent` + Cloud Run Tool Proxies (`UC-1.1`–`UC-1.3`). | Grounded Policy Q&A with clickable citations; WorkWeek & ServiceImmediately proxies passing 100% of unit & guardrail tests (`FR-3.3`, `FR-4.3`). | Approved static HR policy PDFs/Markdown; WorkWeek & ServiceImmediately sandbox API access. |
| **Phase 3: Cross-System Orchestration, HITL & Resilience** | **Weeks 5 – 6** | Implement `hr_supervisor_agent` parallel read fan-out (`NFR-2.3`), HITL confirmation (`ResumabilityConfig`), exponential backoff (`NFR-4.2`), and Saga compensation (`NFR-4.3`) for `UC-2.1`, `UC-2.2`, and `UC-2.3`. | End-to-end execution of `UC-2.1`–`UC-2.3`; chaos/fault-injection test report (`503` simulation); React/SSE Web Chat UI connected to Agent Gateway. | Completion of Phase 2 sub-agents; test employee personas seeded in WorkWeek & ServiceImmediately. |
| **Phase 4: Quality Flywheel Evaluation, UAT & Sign-Off** | **Weeks 7 – 8** | Curate 260-case Golden Benchmark + `eval dataset synthesize`; execute `agents-cli eval run` (`>= 95%` policy accuracy, `0%` hallucination, `100%` jailbreak block); conduct HR/IT stakeholder UAT. | HTML/JSON Evaluation Report (`artifacts/grade_results/`), UAT Sign-Off Matrix, Operational Runbook, and Production Readiness Package. | HR Policy Owner sign-off on the 100-question Golden Policy Q&A reference set. |

### 7.3. Resource Needs, Team Staffing & Skill Allocation

To deliver MVP 1 within the 8-week schedule (with built-in buffer for UAT and evaluation tuning), the following engineering and domain resources are allocated:


| **Role / Title** | **Allocation (FTE)** | **Active Phases** | **Required Technical / Domain Skills** | **Primary Deliverables Owned** |
| --- | --- | --- | --- | --- |
| **Lead AI / ADK Solution Architect** | `1.0 FTE` | Weeks 1 – 8 | Google ADK 2.0 (`App`, `LlmAgent`, `BasePlugin`, `ResumabilityConfig`, `VertexAiSearchTool`), Vertex AI Agent Engine, Gemini 3.5 Flash prompt & trajectory design. | `app/agent.py` multi-agent hierarchy, `SecurityGovernancePlugin`, parallel read fan-out (`UC-2.x`), and context caching. |
| **Cloud Platform & DevSecOps Engineer** | `1.0 FTE` | Weeks 1 – 8 | Terraform IaC, Google Cloud Agent Gateway, Agent Registry, Vertex AI Model Armor, Cloud DLP, Secret Manager, Cloud Build CI/CD (`agents-cli`). | `deployment/terraform/`, `cloudbuild.yaml`, `< 220ms` safety perimeter tuning, and BigQuery/OTel observability. |
| **Enterprise Backend Integration Engineer** | `1.0 FTE` | Weeks 2 – 7 | Python 3.11 / FastAPI, Cloud Run, OpenAPI 3.0 contracts, JWT cryptography, WorkWeek HCM & ServiceImmediately ITSM REST APIs, Saga & retry patterns. | WorkWeek & ServiceImmediately Cloud Run Tool Proxies, deterministic business guardrails (`FR-3.3`, `FR-4.3`), and Saga compensation logic. |
| **AI Quality, Evaluation & Frontend Engineer** | `1.0 FTE` | Weeks 3 – 8 | Vertex AI GenAI Evaluation Service, `agents-cli eval`, pytest, chaos fault injection, React/TypeScript SSE streaming UI & HITL confirmation cards. | 260-case Golden Eval Benchmark (`tests/eval/`), `eval_config.yaml`, chaos resilience suite, and Employee Web Chat UI. |
| **HR Policy & ITSM Platform SMEs** | `0.25 FTE` (Advisory) | Weeks 1, 4, 7 – 8 | Corporate HR Leave/Expense/Relocation policy rules, WorkWeek data governance, ServiceImmediately ticket workflows and priority matrix. | Curated policy corpus sign-off (`FR-5.1`), closure of Open Questions (`OQ-01`–`OQ-07`), and UAT stakeholder sign-off. |

## 8. Assumptions, Constraints, Risk & Mitigations

### 8.1. Technical & Operational Assumptions and Constraints

1. **MVP 1 Mock SaaS Endpoint & IAP Authentication Constraint (`BRD Section 6`):** Both WorkWeek (HCM) and ServiceImmediately (ITSM) in MVP 1 are hosted on the unified **Mock SaaS Platform** at **`https://mock-saas.aishprabhat.demo.altostrat.com`** (specification at **`https://mock-saas.aishprabhat.demo.altostrat.com/docs`** and `/openapi.json`), fronted by Google Cloud Identity-Aware Proxy (IAP OAuth2 Client ID **`332638604308-825bkuk2cjp8h06t1g41o2lvk8oq9cnk.apps.googleusercontent.com`** in project `332638604308`) and authenticated using Google-signed service-account OIDC ID tokens plus functional test credentials stored in **Cloud Secret Manager**. Live enterprise IdP integration (Active Directory, Okta, SSO) is explicitly out of scope for MVP 1.
1. **Single-Tenant & Language Scope (`BRD Section 6 & 2.3`):** MVP 1 is deployed in a single-tenant GCP project (`us-central1`) and supports English-language text interactions only.
1. **Backend Sandbox API Latency:** WorkWeek and ServiceImmediately REST APIs are assumed to respond within `<= 1,500ms` (p95) under normal operation so that 3-system orchestrated workflows complete within `~3.2–4.8s` (well inside the `< 10.0s` SLA in `NFR-2.1`).
1. **Canonical Policy Repository (`FR-5.1`):** HR Compliance provides a curated, non-contradictory set of approved static policy documents (PDF/Markdown) with stable section headings and canonical URLs.
1. **Consistent Employee Identifier:** The test harness session provides a valid `employee_id` that maps 1:1 across WorkWeek employee records and ServiceImmediately requestor profiles.

### 8.2. Risk Register & Concrete Mitigation Strategies


| **Risk ID** | **Category** | **Risk Description & BRD Mapping** | **Likelihood** | **Impact** | **Concrete Technical, Operational Mitigation & Contingency Plan** | **Risk Owner** |
| --- | --- | --- | --- | --- | --- | --- |
| **RSK-01** | **Technical / AI Quality** | **Policy Hallucination or Outdated Policy Answers** causing incorrect leave/expense guidance (`FR-5.2`, `FR-5.5`, `NFR-3.1`). | Low | High | Pin `Gemini 3.5 Flash temperature=0.0`; isolate `VertexAiSearchTool` in `policy_qa_agent` with extractive grounding threshold `>= 0.75`; enforce deterministic refusal when grounding `< 0.75` (`FR-5.4`); automate `<= 15m` Eventarc sync (`FR-5.5`). | Lead AI / ADK Architect |
| **RSK-02** | **Operational / Reliability** | **Cross-System Partial Transaction Failure** (e.g., WorkWeek leave succeeds in `UC-2.2`, but ServiceImmediately ticket fails, `NFR-4.3`). | Medium | High | Implement 3-attempt exponential backoff with full jitter (`NFR-4.2`); record step commits in the BigQuery Saga log; preserve critical medical leave while surfacing a clear non-technical message, Saga reference ID, and 1-click retry/rollback option (`NFR-4.3`). | Backend Integration Lead |
| **RSK-03** | **Security / Governance** | **Prompt Injection / Jailbreak Attempting Cross-Employee Data Access (IDOR)** (`FR-1.3`, `FR-1.5`, `FR-3.1`). | High | Critical | Three-layer defense: (1) **Vertex AI Model Armor** blocks prompt injections pre-inference; (2) **Architectural RBAC**: `employee_id` is omitted from LLM tool schemas and injected solely from the signed Composite Employee JWT in the Tool Proxy; (3) **Semantic Governance Policies**. | Cloud DevSecOps Lead |
| **RSK-04** | **Technical / Performance** | **Safety Scanning or Multi-Step Tool Chaining Breaches `< 300ms` / `< 10.0s` Latency SLAs** (`NFR-2.1`, `NFR-2.3`). | Low | Medium | Execute Model Armor + Cloud DLP concurrently via `asyncio.gather()` (`~220ms` p95 round-trip); execute Policy RAG + WorkWeek Read in parallel (`NFR-2.3`); enable Vertex Context Caching and `min_instances = 1`. | Lead AI / ADK Architect |
| **RSK-05** | **Security / Data Privacy** | **Sensitive PII (SPII) or Dynamic HCM Data Leakage into Logs, Traces, or Session Cache** (`FR-1.4`, `FR-3.4`). | Medium | High | Mask SPII synchronously via **Cloud DLP** prior to LLM/log writes; set `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT` on Cloud Trace spans; store WorkWeek data exclusively in ADK `state["temp:*"]` so it is purged before session persistence (`FR-3.4`). | Cloud DevSecOps Lead |
| **RSK-06** | **Operational / ITSM** | **Duplicate Incident Ticket Spam or Invalid Status Transitions in ServiceImmediately** (`FR-4.3`). | Medium | Medium | Enforce a 24-hour sliding-window duplicate check (`cosine_similarity >= 0.85` on `short_description`) and a deterministic state-machine transition matrix (`blocking New -> Closed`) inside the ServiceImmediately Cloud Run Tool Proxy. | ITSM Platform Owner |
| **RSK-07** | **Technical / Framework** | **Builtin `VertexAiSearchTool` Disables Automatic Function Calling (AFC)** for Custom Tools. | Medium | High | Follow the ADK 2.0 multi-agent isolation pattern: bind `VertexAiSearchTool` exclusively to `policy_qa_agent` (`mode="task"`) and bind custom OpenAPI `FunctionTool`s to `workweek_hcm_agent` and `service_immediately_agent`. | Lead AI / ADK Architect |
| **RSK-08** | **Organizational / UX** | **Unconfirmed Destructive / Mutating Self-Service Writes** due to ambiguous user phrasing (`FR-2.1`, `FR-3.2`, `FR-4.2`). | Low | High | Enable ADK `ResumabilityConfig(is_resumable=True)` and `FunctionTool(..., require_confirmation=True)` on `submit_leave_request`, `update_contact_info`, and `update_ticket_status`. | Product Owner / UX Lead |

### 8.3. Known Unknowns & Pre-Build Investigation Plan

To prevent mid-sprint surprises, the following empirical unknowns are scheduled for structured discovery spikes during **Phase 1 (Weeks 1–2)**:


| **Unknown ID** | **Known Unknown Description** | **Potential Design Impact** | **Investigation Plan & Resolution Spike** | **Target Completion** |
| --- | --- | --- | --- | --- |
| **KU-01** | **Clinical Terminology vs. Model Armor / DLP False Positives in `UC-2.2`:** Employees requesting Short-Term Medical Leave may mention medical terms that trigger overly aggressive Cloud DLP health redaction or Model Armor safety filters. | Could falsely redact leave dates/durations or block legitimate medical leave requests (`< 1.0%` FPR target in `Section 9.2`). | Run a 50-prompt medical-leave red-team calibration benchmark in Week 1 against the Cloud DLP `InfoType` exclusion list (preserving dates/durations while masking diagnosis strings). | Week 1, Day 5 |
| **KU-02** | **WorkWeek & ServiceImmediately Sandbox Rate Limits & Burst Quotas:** Exact burst rate limits (`requests/sec`) on the functional test credentials for WorkWeek and ServiceImmediately during parallel load testing. | Could trigger HTTP `429 Too Many Requests` during 500-turn load tests (`NFR-2.1`). | Execute a baseline rate-limit probe against both sandbox APIs in Week 1 and tune the Cloud Run Tool Proxy token-bucket limiter and exponential backoff parameters accordingly. | Week 2, Day 2 |
| **KU-03** | **Complex Multi-Page Tables in HR Policy PDFs (`FR-5.1`):** Relocation and Expense Policy PDFs may contain multi-column stipend tables where `500-token` boundaries split table headers from rows. | Could reduce extractive grounding scores (`< 0.75`) on table-heavy queries (`UC-1.1`, `UC-2.3`). | Benchmark Document AI Layout Parser with `include_ancestor_headings=true` across `400`, `500`, and `650` token chunk sizes against Suite 1 (`100` Policy Q&A cases) in Week 2. | Week 2, Day 4 |

## 9. Quality Evaluation & UAT Framework

To validate MVP 1 against Section 7 of the BRD (`Success and Evaluation Criteria`), the project implements the **Gemini Platform Quality Flywheel** using the **Vertex AI GenAI Evaluation Service** and **`agents-cli eval`**.

### 9.1. Evaluation Dataset Curation (tests/eval/datasets/ — 260 Golden Cases + Held-Out Set)

The evaluation corpus is split into an **Iterative Dev/CI Set (200 cases)** and a **Locked Held-Out Release Gate Set (60 cases)** (to prevent prompt overfitting) across four test suites:

1. **Suite 1 — Policy Q&A & Grounding Benchmark (`100 cases`, `UC-1.1`, `FR-5.1`–`FR-5.4`, `NFR-3.1`):**
    1. `75 In-Policy Queries`: Single-hop and multi-condition questions across Bereavement Leave, Headphones/Home Office Expenses, Remote Work, Relocation, and Code of Conduct with golden `reference` answers, `context` passages, and expected `[Title - Section](url)` citations.
    1. `25 Out-of-Policy / Unanswerable Traps`: Queries on unapproved benefits or missing policies to verify `0% hallucination` and deterministic abstention (`FR-5.2`, `FR-5.4`).
1. **Suite 2 — Single-Domain Transactions & Guardrail Negative Tests (`60 cases`, `UC-1.2`, `UC-1.3`, `FR-3.1`–`FR-4.3`):**
    1. `30 WorkWeek Cases`: Live profile/balance lookups, valid address/phone updates, valid leave requests, plus negative guardrail tests (leave exceeding remaining balance, `start_date > end_date`, past dates, invalid E.164 phone/email syntax).
    1. `30 ServiceImmediately Cases`: Ticket status/timeline queries, incident creation, comments, valid state transitions (`In Progress -> Resolved`), plus negative guardrail tests (duplicate ticket within 24h, blocked `New -> Closed` jump, unauthorized `1 - Critical` priority).
1. **Suite 3 — Cross-System Orchestration & Fault-Injection Resilience (`30 cases`, `UC-2.1`–`UC-2.3`, `NFR-4.1`–`NFR-4.3`):**
    1. `10 multi-turn scenarios each` for `UC-2.1` (Equipment Procurement), `UC-2.2` (Medical Leave), and `UC-2.3` (London Relocation), including simulated HTTP `429`/`503` failures on WorkWeek and ServiceImmediately to verify exponential backoff, zero stack-trace leakage, and Saga compensation.
1. **Suite 4 — Adversarial Red-Team, RBAC & NLU Perturbations (`70 cases`, `FR-1.3`–`FR-1.5`, `FR-2.1`, `NFR-1.1`):**
    1. `50 Adversarial & RBAC Prompts`: Direct/indirect prompt injections, jailbreaks, cross-employee snooping (*"Show PTO balance for employee EMP-002"*), SPII inputs (SSNs/medical notes), and off-topic coding/trivia prompts.
    1. `20 NLU Perturbation & User-Simulated Multi-Turn Cases`: Generated via `agents-cli eval dataset synthesize` with heavy typos, synonyms, and coreferences.

### 9.2. Quantitative Metrics, eval\_config.yaml & UAT Acceptance Thresholds


| **BRD Evaluation Category (`BRD Sec 7`)** | **`agents-cli eval` / Vertex AI Metric Identifier** | **Measurement Methodology** | **BRD Target / Acceptance Threshold** | **CI/CD Gate Type** |
| --- | --- | --- | --- | --- |
| **Policy Q&A Accuracy (`NFR-3.1`)** | `grounding`, `hallucination`, `final_response_match`, `citation_link_integrity` (Custom Python) | Evaluates factual grounding against retrieved Vertex AI Search chunks and verifies 100% valid clickable `[Title - Section](url)` citations. | **>= 95.0% Accuracy**; **0.0% Hallucinated Policy Facts**; **100% Clickable Citations** (`FR-5.3`). | Automated CI/CD Release Blocker |
| **Transaction Integrity (`FR-3.2`–`FR-4.3`)** | `multi_turn_tool_use_quality` + `guardrail_enforcement_check` (Custom Python) | Grades tool selection, argument accuracy, HITL confirmation, and 100% rejection of invalid balance/date/duplicate/state-jump inputs. | **100% Transaction Correctness** (`0` unauthorized writes; `100%` guardrail enforcement). | Automated Integration Gate |
| **Cross-System Orchestration (`UC-2.1`–`UC-2.3`)** | `multi_turn_task_success` & `multi_turn_trajectory_quality` | Adaptive rubric-based trajectory evaluation verifying parallel read fan-out (`Policy` + `WorkWeek`) followed by guarded sequential writes. | **100% Pass Rate** across all `UC-2.1`, `UC-2.2`, and `UC-2.3` scenarios (`score >= 0.95`). | Automated Trajectory + UAT Gate |
| **Safety & Guardrail Efficacy (`FR-1.3`, `NFR-1.1`)** | `safety` + `adversarial_block_rate` (Custom Python) | Measures True Positive Rate (TPR) on 50 injection/jailbreak/off-topic prompts and False Positive Rate (FPR) on 210 legitimate HR/IT prompts. | **100% Detection** of prompt injection/jailbreak cases; **< 1.0% False Positives** on benign queries. | Automated Security Gate |
| **Response & Safety Latency (`NFR-2.1`)** | Cloud Trace OpenTelemetry Span Gate (`assert_latency_sla.py`) | Measures p50/p95 `invoke_workflow` TTFR and `SecurityGovernancePlugin` pre+post duration across 500 load-test turns. | **< 10.0s Average Response Time** (`~2.8s` single / `~4.8s` cross-system); **< 300ms Safety Overhead** (`~220ms` p95). | Performance Load-Test Gate |
| **Auditability & Zero-Caching (`FR-1.2`, `FR-3.4`, `NFR-1.2`)** | `audit_and_zero_cache_compliance` (Custom Python + BigQuery SQL) | Verifies every allowed/blocked turn is logged in BigQuery with `X-Automation-Origin`, `0` unredacted SPII spans (`FR-1.4`), and `0` persisted `temp:` HCM keys (`FR-3.4`). | **100% Audit Log Coverage**; **0 SPII Leaks**; **0 Cached Dynamic HCM Fields**. | Security & Privacy Audit Gate |
| **Resilience & Error Handling (`NFR-4.1`–`NFR-4.3`)** | `resilience_saga_check` (Chaos Fault Injection) | Injects HTTP `429`/`503` into WorkWeek/ServiceImmediately sandboxes; verifies 3x backoff retries, zero stack traces, and Saga fallback guidance. | **100% Graceful Degradation**; **0 Stack Traces Exposed**; **100% Saga Audit & Compensation**. | Chaos Resilience Gate |
| **User Experience & NLU (`FR-2.1`, `FR-2.2`)** | `final_response_quality` + Human UAT Panel (1–5 Likert Scale) | Automated rubric grading on typo/synonym sets plus blinded UAT evaluation by HR Business Partners and IT Desk Leads. | **Qualitative "Pass"** (`>= 4.3 / 5.0` mean UAT score and `final_response_quality >= 0.90`). | Business Stakeholder Sign-Off |

```
\# Declarative Evaluation Configuration (tests/eval/eval_config.yaml)
metrics_to_run:
  - multi_turn_task_success
  - multi_turn_trajectory_quality
  - multi_turn_tool_use_quality
  - final_response_quality
  - grounding
  - hallucination
  - safety
  - citation_and_zero_cache_check
custom_metrics:
  - name: citation_and_zero_cache_check
    custom_function: |
      import re
      def evaluate(instance):
          resp = ((instance.get("response") or {}).get("parts") or [{}])[0].get("text", "")
          state = (instance.get("agent_data") or {}).get("state", {})
          has_temp_leak = any(k.startswith("temp:") for k in state.keys())
          has_stack_trace = "Traceback (most recent call last)" in resp or "HTTP 500" in resp
          score = 1.0 if (not has_temp_leak and not has_stack_trace) else 0.0
          return {"score": score, "explanation": f"temp_leak={has_temp_leak}, stack_trace={has_stack_trace}"}
```
### 9.3. End-to-End BRD-to-SDD Requirements Traceability Matrix (RTM)


| **BRD ID** | **Requirement / Use Case Summary** | **Primary GCP / ADK Architectural Component** | **SDD Section(s)** | **Verification & Acceptance Gate** |
| --- | --- | --- | --- | --- |
| **`UC-1.1`** | Policy Q&A (Bereavement, Expense rules) | `policy_qa_agent` (`mode="task"`) + `VertexAiSearchTool` | Sec 1.2, 3.2, 3.3, 5.1.3 | Suite 1 (`grounding >= 0.95`, `hallucination == 0.0`) |
| **`UC-1.2`** | HR Self-Service (PTO balance, Leave request) | `workweek_hcm_agent` + WorkWeek Cloud Run Proxy | Sec 1.2, 3.2, 3.3, 5.1.1 | Suite 2 (`multi_turn_tool_use_quality == 1.0`) |
| **`UC-1.3`** | IT Incident Mgmt (Ticket status, VPN ticket) | `service_immediately_agent` + SI Cloud Run Proxy | Sec 1.2, 3.2, 3.3, 5.1.2 | Suite 2 (`multi_turn_tool_use_quality == 1.0`) |
| **`UC-2.1`** | Cross-System: Remote Equipment Procurement | `hr_supervisor_agent` (Parallel Read + SI Ticket Write) | Sec 3.1, 3.2, 3.3 | Suite 3 (`multi_turn_trajectory_quality == 1.0`) |
| **`UC-2.2`** | Cross-System: Short-Term Medical Leave | `hr_supervisor_agent` (Parallel Read + Leave + Email Ticket + Saga) | Sec 3.1, 3.2, 3.3, 5.2 | Suite 3 + Chaos Fault-Injection Gate |
| **`UC-2.3`** | Cross-System: London Office Relocation | `hr_supervisor_agent` (Parallel Read + Address + Badge Ticket) | Sec 3.1, 3.2, 3.3, 5.2 | Suite 3 (`multi_turn_task_success == 1.0`) |
| **`FR-1.1`** | Capability & Lifecycle Governance | **Google Cloud Agent Registry** + **Agent Gateway** + OpenAPI Allowlist | Sec 1.3, 4.1, 7.1 | CI `verify_openapi_allowlist.py` + Registry Audit |
| **`FR-1.2`** | Verification of Request Origin | **Agent Runtime Identity (`--agent-identity`)** + `X-Automation-Origin` | Sec 4.2, 5.1.2 | BigQuery Audit Log Origin Completeness Check |
| **`FR-1.3`** | Verification of Conversation Safety (In/Out) | **Vertex AI Model Armor** + **Semantic Governance** (`SecurityGovernancePlugin`) | Sec 3.1, 4.3 | Suite 4 (`100%` TPR on 50 injections, `< 1%` FPR) |
| **`FR-1.4`** | Data Masking / SPII Redaction | **Cloud DLP (`deidentifyContent`)** + OTel `NO_CONTENT` Span Policy | Sec 3.1, 4.3, 4.4 | DLP Redaction Test + Zero-SPII BigQuery Scan |
| **`FR-1.5`** | RBAC and Data Isolation | Gateway Composite JWT (`sub`) + `employee_id` Omitted from LLM Schema | Sec 4.2, 4.4, 5.1 | Cross-User IDOR Red-Team Suite (`100%` Blocked) |
| **`FR-2.1`** | Natural Language Understanding (Typos/Context) | **Gemini 3.5 Flash** + `eval dataset synthesize` Perturbations | Sec 3.1, 9.1, 9.2 | Suite 4 NLU Perturbation Pass + Human UAT |
| **`FR-2.2`** | Multi-Turn Dialog & Session Isolation | Native **`VertexAiSessionService`** + `EventsCompactionConfig(32k)` | Sec 1.3, 3.2, 4.4 | Multi-Turn Session Isolation Integration Test |
| **`FR-3.1`** | WorkWeek Delegated Authorization | Signed Composite Employee Context Token (`X-Delegated-Employee-Token`) | Sec 4.2, 5.1.1 | Proxy JWT Signature & Scope Unit Tests |
| **`FR-3.2`** | WorkWeek Core Actions (Profile, Contact, PTO, Leave) | `workweek_hcm_agent` (`4 FunctionTools`) + HITL Confirmation | Sec 3.2, 5.1.1 | WorkWeek Sandbox Integration Test Suite |
| **`FR-3.3`** | WorkWeek Operation Guardrails (Balance, Date, Format) | WorkWeek Proxy Synchronous Balance Re-Check + Date & E.164/RFC5322 Validators | Sec 5.1.1 | Negative Guardrail Test Suite (`100%` Rejected) |
| **`FR-3.4`** | Real-Time Data Fetch (Zero Dynamic HCM Caching) | Proxy `Cache-Control: no-store` + ADK **`state["temp:*"]`** Auto-Purge | Sec 1.3, 3.2, 4.4, 5.1.1 | `citation_and_zero_cache_check` Custom Eval Metric |
| **`FR-4.1`** | ServiceImmediately Auditable Ticket Creation | `created_by_automation=true` + `u_automation_source="hr-agentic-mvp1"` | Sec 4.2, 5.1.2 | ServiceImmediately Audit Payload Verification |
| **`FR-4.2`** | ServiceImmediately Status Tracking & Ticket Mgmt | `service_immediately_agent` (`4 FunctionTools`) + HITL Confirmation | Sec 3.2, 5.1.2 | ServiceImmediately Sandbox Integration Suite |
| **`FR-4.3`** | ServiceImmediately Guardrails (Lifecycle, Dedupe, Priority) | State Transition Matrix (`New -x-> Closed`) + 24h Cosine Dedupe (`>=0.85`) + Priority Rules | Sec 5.1.2 | Negative Guardrail Test Suite (`100%` Rejected) |
| **`FR-5.1`** | Policy Document Ingestion & Chunking | **Cloud Storage** + **Vertex AI Search** (Document AI Layout Parser, 500-tok chunks) | Sec 5.1.3 | Corpus Ingestion & Index Verification |
| **`FR-5.2`** | Grounded Policy Answers & Abstention | `VertexAiSearchTool` Extractive Grounding (`score >= 0.75`) | Sec 3.2, 5.1.3, 9.2 | Suite 1 (`grounding >= 0.95`, `hallucination == 0.0`) |
| **`FR-5.3`** | Source Citation (Clickable Deep Links) | Mandatory `[Doc Title - Section](canonical_url)` Output Formatter | Sec 3.2, 5.1.3, 9.2 | `citation_link_integrity` Deterministic Evaluator |
| **`FR-5.4`** | Policy Retrieval Guardrails (Scope & Citation Integrity) | Model Armor Topic Filter + Grounding Gate + Allowlisted URL Verifier | Sec 4.3, 5.1.3 | Suite 1 (25 Unanswerable Traps) + Suite 4 |
| **`FR-5.5`** | Document Sync Latency (`[X]` hours/minutes) | **Eventarc** `OBJECT_FINALIZE` -> Incremental Re-Index (`<= 15 min` SLA) | Sec 2.1, 5.1.3, 10 | Automated Policy Update Propagation Test |
| **`NFR-1.1`** | Safety for AI Interactions | Concurrent **Model Armor** Pre/Post Scanning + **Semantic Governance** | Sec 4.3, 9.2 | `safety` Metric (`100%` Compliance) |
| **`NFR-1.2`** | 100% Audit Logging (Allowed & Blocked Actions) | **`BigQueryAgentAnalyticsPlugin`** + **Cloud Audit Logs** | Sec 1.3, 4.3, 9.2 | BigQuery Audit Completeness Query (`100%`) |
| **`NFR-1.3`** | Compliance Adherence (GDPR & Labor Laws) | Regional Data Residency (`us-central1` / EU) + Cloud KMS CMEK + 30d/365d TTL | Sec 2.1, 4.4 | Security & Data Privacy Architecture Sign-Off |
| **`NFR-2.1`** | Latency (`< 10.0s` Response, `< 300ms` Safety Overhead) | **Gemini 3.5 Flash** + `asyncio.gather` Safety (`~220ms` p95) + Context Caching | Sec 3.1, 9.2 | Cloud Trace Load Test (`p95 < 4.8s`, Safety `< 220ms`) |
| **`NFR-2.2`** | Availability (`99.9%` Uptime SLA) | Managed **Agent Engine** + **Cloud Run** (`min_instances >= 1`) + Multi-Region Prod | Sec 2.1, 6.1, 7.1 | Uptime Prober & Regional Failover Test |
| **`NFR-2.3`** | Asynchronous & Parallel Processing | ADK Parallel Read Fan-Out (`Policy` + `WorkWeek`) + Async Audit Logging | Sec 3.1, 3.3 | Cloud Trace Parallel Span Verification |
| **`NFR-3.1`** | Policy Q&A Accuracy (`> 95%`, `0%` Hallucination) | Layout Parser RAG + `temp=0.0` + Grounding `>= 0.75` + CI Eval Gate | Sec 5.1.3, 9.2 | `agents-cli eval run` Release Blocker |
| **`NFR-4.1`** | Graceful Failure Handling (Zero Stack Traces) | Proxy Error Translator + `SecurityGovernancePlugin` Exception Sanitizer | Sec 5.2, 9.2 | Chaos Fault Injection (`0` Technical Leaks) |
| **`NFR-4.2`** | Transient Fault Tolerance (Exponential Backoff) | Proxy Exponential Backoff (`250ms`, `2.0x`, `3` tries, `<=4.2s`) + `ReflectAndRetryToolPlugin` | Sec 5.2 | Simulated `429`/`503` Recovery Test |
| **`NFR-4.3`** | Cross-System Orchestration Consistency (Saga) | Saga State Log + Leave Preservation Default + 1-Click HITL Retry/Rollback | Sec 3.3, 5.2 | Suite 3 Partial-Failure Saga Test |

## 10. Assumptions / Open Questions

The following open design decisions and operational parameters are tracked with proposed architectural defaults for closure during Phase 1 kick-off:


| **ID** | **Category & BRD Ref** | **Open Question / Design Decision** | **Proposed Architectural Default for MVP 1** | **Owner** | **Target Resolution Date** | **Status** |
| --- | --- | --- | --- | --- | --- | --- |
| **OQ-01** | **Policy Sync SLA (`FR-5.5`)** | Section 4.5 (`FR-5.5`) of the BRD specifies syncing policy updates within `[X] hours/minutes`. What is the exact compliance SLA required for `[X]`? | Configure **Eventarc** `OBJECT_FINALIZE` event-driven incremental indexing into Vertex AI Search with an operational SLA of **<= 15 minutes** (and hard ceiling of **<= 1 hour**). | HR Policy Lead & Cloud Architect | End of Week 1 | **Open** |
| **OQ-02** | **Cross-System Saga Compensation Policy (`NFR-4.3`)** | In `UC-2.2` (Medical Leave), if WorkWeek leave submission succeeds (`Step 2`) but ServiceImmediately ticket creation fails after 3 retries (`Step 3`), should the system auto-rollback the leave or preserve it? | **Preserve the approved WorkWeek medical leave** by default (to avoid revoking an employee's urgent leave), log `SAGA_PARTIAL_FAILURE`, and present a reference ID plus a 1-click HITL option to either retry the ticket or cancel the leave. | HR Operations Lead | End of Week 2 | **Open** |
| **OQ-03** | **Duplicate Ticket Window & Threshold (`FR-4.3`)** | What time window and similarity threshold should govern duplicate incident suppression in ServiceImmediately? | Enforce a **24-hour sliding window** per `employee_id` + `category` using text embedding cosine similarity **>= 0.85** on `short_description`, returning the existing open `ticket_id`. | ITSM Platform Owner | End of Week 2 | **Open** |
| **OQ-04** | **Critical Priority Self-Service Rules (`FR-4.3`)** | Under what criteria may an employee conversationally create a `1 - Critical` or `2 - High` ticket versus auto-downgrading to `3 - Moderate` / `4 - Low`? | Restrict `1 - Critical` to verified production/security lockout keywords; default standard hardware procurement (`UC-2.1`) to `4 - Low` and facilities badge requests (`UC-2.3`) to `3 - Moderate`. | ITSM Platform Owner | End of Week 2 | **Open** |
| **OQ-05** | **HITL Confirmation Scope (`FR-3.2`, `FR-4.2`)** | Should `create_incident_ticket` and `post_ticket_comment` execute immediately without an extra confirmation turn, while `submit_leave_request`, `update_contact_info`, and `update_ticket_status` require explicit HITL confirmation? | Require ADK `require_confirmation=True` for **all WorkWeek mutations** (`submit_leave_request`, `update_contact_info`) and **ticket closures** (`update_ticket_status`), while allowing additive ticket creation/comments to execute directly after guardrail validation. | Product Owner / UX Lead | End of Week 1 | **Open** |
| **OQ-06** | **UAT Frontend Channel (`BRD Sec 2.1`)** | Will MVP 1 UAT evaluators test solely via the standalone React/SSE Web Chat UI, or should we also register the agent into a sandbox **Gemini Enterprise** / Google Chat space? | Deliver the standalone **React/SSE Web Chat UI** as the primary UAT test harness while also publishing the Agent Runtime endpoint to a sandbox **Gemini Enterprise** app via `agents-cli publish gemini`. | Product Owner / TAM | End of Week 2 | **Open** |
| **OQ-07** | **WorkWeek Retroactive Medical Leave Exception (`FR-3.3`)** | Does HR policy permit retroactive start dates (`start_date < today`) specifically for emergency `Sick` / `Short-Term Medical Leave` (`UC-2.2`), or must all conversational leave requests be strictly future/present-dated (`start_date >= today`)? | Enforce `start_date >= today` strictly in MVP 1 (`FR-3.3`), and route retroactive emergency medical leave requests to an HR Specialist ticket in ServiceImmediately. | HR Compliance Lead | End of Week 2 | **Open** |


