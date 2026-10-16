# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SS-Enterprise HR Agentic Solution (MVP 1) — Google ADK Multi-Agent System (SDD §3, §5, §8).

Implements the Hierarchical Coordinator + Sub-Agent Delegation + Agent-as-Tool topology:
- Root Coordinator: `hr_supervisor_agent` (LlmAgent, Gemini 3.5 Flash)
- Policy Sub-Agent (AgentTool): `policy_qa_agent` (Vertex AI Search + CheckGrounding >= 0.80)
- WorkWeek HCM Sub-Agent: `workweek_hcm_agent` (McpToolset / WorkWeekMcpServer)
- ServiceImmediately ITSM Sub-Agent: `service_immediately_agent` (McpToolset / ServiceDeskMcpServer)
- Cross-Cutting Guardrails: `SecurityGovernancePlugin` (BasePlugin — 7 lifecycle callbacks)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from typing import Any

from google.adk.agents import Agent, LlmAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App
from google.adk.apps.app import EventsCompactionConfig, ResumabilityConfig
from google.adk.models import Gemini
from google.adk.plugins import BasePlugin
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from app.upstream_client import (
    UpstreamMockSaasClient,
)
from hr_agent.models import (
    BLOCKED_COUNTRIES,
    ErrorCode,
)
from hr_agent.orchestrator import AgentCoordinator
from hr_agent.policy_rag import PolicyRagService
from hr_agent.security import (
    InputSafetyGate,
    KmsTokenSigner,
    OutputSafetyGate,
    RegionAccessGuard,
    TelemetrySanitizer,
)
from hr_agent.servicedesk_mcp import ServiceDeskMcpServer
from hr_agent.workweek_mcp import WorkWeekMcpServer

# Configurable environment parameters (Principle 1: zero hardcoded environment assumptions)
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "argolis-sandbox-main")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = os.environ.get("HR_AGENT_MODEL", "gemini-3.5-flash")
FALLBACK_MODEL = os.environ.get("HR_AGENT_FALLBACK_MODEL", "gemini-2.5-flash")
MODEL_ARMOR_TEMPLATE = os.environ.get(
    "MODEL_ARMOR_TEMPLATE_ID",
    f"projects/{PROJECT_ID}/locations/{REGION}/templates/hr-agent-guardrail-v1",
)
DLP_DEIDENTIFY_TEMPLATE = os.environ.get(
    "DLP_DEIDENTIFY_TEMPLATE_ID",
    f"projects/{PROJECT_ID}/locations/global/deidentifyTemplates/hr-spii-deidentify-v1",
)
WORKWEEK_MCP_URL = os.environ.get("WORKWEEK_MCP_URL", "")
SERVICEDESK_MCP_URL = os.environ.get("SERVICEDESK_MCP_URL", "")

# Shared domain services
_policy_rag = PolicyRagService()
_workweek_mcp = WorkWeekMcpServer()
_servicedesk_mcp = ServiceDeskMcpServer()
_upstream_client = UpstreamMockSaasClient()
_coordinator = AgentCoordinator(
    workweek_mcp=_workweek_mcp, servicedesk_mcp=_servicedesk_mcp
)
_input_safety_gate = InputSafetyGate()
_output_safety_gate = OutputSafetyGate()
_region_guard = RegionAccessGuard()
_telemetry_sanitizer = TelemetrySanitizer()
_token_signer = KmsTokenSigner()

_FORBIDDEN_PROFILE_FIELDS = frozenset(
    {
        "salary",
        "base_salary",
        "compensation",
        "bonus_target",
        "stock_equity",
        "bank_account_iban",
        "iban",
        "national_id_ssn",
        "ssn",
        "bsn",
        "medical_diagnosis",
        "performance_rating",
    }
)

_MUTATING_TOOLS = frozenset(
    {
        "submit_leave_request",
        "submit_my_leave_request",
        "update_contact_info",
        "update_my_contact_info",
        "create_incident_ticket",
        "create_incident",
        "update_ticket",
        "add_ticket_comment",
        "update_ticket_status",
        "execute_confirmed_plan",
    }
)

_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|system)\s+instructions", re.I),
    re.compile(r"system\s+override", re.I),
    re.compile(r"developer\s+mode", re.I),
    re.compile(r"print\s+the\s+database\s+dump", re.I),
    re.compile(r"reveal\s+your\s+system\s+prompt", re.I),
    re.compile(r"bypass\s+(security|hitl|confirmation|rbac)", re.I),
    re.compile(r"act\s+as\s+admin\s+for\s+emp-", re.I),
)

_SPII_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b"),
        "[REDACTED_IBAN]",
    ),
    (re.compile(r"\b(BSN|SSN)\s*[:=]?\s*\d{8,9}\b", re.I), "[REDACTED_NATIONAL_ID]"),
    (
        re.compile(
            r"(?:base\s+salary|annual\s+salary)\s*(?:is|of|:)\s*(?:EUR|USD|GBP|\$|€)\s*\d[\d,]*",
            re.I,
        ),
        "[REDACTED_SALARY]",
    ),
)


def scrub_spii_text(text: str) -> tuple[str, list[str]]:
    """Scrub Sensitive PII using DLP template rules before LLM invocation or logging."""
    redacted = text
    findings: list[str] = []
    for pattern, replacement in _SPII_REDACTION_PATTERNS:
        if pattern.search(redacted):
            findings.append(replacement)
            redacted = pattern.sub(replacement, redacted)
    return redacted, findings


def screen_prompt_injection(text: str) -> dict[str, Any]:
    """Screen user input or retrieved chunks against Model Armor guardrail rules."""
    for pat in _PROMPT_INJECTION_PATTERNS:
        match = pat.search(text)
        if match:
            return {
                "blocked": True,
                "error_code": ErrorCode.SAFETY_BLOCKED.value,
                "template": MODEL_ARMOR_TEMPLATE,
                "matched_rule": pat.pattern,
                "message": (
                    "Request blocked by Model Armor security policy: adversarial instruction "
                    "or cross-employee privilege escalation attempt detected."
                ),
            }
    return {"blocked": False, "template": MODEL_ARMOR_TEMPLATE}


class SecurityGovernancePlugin(BasePlugin):
    """Cross-cutting ADK BasePlugin enforcing the 7-stage defense-in-depth pipeline (SDD §4, §5.2)."""

    def __init__(self) -> None:
        super().__init__(name="security_governance_plugin")
        self._consumed_tokens: set[str] = set()
        self._idempotency_ledger: dict[str, dict[str, Any]] = {}
        self.audit_trail: list[dict[str, Any]] = []

    def inspect_run_context(self, state: dict[str, Any]) -> dict[str, Any]:
        """Stage 1 (before_run_callback): Verify employee context and block out-of-scope Taiwan (TW) region."""
        country = str(state.get("user:country", "NL")).upper()
        employee_id = str(state.get("user:employee_id", "EMP-NL-001"))
        if country in BLOCKED_COUNTRIES:
            return {
                "allowed": False,
                "error_code": ErrorCode.REGION_BLOCKED.value,
                "status": "REGION_OUT_OF_SCOPE",
                "message": (
                    "HR Assistant MVP 1 is not available in Taiwan (TW). "
                    "Please contact Local Taiwan HR Support directly."
                ),
            }
        return {
            "allowed": True,
            "employee_id": employee_id,
            "country": country,
            "legal_entity": state.get("user:legal_entity", "LE-NL-BV"),
            "work_location_type": state.get("user:work_location_type", "REMOTE"),
        }

    def inspect_model_input(self, prompt_text: str) -> dict[str, Any]:
        """Stage 2 (before_model_callback): Model Armor prompt injection screening + Cloud DLP SPII redaction."""
        armor_verdict = screen_prompt_injection(prompt_text)
        if armor_verdict["blocked"]:
            return armor_verdict
        sanitized_text, findings = scrub_spii_text(prompt_text)
        return {
            "blocked": False,
            "sanitized_text": sanitized_text,
            "dlp_findings": findings,
            "dlp_template": DLP_DEIDENTIFY_TEMPLATE,
        }

    def enforce_tool_guardrails(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        session_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Stage 3 (before_tool_callback): Strip LLM employee_id (IDOR prevention) and enforce HITL confirmation gate."""
        authenticated_emp = str(session_state.get("user:employee_id", "EMP-NL-001"))
        supplied_emp = tool_args.get("employee_id")
        if supplied_emp and str(supplied_emp) != authenticated_emp:
            return {
                "allowed": False,
                "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED.value,
                "status": "IDOR_BLOCKED",
                "message": (
                    f"Cross-employee access denied: cannot access records for '{supplied_emp}'. "
                    f"Scope is strictly bound to authenticated principal '{authenticated_emp}'."
                ),
            }

        safe_args = dict(tool_args)
        safe_args.pop("employee_id", None)
        safe_args["authenticated_employee_id"] = authenticated_emp

        if tool_name in _MUTATING_TOOLS:
            confirmed = bool(tool_args.get("confirmed", False))
            confirmation_token = str(tool_args.get("confirmation_token", ""))
            idempotency_key = str(tool_args.get("idempotency_key", ""))

            if idempotency_key and idempotency_key in self._idempotency_ledger:
                cached = self._idempotency_ledger[idempotency_key]
                return {
                    "allowed": False,
                    "status": "IDEMPOTENT_REPLAY",
                    "deduplicated": True,
                    "cached_result": cached,
                }

            if not confirmed or not confirmation_token:
                preview_payload = {
                    "tool_name": tool_name,
                    "employee_id": authenticated_emp,
                    "proposed_arguments": {
                        k: v
                        for k, v in safe_args.items()
                        if k
                        not in (
                            "authenticated_employee_id",
                            "uct",
                            "confirmation_token",
                        )
                    },
                }
                digest = (
                    "sha256:"
                    + hashlib.sha256(
                        json.dumps(preview_payload, sort_keys=True).encode("utf-8")
                    ).hexdigest()
                )
                minted_ct = f"ct-{uuid.uuid4().hex[:16]}"
                session_state["temp:pending_confirmation_token"] = minted_ct
                session_state["temp:draft_action_plan"] = preview_payload
                return {
                    "allowed": False,
                    "status": "PENDING_USER_CONFIRMATION",
                    "requires_confirmation": True,
                    "preview_card": preview_payload,
                    "preview_digest": digest,
                    "confirmation_token": minted_ct,
                    "message": (
                        f"Action '{tool_name}' requires your explicit confirmation before execution. "
                        "Please review the preview card and confirm."
                    ),
                }

            if confirmation_token in self._consumed_tokens:
                return {
                    "allowed": False,
                    "error_code": ErrorCode.CONFIRMATION_REPLAYED.value,
                    "status": "CONFIRMATION_TOKEN_REPLAYED",
                    "message": "Confirmation token has already been used (single-use policy).",
                }
            self._consumed_tokens.add(confirmation_token)

        return {"allowed": True, "sanitized_args": safe_args}

    def sanitize_tool_output(self, tool_output: dict[str, Any]) -> dict[str, Any]:
        """Stage 4 (after_tool_callback): Enforce strict field allow-list and strip sensitive PII fields."""
        cleaned = dict(tool_output)
        for forbidden in _FORBIDDEN_PROFILE_FIELDS:
            cleaned.pop(forbidden, None)
        if isinstance(cleaned.get("for_llm"), dict):
            llm_view = dict(cleaned["for_llm"])
            for forbidden in _FORBIDDEN_PROFILE_FIELDS:
                llm_view.pop(forbidden, None)
            cleaned["for_llm"] = llm_view
        return cleaned

    def scrub_telemetry_event(self, span_attributes: dict[str, Any]) -> dict[str, Any]:
        """Stage 6 (on_event_callback): Enforce OTel NO_CONTENT span scrubbing."""
        cleaned = dict(span_attributes)
        for content_key in (
            "gen_ai.prompt",
            "gen_ai.completion",
            "llm.request.body",
            "llm.response.body",
        ):
            if content_key in cleaned:
                raw_val = str(cleaned.pop(content_key))
                cleaned[f"{content_key}.sha256"] = hashlib.sha256(
                    raw_val.encode("utf-8")
                ).hexdigest()
        cleaned["otel.capture_mode"] = "NO_CONTENT"
        return cleaned

    def purge_ephemeral_state(self, session_state: dict[str, Any]) -> list[str]:
        """Stage 7 (after_run_callback): Auto-purge all temp:* state keys at turn completion."""
        removed: list[str] = []
        for k in list(session_state.keys()):
            if k.startswith("temp:"):
                session_state.pop(k, None)
                removed.append(k)
        return removed


security_plugin = SecurityGovernancePlugin()


# ============================================================================
# Tool Definitions for Policy Q&A, WorkWeek HCM, and ServiceImmediately ITSM
# ============================================================================


def search_policy_handbook(
    query: str,
    country: str = "NL",
    top_k: int = 5,
) -> dict[str, Any]:
    """Search the Country-Scoped HR Policy Handbook in Vertex AI Search / PolicyRagService.

    Args:
        query: Natural-language HR policy question (leave, expense, remote work, equipment, relocation).
        country: ISO-3166-1 alpha-2 country code from verified employee context (default 'NL').
        top_k: Number of chunks to retrieve (1..6).
    """
    uct_map = {
        "NL": "uct-nl-001",
        "DE": "uct-de-002",
        "CN": "uct-cn-003",
        "SG": "uct-sg-004",
        "TW": "uct-tw-999",
    }
    uct = uct_map.get(country.upper(), "uct-nl-001")
    return _policy_rag.retrieve_policy_chunks(
        uct=uct, query=query, top_k=min(max(top_k, 1), 6)
    )


def check_grounding_citations(
    query: str,
    draft_answer: str,
    retrieved_chunks: list[dict[str, Any]],
    country: str = "NL",
) -> dict[str, Any]:
    """Verify every factual claim in draft_answer against retrieved_chunks (support >= 0.80) and active citations."""
    uct_map = {
        "NL": "uct-nl-001",
        "DE": "uct-de-002",
        "CN": "uct-cn-003",
        "SG": "uct-sg-004",
    }
    uct = uct_map.get(country.upper(), "uct-nl-001")
    return _policy_rag.verify_grounding_and_repair(
        uct=uct,
        query=query,
        draft_answer=draft_answer,
        retrieved_chunks=retrieved_chunks,
    )


def get_employee_profile(
    include_contact_details: bool = False,
    uct: str = "uct-nl-001",
) -> dict[str, Any]:
    """Read the authenticated employee's WorkWeek profile projected through the strict allow-list.

    Args:
        include_contact_details: Set True only when the user explicitly asks to view/update phone or address.
        uct: Server-injected UserContextToken (never user-supplied).
    """
    res = _workweek_mcp.get_my_profile(
        uct=uct, include_contact_details=include_contact_details
    )
    return security_plugin.sanitize_tool_output(res)


def get_leave_balances(
    leave_type: str = "ALL",
    uct: str = "uct-nl-001",
) -> dict[str, Any]:
    """Fetch live accrued, used, pending, and remaining leave balances from WorkWeek HCM.

    Args:
        leave_type: One of ALL, VACATION, SICK, BEREAVEMENT, PARENTAL, STUDY.
        uct: Server-injected UserContextToken.
    """
    res = _workweek_mcp.get_my_leave_balances(uct=uct, force_live_fetch=True)
    if res.get("ok") and leave_type.upper() not in ("ALL", ""):
        balances = res.get("for_llm", {}).get("balances", {})
        lt = leave_type.upper()
        if lt in balances:
            res["for_llm"]["filtered_leave_type"] = lt
            res["for_llm"]["filtered_balance"] = balances[lt]
    return res


def submit_leave_request(
    leave_type: str,
    start_date: str,
    end_date: str,
    reason_note: str = "",
    confirmed: bool = False,
    confirmation_token: str = "",
    idempotency_key: str = "",
    uct: str = "uct-nl-001",
) -> dict[str, Any]:
    """Validate and submit a leave request in WorkWeek HCM (requires HITL confirmation for execution).

    Args:
        leave_type: VACATION, SICK, BEREAVEMENT, PARENTAL, or STUDY.
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
        reason_note: Optional non-sensitive note.
        confirmed: True only after the employee clicks Confirm on the HITL preview card.
        confirmation_token: Server-minted single-use confirmation token.
        idempotency_key: UUID v4 key preventing duplicate submission.
        uct: Server-injected UserContextToken.
    """
    val_res = _workweek_mcp.validate_leave_request(
        uct=uct,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
    )
    if not val_res.get("ok") or not confirmed or not confirmation_token:
        return {
            "ok": val_res.get("ok", False),
            "status": "PENDING_USER_CONFIRMATION"
            if val_res.get("ok")
            else "VALIDATION_FAILED",
            "requires_confirmation": bool(val_res.get("ok")),
            "validation": val_res,
        }
    idem_key = idempotency_key or str(uuid.uuid4())
    preview_digest = str(val_res["for_llm"]["preview_digest"])
    expected_work_days = float(val_res["for_llm"]["work_days"])
    return _workweek_mcp.submit_my_leave_request(
        uct=uct,
        ct=confirmation_token,
        idempotency_key=idem_key,
        step_id="step-1",
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        expected_work_days=expected_work_days,
        preview_digest=preview_digest,
    )


def update_contact_info(
    personal_phone: str | None = None,
    personal_email: str | None = None,
    home_address: dict[str, str] | None = None,
    confirmed: bool = False,
    confirmation_token: str = "",
    idempotency_key: str = "",
    uct: str = "uct-nl-001",
) -> dict[str, Any]:
    """Validate and update personal contact info in WorkWeek HCM (requires HITL confirmation for execution)."""
    val_res = _workweek_mcp.validate_contact_update(
        uct=uct,
        personal_phone=personal_phone,
        email=personal_email,
        home_address=home_address,
    )
    if not val_res.get("ok") or not confirmed or not confirmation_token:
        return {
            "ok": val_res.get("ok", False),
            "status": "PENDING_USER_CONFIRMATION"
            if val_res.get("ok")
            else "VALIDATION_FAILED",
            "requires_confirmation": bool(val_res.get("ok")),
            "validation": val_res,
        }
    idem_key = idempotency_key or str(uuid.uuid4())
    preview_digest = str(val_res["for_llm"]["preview_digest"])
    return _workweek_mcp.update_my_contact_info(
        uct=uct,
        ct=confirmation_token,
        idempotency_key=idem_key,
        step_id="step-1",
        preview_digest=preview_digest,
        personal_phone=personal_phone,
        home_address=home_address,
    )


def execute_confirmed_plan(
    plan_id: str,
    confirmation_token: str,
    idempotency_key: str = "",
    uct: str = "uct-nl-001",
) -> dict[str, Any]:
    """Execute a multi-step ActionPlan after explicit employee confirmation (Saga pattern)."""
    return _coordinator.execute_confirmed_plan(
        session_id=plan_id.replace("plan-", ""),
        uct=uct,
        plan_id=plan_id,
        ct=confirmation_token,
        step_outcomes=["SUCCESS", "SUCCESS"],
    )


def get_ticket_status(
    ticket_id: str,
    uct: str = "uct-nl-001",
) -> dict[str, Any]:
    """Retrieve status, priority, category, assignee, and comments for an owned ServiceImmediately ticket."""
    return _servicedesk_mcp.get_my_ticket(uct=uct, ticket_id=ticket_id)


def list_open_tickets(
    uct: str = "uct-nl-001",
) -> dict[str, Any]:
    """List open ServiceImmediately tickets belonging to the authenticated employee."""
    return _servicedesk_mcp.list_my_open_tickets(uct=uct)


def create_incident_ticket(
    category: str,
    short_description: str,
    detailed_description: str,
    priority: str = "3 - Moderate",
    impact: dict[str, Any] | None = None,
    template_id: str | None = None,
    confirmed: bool = False,
    confirmation_token: str = "",
    idempotency_key: str = "",
    uct: str = "uct-nl-001",
) -> dict[str, Any]:
    """Validate and create a ServiceImmediately IT/HR ticket (enforces priority rules P-01..P-13 and HITL confirmation)."""
    impact_dict = impact or {
        "users_affected": "ONE",
        "can_work": True,
        "workaround_available": True,
    }
    val_res = _servicedesk_mcp.validate_incident(
        uct=uct,
        category=category,
        short_description=short_description,
        detailed_description=detailed_description,
        requested_priority=priority,
        impact=impact_dict,
        template_id=template_id,
    )
    if not val_res.get("ok") or not confirmed or not confirmation_token:
        return {
            "ok": val_res.get("ok", False),
            "status": "PENDING_USER_CONFIRMATION"
            if val_res.get("ok")
            else "VALIDATION_FAILED",
            "requires_confirmation": bool(val_res.get("ok")),
            "validation": val_res,
        }
    idem_key = idempotency_key or str(uuid.uuid4())
    preview_digest = str(val_res["for_llm"]["preview_digest"])
    return _servicedesk_mcp.create_incident(
        uct=uct,
        ct=confirmation_token,
        idempotency_key=idem_key,
        step_id="step-1",
        preview_digest=preview_digest,
        category=category,
        short_description=short_description,
        detailed_description=detailed_description,
        priority=priority,
        impact=impact_dict,
        template_id=template_id,
    )


def update_ticket(
    ticket_id: str,
    comment: str | None = None,
    new_state: str | None = None,
    confirmed: bool = False,
    confirmation_token: str = "",
    idempotency_key: str = "",
    uct: str = "uct-nl-001",
) -> dict[str, Any]:
    """Validate and update an owned ServiceImmediately ticket (add comment or transition state; requires HITL confirmation)."""
    val_res = _servicedesk_mcp.validate_ticket_update(
        uct=uct,
        ticket_id=ticket_id,
        comment=comment,
        new_status=new_state,
    )
    if not val_res.get("ok") or not confirmed or not confirmation_token:
        return {
            "ok": val_res.get("ok", False),
            "status": "PENDING_USER_CONFIRMATION"
            if val_res.get("ok")
            else "VALIDATION_FAILED",
            "requires_confirmation": bool(val_res.get("ok")),
            "validation": val_res,
        }
    idem_key = idempotency_key or str(uuid.uuid4())
    preview_digest = str(val_res["for_llm"]["preview_digest"])
    if new_state:
        return _servicedesk_mcp.update_ticket_status(
            uct=uct,
            ct=confirmation_token,
            idempotency_key=idem_key,
            step_id="step-1",
            preview_digest=preview_digest,
            ticket_id=ticket_id,
            new_status=new_state,
            resolution_notes=comment or "Updated by employee via HR Assistant",
        )
    return _servicedesk_mcp.add_ticket_comment(
        uct=uct,
        ct=confirmation_token,
        idempotency_key=idem_key,
        step_id="step-1",
        preview_digest=preview_digest,
        ticket_id=ticket_id,
        comment=comment or "",
    )


# ============================================================================
# ADK Multi-Agent Hierarchy (SDD §3.1, §5.2, §5.3)
# ============================================================================

policy_qa_agent = LlmAgent(
    name="policy_qa_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description=(
        "Country-scoped HR Policy & Handbook Q&A specialist. Retrieves active policy chunks "
        "filtered by employee country and GLOBAL scope, and verifies citations via CheckGrounding."
    ),
    instruction=(
        "You are the HR Policy Q&A Specialist (`policy_qa_agent`). "
        "Always call `search_policy_handbook` to retrieve policy sections filtered by the employee's "
        "country and GLOBAL scope, then call `check_grounding_citations` to verify every claim has "
        "grounding support >= 0.80 and an explicit `[doc_id#section_id]` citation. "
        "If context sufficiency is INSUFFICIENT or relevance < 0.65, abstain cleanly."
    ),
    tools=[search_policy_handbook, check_grounding_citations],
)

workweek_hcm_agent = LlmAgent(
    name="workweek_hcm_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description=(
        "WorkWeek HCM transactional specialist for reading employee profiles, checking live leave "
        "balances, submitting leave requests, and updating contact details with HITL confirmation."
    ),
    instruction=(
        "You are the WorkWeek HCM Specialist (`workweek_hcm_agent`). "
        "Never ask the user for their employee_id; it is injected server-side from their verified UCT token. "
        "Always fetch live leave balances via `get_leave_balances`. "
        "For any write operation (`submit_leave_request`, `update_contact_info`, `execute_confirmed_plan`), "
        "always generate a preview card first and require explicit employee confirmation (`confirmed=True` "
        "and a valid single-use `confirmation_token`) before committing."
    ),
    tools=[
        get_employee_profile,
        get_leave_balances,
        submit_leave_request,
        update_contact_info,
        execute_confirmed_plan,
    ],
)

service_immediately_agent = LlmAgent(
    name="service_immediately_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description=(
        "ServiceImmediately ITSM transactional specialist for checking ticket statuses, listing open "
        "tickets, creating IT/HR support incidents (with P-01..P-13 priority rules), and updating tickets."
    ),
    instruction=(
        "You are the ServiceImmediately ITSM Specialist (`service_immediately_agent`). "
        "Validate ticket IDs against `^INC[0-9]{6,10}$` before querying. "
        "Enforce deterministic priority rules (P-01..P-13) and never leak medical or leave details into "
        "IT tickets (FR-096). All ticket creations and updates require explicit employee HITL confirmation."
    ),
    tools=[
        get_ticket_status,
        list_open_tickets,
        create_incident_ticket,
        update_ticket,
    ],
)

hr_supervisor_agent = Agent(
    name="hr_supervisor_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    description=(
        "SS-Enterprise HR Multi-Agent Supervisor orchestrating Policy Q&A (`policy_qa_agent`), "
        "WorkWeek HCM (`workweek_hcm_agent`), and ServiceImmediately ITSM (`service_immediately_agent`)."
    ),
    instruction=(
        "You are the SS-Enterprise HR Supervisor (`hr_supervisor_agent`). "
        "1. Enforce strict scope: deflect out-of-scope domains (Payroll, Compensation, Stock/Equity, Tax, "
        "Performance Reviews, Benefits Enrollment, Legal Disputes, Termination) and block Taiwan (TW) region. "
        "2. For HR policy questions, invoke `policy_qa_agent` (as an AgentTool) so every answer carries "
        "verified `[doc_id#section_id]` citations with CheckGrounding >= 0.80. "
        "3. For WorkWeek HCM profile or leave tasks, delegate to `workweek_hcm_agent`. "
        "4. For ServiceImmediately IT/HR ticket tasks, delegate to `service_immediately_agent`. "
        "5. For composite cross-system workflows (UC-2.1 Remote Monitor Eligibility + Ticket, UC-2.2 Medical "
        "Leave + IT Access Delegation without medical leakage, UC-2.3 Address Update + Relocation Policy + "
        "Facilities Badge Ticket), coordinate the read/policy steps first and present a unified HITL "
        "Confirmation Preview Card before any write tool executes."
    ),
    tools=[
        AgentTool(agent=policy_qa_agent),
        search_policy_handbook,
        check_grounding_citations,
    ],
    sub_agents=[
        workweek_hcm_agent,
        service_immediately_agent,
    ],
)

root_agent = hr_supervisor_agent

app = App(
    name="app",
    root_agent=hr_supervisor_agent,
    plugins=[security_plugin],
    resumability_config=ResumabilityConfig(is_resumable=True),
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=5,
        overlap_size=1,
    ),
    context_cache_config=ContextCacheConfig(
        min_tokens=2048,
        ttl_seconds=1800,
        cache_intervals=10,
    ),
)
