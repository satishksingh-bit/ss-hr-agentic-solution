# SS-Enterprise HR Agentic Solution — MVP 1 (`ss-hr-agentic-solution`)

Enterprise Multi-Agent HR & IT Service Delivery Platform built on **Google Agent Development Kit (ADK v2.11.0)** and **Vertex AI Agent Engine**, implementing the **SS-Enterprise Agentic Solution Design Document (SDD v2.1)** with **zero architecture drift**.

---

## 1. Architecture Overview

The solution uses a **Hierarchical Coordinator + Specialist Sub-Agent + `AgentTool` Topology** with a **Two-Tier Identity Model** (`UserContextToken` ES256/HMAC JWT + Workload Identity Federation) and **Two-Phase Human-in-the-Loop (HITL) Confirmation (`CT`)** for all external writes:

- **`hr_supervisor_agent` (`root_agent` — `LlmAgent`, `gemini-3.5-flash` with `gemini-2.5-flash` fallback):**
  Top-level supervisor and intent router. Orchestrates single-domain and multi-system composite workflows (`UC-2.2` Leave + Mailbox Delegation, `UC-2.3` Address Update + Badge Re-provisioning) via `DeterministicPlanExecutor` and Firestore transactional journals (`hr-sessions`, `hr-idempotency`, `hr-plans`, `hr-audit-events`).
- **`policy_qa_agent` (wrapped via `AgentTool`):**
  Country-scoped RAG specialist querying `hr-policy-datastore-eu` across `NL`, `DE`, `CN`, and `GLOBAL` policy documents, enforcing paragraph-level `CheckGrounding >= 0.80` and `[doc_id#section_id]` citation verification.
- **`workweek_hcm_agent` (`sub_agent`):**
  WorkWeek HCM specialist exposing 6 MCP tools (`get_my_profile`, `get_my_leave_balances`, `validate_leave_request`, `submit_my_leave_request`, `validate_contact_update`, `update_my_contact_info`) connected to the WorkWeek MCP proxy (`app/workweek_proxy.py`) and upstream Mock SaaS (`https://mock-saas.aishprabhat.demo.altostrat.com/docs`).
- **`service_immediately_agent` (`sub_agent`):**
  ServiceImmediately ITSM specialist exposing 7 MCP tools (`get_my_ticket`, `list_my_open_tickets`, `validate_incident`, `create_incident`, `validate_ticket_update`, `add_ticket_comment`, `update_ticket_status`) enforcing deterministic priority matrix rules (`P-01..P-13`).
- **`SecurityGovernancePlugin` (`BasePlugin`):**
  Global ADK guardrail plugin intercepting every `before_agent_callback`, `before_model_callback`, `before_tool_callback`, `after_tool_callback`, and `after_model_callback` to enforce:
  1. **Taiwan (`TW`) Regional Access Blocking** (`REGIONAL_ACCESS_BLOCKED`)
  2. **Model Armor Prompt Injection & Jailbreak Defense** (`PROMPT_INJECTION_DETECTED`)
  3. **Cloud DLP SPII Redaction** (`SSN`, `BSN`, `IBAN`, `Salary`, `Bank Account`)
  4. **Zero-IDOR Identity Binding** (stripping caller-supplied `employee_id` and binding exclusively to server-injected `UCT.sub`)
  5. **Mandatory HITL Confirmation Token (`CT`) Verification** on all mutating MCP tools

---

## 2. Repository Structure

```text
ss-hr-agentic-solution/
├── app/
│   ├── agent.py                  # Root ADK App, hr_supervisor_agent, sub-agents, AgentTool & SecurityGovernancePlugin
│   ├── fast_api_app.py           # FastAPI BFF & Agent Engine entrypoint (/v1/chat:stream, /v1/actions/{plan_id}:confirm)
│   ├── upstream_client.py        # Upstream Mock SaaS client (https://mock-saas.aishprabhat.demo.altostrat.com)
│   ├── workweek_proxy.py         # Cloud Run FastAPI MCP Proxy for WorkWeek HCM (:8081)
│   ├── servicedesk_proxy.py      # Cloud Run FastAPI MCP Proxy for ServiceImmediately ITSM (:8082)
│   └── app_utils/                # Telemetry, typing, and deployment helpers
├── src/hr_agent/                 # Core domain engines & deterministic state machines
│   ├── bff.py                    # BFF gateway, JWT validation, UCT minting, Redis rate limiter, SSE envelope
│   ├── models.py                 # Canonical data contracts, ErrorCode enum, ActionPlanState, IdempotencyStatus
│   ├── orchestrator.py           # AgentCoordinator, DeterministicPlanExecutor, IdempotencyStateMachine
│   ├── policy_rag.py             # Country-scoped Vertex AI Search & CheckGrounding (>= 0.80) engine
│   ├── workweek_mcp.py           # WorkWeek HCM MCP server implementation (6 tools)
│   ├── servicedesk_mcp.py        # ServiceImmediately ITSM MCP server implementation (7 tools, P-01..P-13)
│   ├── security.py               # UCT/CT cryptographic signer, Model Armor, Cloud DLP redactor
│   └── ops.py                    # FinOps budget governor, Cloud Trace sanitizer, BigQuery audit sink
├── specs/
│   ├── workweek-v1.0.0.yaml      # OpenAPI 3.1.0 contract for WorkWeek HCM
│   └── service-immediately-v1.0.0.yaml # OpenAPI 3.1.0 contract for ServiceImmediately ITSM
├── knowledge/                    # 35 country-scoped HR policy handbooks & chunks (NL, DE, CN, GLOBAL)
├── deployment/
│   ├── cd/cloudbuild.yaml        # Cloud Build CI/CD pipeline with 7 release gates
│   └── terraform/                # Terraform IaC for Vertex AI Agent Engine, Cloud Run, Firestore, Secret Manager
├── scripts/
│   ├── run_all_evals.py          # Executes 28-scenario 4-tier stratified golden evaluation suite
│   ├── assert_eval_thresholds.py # Enforces release gate thresholds (Completion >= 0.88, Safety = 1.00)
│   ├── assert_latency_sla.py     # Enforces p95 latency budgets (Policy <= 3.5s, Single <= 4.5s, Composite <= 6.5s)
│   ├── verify_openapi_allowlist.py
│   └── verify_openapi_contract.py
└── tests/
    ├── unit/                     # 164 unit, integration, security, performance, e2e & guardrail tests
    ├── integration/
    ├── security/
    ├── performance/
    ├── e2e/
    ├── guardrails/
    └── eval/
        ├── eval_config.yaml      # ADK / agents-cli evaluation metric definitions
        ├── eval_config.json      # Release gate thresholds & latency budgets
        ├── evaluation_report.md  # Full Section 1 & Section 2 AI Agent Evaluation Report
        └── datasets/             # 28-scenario 4-tier stratified golden datasets (eval-data.json)
```

---

## 3. Environment Variables & Configuration

Copy `.env.example` to `.env` to configure local development or Cloud Run / Vertex AI Agent Engine deployment:

```bash
cp .env.example .env
```

Key environment variables (all have safe local defaults):
- `GOOGLE_CLOUD_PROJECT`: Target GCP project (e.g., `argolis-sandbox-main`)
- `GOOGLE_CLOUD_LOCATION`: Target region (`us-central1`)
- `HR_AGENT_MODEL`: Primary model (`gemini-3.5-flash`, automatic fallback to `gemini-2.5-flash`)
- `MOCK_SAAS_BASE_URL`: Hosted Mock SaaS base URL (`https://mock-saas.aishprabhat.demo.altostrat.com`)
- `MOCK_SAAS_DOCS_URL`: Mock SaaS Swagger UI (`https://mock-saas.aishprabhat.demo.altostrat.com/docs`)
- `MOCK_SAAS_OPENAPI_URL`: Mock SaaS OpenAPI JSON (`https://mock-saas.aishprabhat.demo.altostrat.com/openapi.json`)
- `MOCK_SAAS_IAP_CLIENT_ID`: IAP OAuth2 Client ID (`332638604308-825bkuk2cjp8h06t1g41o2lvk8oq9cnk.apps.googleusercontent.com`)
- `UPSTREAM_TRANSPORT_MODE`: `memory` (deterministic local/CI harness) or `live` (authenticated IAP OIDC HTTP calls)
- `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`: Set to `NO_CONTENT` to prevent PII leakage in Cloud Trace spans
- `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS`: Set to `false`

---

## 4. Setup, Testing & Evaluation Commands

### Install Dependencies
```bash
make install
# or: uv sync --extra eval --extra lint
```

### Run Code Quality & OpenAPI Contract Verification
```bash
make lint
make verify-specs
```

### Run All 164 Unit, Integration, Security, Performance, E2E & Guardrail Tests
```bash
make test
# or: uv run pytest -q
```

### Run the 28-Scenario 4-Tier Stratified Evaluation Suite & Release Gates
```bash
make eval
# Runs:
#   uv run python scripts/run_all_evals.py
#   uv run python scripts/assert_eval_thresholds.py
#   uv run python scripts/assert_latency_sla.py
```

The evaluation report is generated at [`tests/eval/evaluation_report.md`](tests/eval/evaluation_report.md) and HTML/JSON grade artifacts are written to `artifacts/grade_results/`.

---

## 5. Local Execution & Deployment to GCP

### Launch Local Interactive Playground & MCP Proxies
```bash
# Start ADK Web Playground (:8000)
make run-playground

# Start WorkWeek HCM MCP Proxy (:8081)
make run-workweek-mcp

# Start ServiceImmediately ITSM MCP Proxy (:8082)
make run-servicedesk-mcp
```

### Deploy to Google Cloud (Vertex AI Agent Engine & Cloud Run)
```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project argolis-sandbox-main
make deploy
# or: agents-cli deploy --project argolis-sandbox-main --region us-central1
```
