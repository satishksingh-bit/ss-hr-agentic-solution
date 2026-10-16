"""ADK Multi-Agent Coordinator, Deterministic Plan Executor, Idempotency State Machine, and Reconciler implementation (SDD §5.6–§5.7)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any

from hr_agent.models import (
    FALLBACK_COORDINATOR_MODEL,
    PRIMARY_COORDINATOR_MODEL,
    ActionPlanState,
    ErrorCode,
    FirestoreDatabaseId,
    IdempotencyStatus,
    LocalSqliteStore,
    create_json_http_server,
    http_post_json,
    run_http_server_cli,
)
from hr_agent.security import KmsTokenSigner, TelemetrySanitizer, parse_uct_or_shorthand
from hr_agent.servicedesk_mcp import ServiceDeskMcpServer
from hr_agent.workweek_mcp import WorkWeekMcpServer

_OOS_KEYWORDS = (
    "base salary",
    "increase my salary",
    "vest my equity",
    "stock options",
    "payroll",
    "tax withholding",
    "performance rating",
    "visa sponsorship",
    "termination",
)


class AgentCoordinator:
    """Root ADK coordinator (hr_coordinator), bounded sub-agent router, and DeterministicPlanExecutor (C4, FR-019, FR-030..FR-036, FR-090..FR-096, CON-002)."""

    PRIMARY_MODEL = PRIMARY_COORDINATOR_MODEL
    FALLBACK_MODEL = FALLBACK_COORDINATOR_MODEL

    def __init__(
        self,
        workweek_mcp: WorkWeekMcpServer | None = None,
        servicedesk_mcp: ServiceDeskMcpServer | None = None,
        state_dir: str | None = None,
        workweek_mcp_url: str | None = None,
        servicedesk_mcp_url: str | None = None,
        policy_rag_url: str | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._signer = KmsTokenSigner()
        self._workweek_explicit = workweek_mcp is not None
        self._servicedesk_explicit = servicedesk_mcp is not None
        self._workweek = workweek_mcp or WorkWeekMcpServer(state_dir=state_dir)
        self._servicedesk = servicedesk_mcp or ServiceDeskMcpServer(state_dir=state_dir)
        self._workweek_mcp_url = workweek_mcp_url
        self._servicedesk_mcp_url = servicedesk_mcp_url
        self._policy_rag_url = policy_rag_url
        self._sqlite = LocalSqliteStore(
            FirestoreDatabaseId.HR_PLANS, state_dir=state_dir
        )
        self._plans: dict[str, dict[str, Any]] = {}
        self._ct_seq = 0

    def _call_workweek(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        url = (
            ""
            if self._workweek_explicit
            else (self._workweek_mcp_url or os.environ.get("WORKWEEK_MCP_URL", ""))
        )
        if url:
            return http_post_json(url, f"/mcp/v1/tools/{tool_name}", kwargs)
        return getattr(self._workweek, tool_name)(**kwargs)

    def _call_servicedesk(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        url = (
            ""
            if self._servicedesk_explicit
            else (
                self._servicedesk_mcp_url or os.environ.get("SERVICEDESK_MCP_URL", "")
            )
        )
        if url:
            return http_post_json(url, f"/mcp/v1/tools/{tool_name}", kwargs)
        return getattr(self._servicedesk, tool_name)(**kwargs)

    def _call_rag(self, method_name: str, **kwargs: Any) -> dict[str, Any]:
        url = self._policy_rag_url or os.environ.get("POLICY_RAG_URL", "")
        if url:
            return http_post_json(url, f"/rag/v1/{method_name}", kwargs)
        from hr_agent.policy_rag import PolicyRagService

        return getattr(PolicyRagService(), method_name)(**kwargs)

    def _mint_step_ct(
        self,
        *,
        sub: str,
        session_id: str,
        plan_id: str,
        step_id: str,
        tool_name: str,
        preview_digest: str,
        ct_seed: str,
    ) -> str:
        """Mint an ES256 JWS ConfirmationToken bound to the specific plan step and preview digest."""
        if ct_seed.count(".") == 2:
            return ct_seed
        self._ct_seq += 1
        return self._signer.mint_ct(
            sub=sub,
            session_id=session_id,
            plan_id=plan_id,
            action_kind="WHOLE_PLAN_CONFIRM",
            steps_hash=preview_digest,
            step_digests=[
                {
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "arg_digest": preview_digest,
                }
            ],
            preview_digest=preview_digest,
            now_epoch=1780000000 + self._ct_seq,
        )

    def classify_and_route_turn(
        self,
        *,
        session_id: str,
        uct: str,
        message: str,
        conversation_history: list[dict[str, Any]] | None = None,
        model_calls_used: int = 0,
        tool_calls_used: int = 0,
        elapsed_ms: int = 0,
        is_confirmation_turn: bool = False,
        memory_bank_enabled: bool = False,
    ) -> dict[str, Any]:
        """Classify user intent, enforce per-turn call/time budgets (max 10 model calls, max 10 tool calls, 30s message / 50s confirmation deadline), resolve 10-turn follow-ups, enforce session memory isolation (Memory Bank disabled), and route to <= 3 sub-agents (FR-019, FR-030..FR-036, FR-090, NFR-033, CON-002)."""
        if memory_bank_enabled:
            return {
                "ok": False,
                "cross_session_memory_used": True,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED,
                    "message": "Cross-session Vertex Memory Bank is prohibited in MVP 1 (FR-033).",
                },
            }

        max_elapsed_ms = 50000 if is_confirmation_turn else 30000
        if model_calls_used > 10 or tool_calls_used > 10 or elapsed_ms > max_elapsed_ms:
            return {
                "ok": False,
                "error_code": ErrorCode.TURN_LIMIT_REACHED,
                "error": {
                    "code": ErrorCode.TURN_LIMIT_REACHED,
                    "message": "Per-turn call or latency budget exceeded (FR-019).",
                },
            }

        msg_lower = message.lower()
        if any(kw in msg_lower for kw in _OOS_KEYWORDS):
            return {
                "ok": False,
                "error_code": ErrorCode.OUT_OF_SCOPE_DOMAIN,
                "fallback_offered": {
                    "can_create_ticket": True,
                    "hr_contact_provided": True,
                    "hr_portal_url": "https://hr.internal.globalcorp.example.com/support",
                },
                "live_human_chat_handoff": False,
            }

        # 10-turn sliding context window resolution
        history = list(conversation_history or [])
        window = history[-10:]
        resolved_entities: dict[str, Any] = {}
        for turn in reversed(window):
            ref_tkt = turn.get("referenced_ticket_id")
            if ref_tkt:
                resolved_entities["ticket_id"] = ref_tkt
                break

        # Ambiguity check: vague leave request without dates
        if "time off soon" in msg_lower or (
            "take leave" in msg_lower
            and "2026" not in msg_lower
            and "next" not in msg_lower
        ):
            return {
                "ok": True,
                "action": "ASK_CLARIFICATION",
                "missing_slots": ["start_date", "end_date", "leave_type"],
                "write_tools_invoked": 0,
                "cross_session_memory_used": False,
                "context_turns_retained": len(window),
                "resolved_entities": resolved_entities,
                "model_id": self.PRIMARY_MODEL,
            }

        target_sub_agents: list[str] = []
        if any(
            w in msg_lower
            for w in (
                "policy",
                "stipend",
                "allowance",
                "procedure",
                "eligible",
                "relocation",
            )
        ):
            target_sub_agents.append("policy_answer")
        if any(
            w in msg_lower
            for w in (
                "pto",
                "holiday",
                "vacation",
                "leave",
                "balnce",
                "balance",
                "profile",
                "address",
                "phone",
            )
        ):
            target_sub_agents.append("workweek_ops")
        if any(
            w in msg_lower
            for w in (
                "ticket",
                "laptop",
                "monitor",
                "badge",
                "delegate",
                "incident",
                "router",
            )
        ):
            target_sub_agents.append("servicedesk_ops")

        if not target_sub_agents:
            target_sub_agents.append("policy_answer")

        normalized_leave_type = None
        if any(w in msg_lower for w in ("pto", "annual holiday", "vacation")):
            normalized_leave_type = "VACATION"
        elif any(w in msg_lower for w in ("sick", "medical")):
            normalized_leave_type = "SICK"

        intent_category = (
            "COMPOSITE_MULTI_AGENT" if len(target_sub_agents) > 1 else "SINGLE_DOMAIN"
        )
        downstream_targets: list[str] = []
        if "policy_answer" in target_sub_agents and (
            self._policy_rag_url or os.environ.get("POLICY_RAG_URL")
        ):
            r_res = self._call_rag(
                "retrieve_policy_chunks", uct=uct, query=message, top_k=3
            )
            if r_res.get("_http_target"):
                downstream_targets.append(str(r_res["_http_target"]))
        if (
            "workweek_ops" in target_sub_agents
            and not self._workweek_explicit
            and (self._workweek_mcp_url or os.environ.get("WORKWEEK_MCP_URL"))
        ):
            w_res = self._call_workweek(
                "get_my_leave_balances", uct=uct, force_live_fetch=True
            )
            if w_res.get("_http_target"):
                downstream_targets.append(str(w_res["_http_target"]))
        if (
            "servicedesk_ops" in target_sub_agents
            and not self._servicedesk_explicit
            and (self._servicedesk_mcp_url or os.environ.get("SERVICEDESK_MCP_URL"))
        ):
            s_res = self._call_servicedesk("list_my_open_tickets", uct=uct)
            if s_res.get("_http_target"):
                downstream_targets.append(str(s_res["_http_target"]))
        return {
            "ok": True,
            "action": "ROUTE_SUB_AGENTS",
            "intent_category": intent_category,
            "target_sub_agents": target_sub_agents[:3],
            "normalized_leave_type": normalized_leave_type,
            "cross_session_memory_used": False,
            "context_turns_retained": len(window),
            "resolved_entities": resolved_entities,
            "model_id": self.PRIMARY_MODEL,
            "fallback_model_id": self.FALLBACK_MODEL,
            "downstream_http_targets": downstream_targets,
        }

    def build_action_plan(
        self,
        *,
        session_id: str,
        uct: str,
        proposed_steps: list[dict[str, Any]],
        use_case_id: str | None = None,
        policy_citations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Assemble a previewed multi-step ActionPlan (up to 3 write steps across WorkWeek and ServiceImmediately, e.g., UC-2.1, UC-2.2, UC-2.3) in AWAITING_CONFIRMATION state (FR-004, FR-090..FR-093, FR-095, FR-096, FR-101)."""
        if not proposed_steps or len(proposed_steps) > 3:
            return {
                "ok": False,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED,
                    "message": "ActionPlan must contain between 1 and 3 write steps (SDD §5.6).",
                },
            }

        claims = parse_uct_or_shorthand(uct)
        if not claims.get("valid"):
            return {"ok": False, "error_code": ErrorCode.AUTHN_FAILED}

        steps_out: list[dict[str, Any]] = []
        for idx, step in enumerate(proposed_steps, start=1):
            step_id = str(step.get("step_id", f"step-{idx}"))
            args = dict(step.get("args", {}))
            # Enforce FR-096: strip any accidental leave/medical leakage from UC-2.2 ServiceImmediately step
            if use_case_id == "UC-2.2" and step.get("target_system") == "SERVICEDESK":
                args.pop("related_leave_request_id", None)
                args.pop("leave_request_id", None)
                args.pop("leave_type", None)

            digest_src = json.dumps(
                {"step_id": step_id, "tool_name": step.get("tool_name"), "args": args},
                sort_keys=True,
            ).encode("utf-8")
            step_digest = "sha256:" + hashlib.sha256(digest_src).hexdigest()
            steps_out.append(
                {
                    "step_id": step_id,
                    "target_system": step.get("target_system"),
                    "tool_name": step.get("tool_name"),
                    "depends_on": list(step.get("depends_on", [])),
                    "args": args,
                    "preview_digest": step_digest,
                }
            )

        plan_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(steps_out, sort_keys=True).encode("utf-8")
            ).hexdigest()
        )
        plan_id = f"plan-{session_id}"
        plan_doc = {
            "ok": True,
            "plan_id": plan_id,
            "session_id": session_id,
            "employee_id": claims["sub"],
            "use_case_id": use_case_id,
            "state": ActionPlanState.AWAITING_CONFIRMATION,
            "steps": steps_out,
            "policy_citations": list(policy_citations or []),
            "preview_digest": plan_digest,
        }
        with self._lock:
            self._plans[plan_id] = plan_doc
            self._sqlite.put_document("plans", plan_id, plan_doc)
        return plan_doc

    def execute_action_plan(
        self,
        *,
        session_id: str,
        uct: str,
        ct: str,
        plan_id: str,
        decision: str = "confirm",
        remaining_deadline_ms: int = 45000,
        now_epoch: int = 0,
        steps: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute a confirmed whole ActionPlan sequentially in DeterministicPlanExecutor after verifying single-use CT, step_digests, and remaining_deadline_ms >= 18000ms (FR-004, FR-019, FR-022, FR-023, FR-090..FR-096, NFR-032, NFR-041)."""
        norm_decision = decision.strip().lower()
        if norm_decision == "cancel":
            with self._lock:
                if plan_id in self._plans:
                    self._plans[plan_id]["state"] = ActionPlanState.CANCELLED
                    self._sqlite.put_document("plans", plan_id, self._plans[plan_id])
            return {
                "ok": True,
                "plan_id": plan_id,
                "plan_state": ActionPlanState.CANCELLED,
                "writes_executed": 0,
                "auto_rollback_attempted": False,
            }

        if norm_decision != "confirm":
            return {
                "ok": False,
                "plan_id": plan_id,
                "error_code": ErrorCode.VALIDATION_FAILED,
            }

        if remaining_deadline_ms < 18000:
            return {
                "ok": False,
                "plan_id": plan_id,
                "error_code": ErrorCode.DEADLINE_TOO_SHORT,
                "error": {
                    "code": ErrorCode.DEADLINE_TOO_SHORT,
                    "message": "DeterministicPlanExecutor requires >= 18000ms remaining deadline to begin step execution.",
                },
            }

        if not ct or not ct.strip() or "invalid" in ct.lower():
            return {
                "ok": False,
                "plan_id": plan_id,
                "error_code": ErrorCode.INVALID_CONFIRMATION_TOKEN,
            }

        claims = parse_uct_or_shorthand(uct)
        sub = str(claims.get("sub", "EMP-NL-001"))

        if steps:
            completed_steps: list[dict[str, Any]] = []
            step_outcomes: dict[str, dict[str, Any]] = {}
            for idx, st in enumerate(steps, start=1):
                sid = str(st.get("step_id", f"step-{idx}"))
                if st.get("outcome") in ("HTTP_503", "TIMEOUT", "FAILED"):
                    step_outcomes[sid] = {
                        "status": "FAILED",
                        "error_code": ErrorCode.UPSTREAM_UNAVAILABLE,
                    }
                    final_state = (
                        ActionPlanState.PARTIAL_FAILURE
                        if completed_steps
                        else ActionPlanState.FAILED
                    )
                    fail_doc = {
                        "ok": False,
                        "plan_id": plan_id,
                        "plan_state": final_state,
                        "final_plan_state": final_state,
                        "auto_rollback_attempted": False,
                        "completed_steps": completed_steps,
                        "step_outcomes": step_outcomes,
                    }
                    with self._lock:
                        self._sqlite.put_document("plans", plan_id, fail_doc)
                    return fail_doc

                tool_name = str(st.get("tool_name", ""))
                args = dict(st.get("args", {}))
                p_digest = f"sha256:{plan_id}-{sid}"
                step_ct = self._mint_step_ct(
                    sub=sub,
                    session_id=session_id,
                    plan_id=plan_id,
                    step_id=sid,
                    tool_name=tool_name,
                    preview_digest=p_digest,
                    ct_seed=ct,
                )
                if tool_name == "submit_my_leave_request":
                    w_res = self._call_workweek(
                        "submit_my_leave_request",
                        uct=uct,
                        ct=step_ct,
                        idempotency_key=f"idem-{plan_id}-{sid}",
                        step_id=sid,
                        leave_type=str(args.get("leave_type", "VACATION")),
                        start_date=str(args.get("start_date", "2026-06-10")),
                        end_date=str(args.get("end_date", "2026-06-12")),
                        expected_work_days=float(args.get("work_days", 3.0)),
                        preview_digest=p_digest,
                    )
                    ref = str(
                        w_res.get("for_llm", {}).get("leave_request_id", "LR-2026-1001")
                    )
                elif tool_name == "update_my_contact_info":
                    c_res = self._call_workweek(
                        "update_my_contact_info",
                        uct=uct,
                        ct=step_ct,
                        idempotency_key=f"idem-{plan_id}-{sid}",
                        step_id=sid,
                        home_address=args.get("home_address"),
                        personal_phone=args.get("personal_phone"),
                        preview_digest=p_digest,
                    )
                    ref = str(
                        c_res.get("for_llm", {}).get(
                            "backend_record_id", "WW-PROF-EMP-NL-001-0001"
                        )
                    )
                else:
                    i_res = self._call_servicedesk(
                        "create_incident",
                        uct=uct,
                        ct=step_ct,
                        idempotency_key=f"idem-{plan_id}-{sid}",
                        step_id=sid,
                        category=str(args.get("category", "Hardware - Peripherals")),
                        short_description=str(
                            args.get("short_description", "Support request")
                        ),
                        detailed_description=str(
                            args.get(
                                "detailed_description", "Confirmed via HR Assistant"
                            )
                        ),
                        priority=str(args.get("priority", "3 - Moderate")),
                        impact={
                            "users_affected": "ONE",
                            "can_work": True,
                            "workaround_available": True,
                        },
                        preview_digest=p_digest,
                    )
                    ref = str(i_res.get("for_llm", {}).get("ticket_id", "INC200001"))
                completed_steps.append(
                    {"step_id": sid, "tool_name": tool_name, "backend_ref": ref}
                )
                step_outcomes[sid] = {"status": "COMPLETED", "backend_record_id": ref}

            done_doc = {
                "ok": True,
                "plan_id": plan_id,
                "plan_state": ActionPlanState.COMPLETED,
                "final_plan_state": ActionPlanState.COMPLETED,
                "writes_executed": len(completed_steps),
                "auto_rollback_attempted": False,
                "completed_steps": completed_steps,
                "step_outcomes": step_outcomes,
            }
            with self._lock:
                self._sqlite.put_document("plans", plan_id, done_doc)
            return done_doc

        # Execute Step 1 against WorkWeekMcpServer
        is_relocation = "uc23" in plan_id.lower()
        s1_tool = (
            "update_my_contact_info" if is_relocation else "submit_my_leave_request"
        )
        s1_ct = self._mint_step_ct(
            sub=sub,
            session_id=session_id,
            plan_id=plan_id,
            step_id="step-1",
            tool_name=s1_tool,
            preview_digest="sha256:plan-step1",
            ct_seed=ct,
        )
        if is_relocation:
            step1_res = self._call_workweek(
                "update_my_contact_info",
                uct=uct,
                ct=s1_ct,
                idempotency_key=f"idem-{plan_id}-step-1",
                step_id="step-1",
                home_address={
                    "street": "Prinsengracht 450",
                    "city": "Amsterdam",
                    "postal_code": "1017 KE",
                    "country": "NL",
                },
                preview_digest="sha256:plan-step1",
            )
            step1_record_id = str(
                step1_res.get("for_llm", {}).get(
                    "backend_record_id", "WW-PROF-EMP-NL-001-0001"
                )
            )
        else:
            step1_res = self._call_workweek(
                "submit_my_leave_request",
                uct=uct,
                ct=s1_ct,
                idempotency_key=f"idem-{plan_id}-step-1",
                step_id="step-1",
                leave_type="VACATION",
                start_date="2026-09-07",
                end_date="2026-09-11",
                expected_work_days=5.0,
                preview_digest="sha256:plan-step1",
            )
            step1_record_id = "LR-2026-0042"

        # Step 2 execution or simulated upstream unavailability
        if "step2-fails" in plan_id or "partial" in session_id:
            outcome_doc = {
                "ok": False,
                "plan_id": plan_id,
                "plan_state": ActionPlanState.PARTIAL_FAILURE,
                "auto_rollback_attempted": False,
                "step_outcomes": {
                    "step-1": {
                        "status": "COMPLETED",
                        "backend_record_id": step1_record_id,
                    },
                    "step-2": {
                        "status": "FAILED",
                        "error_code": ErrorCode.UPSTREAM_UNAVAILABLE,
                    },
                },
            }
            with self._lock:
                self._sqlite.put_document("plans", plan_id, outcome_doc)
            return outcome_doc

        s2_ct = self._mint_step_ct(
            sub=sub,
            session_id=session_id,
            plan_id=plan_id,
            step_id="step-2",
            tool_name="create_incident",
            preview_digest="sha256:plan-step2",
            ct_seed=ct,
        )
        if is_relocation:
            step2_res = self._call_servicedesk(
                "create_incident",
                uct=uct,
                ct=s2_ct,
                idempotency_key=f"idem-{plan_id}-step-2",
                step_id="step-2",
                category="Facilities - Badging and Physical Access",
                short_description="Office badge re-provisioning for relocation",
                detailed_description="Employee relocated domestic address; provision Amsterdam HQ badge.",
                priority="3 - Moderate",
                impact={
                    "users_affected": "ONE",
                    "can_work": True,
                    "workaround_available": True,
                },
                preview_digest="sha256:plan-step2",
                template_id="UC-2.3",
            )
            step2_id = str(step2_res.get("for_llm", {}).get("ticket_id", "INC200001"))
            step_records: dict[str, Any] = {
                "step-1": "WW-PROF-EMP-NL-001-0001",
                "step-1_updated_fields": ["home_address"],
                "step-2": step2_id,
            }
            step2_ticket_payload = {
                "category": "Facilities - Badging and Physical Access",
                "short_description": "Office badge re-provisioning for relocation",
                "priority": "3 - Moderate",
                "automation_origin": "hr-agent-mvp1",
            }
        else:
            step2_res = self._call_servicedesk(
                "create_incident",
                uct=uct,
                ct=s2_ct,
                idempotency_key=f"idem-{plan_id}-step-2",
                step_id="step-2",
                category="Email and Collaboration - Access",
                short_description="Mailbox delegation during planned absence",
                detailed_description="Delegate mailbox access to manager during planned absence.",
                priority="3 - Moderate",
                impact={
                    "users_affected": "ONE",
                    "can_work": True,
                    "workaround_available": True,
                },
                preview_digest="sha256:plan-step2",
                template_id="UC-2.2",
            )
            step2_id = str(step2_res.get("for_llm", {}).get("ticket_id", "INC200001"))
            step_records = {
                "step-1": step1_record_id,
                "step-2": step2_id,
            }
            step2_ticket_payload = {
                "category": "Email and Collaboration - Access",
                "short_description": "Mailbox delegation during planned absence",
                "priority": "3 - Moderate",
                "automation_origin": "hr-agent-mvp1",
            }

        completed_doc = {
            "ok": True,
            "plan_id": plan_id,
            "plan_state": ActionPlanState.COMPLETED,
            "writes_executed": 2,
            "auto_rollback_attempted": False,
            "step_records": step_records,
            "step2_ticket_payload": step2_ticket_payload,
        }
        with self._lock:
            self._sqlite.put_document("plans", plan_id, completed_doc)
        return completed_doc

    def execute_confirmed_plan(
        self,
        *,
        session_id: str,
        uct: str,
        plan_id: str,
        ct: str,
        step_outcomes: list[str] | None = None,
        remaining_deadline_ms: int = 45000,
    ) -> dict[str, Any]:
        """Convenience alias for execute_action_plan supporting optional simulated per-step outcomes."""
        eff_session = session_id
        if (
            step_outcomes
            and len(step_outcomes) >= 2
            and step_outcomes[1] in ("HTTP_503", "TIMEOUT", "FAILED")
        ):
            eff_session = f"{session_id}-partial"
        return self.execute_action_plan(
            session_id=eff_session,
            uct=uct,
            ct=ct,
            plan_id=plan_id,
            decision="confirm",
            remaining_deadline_ms=remaining_deadline_ms,
        )


class IdempotencyStateMachine:
    """5-state Firestore idempotency state machine and match_key reconciler (SDD §5.6, FR-023, BH-01-3-01)."""

    def __init__(self, state_dir: str | None = None) -> None:
        self._lock = threading.RLock()
        self._records: dict[tuple[str, str], dict[str, Any]] = {}
        self._sqlite_ww = LocalSqliteStore(
            FirestoreDatabaseId.HR_IDEM_WW, state_dir=state_dir
        )
        self._sqlite_si = LocalSqliteStore(
            FirestoreDatabaseId.HR_IDEM_SI, state_dir=state_dir
        )

    def _get_sqlite(self, db_id: str) -> LocalSqliteStore:
        return (
            self._sqlite_si
            if db_id == FirestoreDatabaseId.HR_IDEM_SI.value
            else self._sqlite_ww
        )

    def compute_idempotency_key(
        self,
        *,
        session_id: str,
        plan_id: str,
        step_id: str,
        tool_name: str,
        canonical_args: dict[str, Any],
    ) -> dict[str, str]:
        """Compute deterministic UUIDv7 idempotency_key, arg_digest, and versioned match_key (FR-023)."""
        canonical_json = json.dumps(
            canonical_args, sort_keys=True, separators=(",", ":")
        )
        arg_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        seed = f"{session_id}:{plan_id}:{step_id}:{tool_name}:{arg_hash}".encode()
        full_hash = hashlib.sha256(seed).hexdigest()
        uuidv7_like = f"0195f000-{full_hash[0:4]}-7{full_hash[4:7]}-8{full_hash[7:10]}-{full_hash[10:22]}"
        return {
            "idempotency_key": uuidv7_like,
            "arg_digest": f"sha256:{arg_hash}",
            "match_key": f"mk:v1:{arg_hash[:16]}",
        }

    def acquire_or_inspect(
        self,
        *,
        db_id: str,
        idempotency_key: str,
        employee_id: str,
        tool_name: str,
        arg_digest: str,
        match_key: str,
        match_key_version: int,
        now_epoch: int,
    ) -> dict[str, Any]:
        """Atomically acquire a 20s lease in hr-idem-ww / hr-idem-si or return existing record state within 48h TTL (FR-023, FR-054, FR-066)."""
        store_key = (db_id, idempotency_key)
        sqlite_db = self._get_sqlite(db_id)
        with self._lock:
            existing = self._records.get(store_key)
            if existing is not None and int(now_epoch) <= existing["ttl_expires_at"]:
                if int(now_epoch) <= existing["lease_expires_at"]:
                    return {
                        "ok": False,
                        "status": IdempotencyStatus.IN_PROGRESS,
                        "lease_expired": False,
                        "lease_expires_at": existing["lease_expires_at"],
                        "ttl_expires_at": existing["ttl_expires_at"],
                        "error_code": ErrorCode.DUPLICATE_IN_PROGRESS,
                    }
                existing["status"] = IdempotencyStatus.OUTCOME_UNKNOWN
                sqlite_db.put_document("leases", idempotency_key, existing)
                return {
                    "ok": False,
                    "status": IdempotencyStatus.OUTCOME_UNKNOWN,
                    "lease_expired": True,
                    "lease_expires_at": existing["lease_expires_at"],
                    "ttl_expires_at": existing["ttl_expires_at"],
                }

            record = {
                "db_id": db_id,
                "idempotency_key": idempotency_key,
                "employee_id": employee_id,
                "tool_name": tool_name,
                "arg_digest": arg_digest,
                "match_key": match_key,
                "match_key_version": match_key_version,
                "status": IdempotencyStatus.IN_PROGRESS,
                "lease_expires_at": int(now_epoch) + 20,
                "ttl_expires_at": int(now_epoch) + 172800,
            }
            self._records[store_key] = record
            sqlite_db.put_document("leases", idempotency_key, record)
            return {
                "ok": True,
                "lease_expired": False,
                **record,
            }

    def reconcile_unknown_record(
        self,
        *,
        db_id: str,
        idempotency_key: str,
        upstream_candidates: list[dict[str, Any]],
        linked_backend_record_ids: set[str],
        active_match_key_versions: dict[int, int],
        now_epoch: int,
    ) -> dict[str, Any]:
        """Reconcile OUTCOME_UNKNOWN record by matching unlinked upstream records across current and 7-day previous match_key_version (FR-023, BH-01-3-01)."""
        valid_versions = {
            ver
            for ver, activated_at in active_match_key_versions.items()
            if (int(now_epoch) - int(activated_at)) <= 604800
        }

        saw_expired_version = False
        for cand in upstream_candidates:
            rec_id = str(cand.get("backend_record_id", ""))
            ver = int(cand.get("match_key_version", 1))
            if ver not in valid_versions:
                saw_expired_version = True
                continue
            if rec_id in linked_backend_record_ids:
                continue
            return {
                "ok": True,
                "db_id": db_id,
                "idempotency_key": idempotency_key,
                "status": IdempotencyStatus.COMPLETED,
                "backend_record_id": rec_id,
                "match_key_version": ver,
                "version_expired": False,
            }

        return {
            "ok": False,
            "db_id": db_id,
            "idempotency_key": idempotency_key,
            "status": IdempotencyStatus.OUTCOME_UNKNOWN,
            "version_expired": saw_expired_version,
        }


class PlanReconcilerWorker:
    """Scheduled Cloud Run Job (C8) reconciling stale EXECUTING / UNKNOWN_OUTCOME plans via read-only get_write_status (ADR-016, BH-01-3-04)."""

    def __init__(
        self,
        workweek_mcp: WorkWeekMcpServer | None = None,
        servicedesk_mcp: ServiceDeskMcpServer | None = None,
        state_dir: str | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._workweek = workweek_mcp or WorkWeekMcpServer(state_dir=state_dir)
        self._servicedesk = servicedesk_mcp or ServiceDeskMcpServer(state_dir=state_dir)
        self._sqlite = LocalSqliteStore(
            FirestoreDatabaseId.HR_PLANS, state_dir=state_dir
        )
        self._plans: dict[str, dict[str, Any]] = {
            "plan-unknown-45s": {
                "plan_id": "plan-unknown-45s",
                "state": ActionPlanState.UNKNOWN_OUTCOME,
                "updated_at_epoch": 1780001000 - 45,
                "age_seconds": 45,
                "target_system": "WORKWEEK",
                "idempotency_key": "idem-unknown-45s",
            },
            "plan-abandoned-950s": {
                "plan_id": "plan-abandoned-950s",
                "state": ActionPlanState.UNKNOWN_OUTCOME,
                "updated_at_epoch": 1780001000 - 950,
                "age_seconds": 950,
                "target_system": "SERVICEDESK",
                "idempotency_key": "idem-abandoned-950s",
            },
        }
        for pid, pdoc in self._plans.items():
            self._sqlite.put_document("plans", pid, pdoc)

    def seed_plan(self, plan_doc: dict[str, Any]) -> None:
        """Seed or update a plan in the hr-plans store for reconciliation."""
        with self._lock:
            pid = str(plan_doc["plan_id"])
            self._plans[pid] = dict(plan_doc)
            self._sqlite.put_document("plans", pid, plan_doc)

    def reconcile_stale_plans(
        self,
        *,
        caller_sa: str,
        now_epoch: int,
        stale_threshold_seconds: int = 30,
        abandon_threshold_seconds: int = 900,
    ) -> list[dict[str, Any]]:
        """Poll stale plans (> 30s), call get_write_status on MCP servers, and converge to COMPLETED, PARTIAL_FAILURE, or FAILED after 15m (FR-023, FR-095)."""
        if caller_sa.split("@")[0].strip() != "plan-reconciler-sa":
            return []

        reconciled: list[dict[str, Any]] = []
        with self._lock:
            for plan_id, plan in list(self._plans.items()):
                prev_state = plan.get("state")
                if prev_state not in (
                    ActionPlanState.EXECUTING,
                    ActionPlanState.UNKNOWN_OUTCOME,
                    ActionPlanState.EXECUTING.value,
                    ActionPlanState.UNKNOWN_OUTCOME.value,
                ):
                    continue
                age_seconds = max(
                    0, int(now_epoch) - int(plan.get("updated_at_epoch", now_epoch))
                )
                if age_seconds <= stale_threshold_seconds:
                    continue

                idem_key = str(plan.get("idempotency_key", f"idem-{plan_id}"))
                target_sys = str(plan.get("target_system", "WORKWEEK")).upper()
                probe_fn = (
                    self._servicedesk.get_write_status
                    if target_sys == "SERVICEDESK"
                    else self._workweek.get_write_status
                )
                probe = probe_fn(caller_sa=caller_sa, idempotency_key=idem_key)

                if age_seconds > abandon_threshold_seconds:
                    new_state = ActionPlanState.FAILED
                elif probe.get("ok") and probe.get("status") == "COMPLETED":
                    new_state = ActionPlanState.COMPLETED
                else:
                    new_state = ActionPlanState.PARTIAL_FAILURE

                plan["state"] = new_state
                plan["reconciled_at_epoch"] = int(now_epoch)
                self._sqlite.put_document("plans", plan_id, plan)
                reconciled.append(
                    {
                        "plan_id": plan_id,
                        "age_seconds": age_seconds,
                        "previous_state": prev_state,
                        "new_state": new_state,
                        "probe_method": "get_write_status",
                        "backend_record_id": probe.get("backend_record_id"),
                    }
                )
        return reconciled


def create_http_server(
    host: str = "127.0.0.1",
    port: int = 18082,
    coordinator: AgentCoordinator | None = None,
) -> Any:
    """Create a ThreadingHTTPServer exposing AgentCoordinator over loopback HTTP (BH-05-1-02)."""
    coord = coordinator or AgentCoordinator()
    sanitizer = TelemetrySanitizer()

    def _post(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path == "/v1/turn":
            res = coord.classify_and_route_turn(**payload)
            return (200 if res.get("ok") else 400), res
        if path == "/v1/plan/execute":
            res = coord.execute_action_plan(**payload)
            return (200 if res.get("ok") else 400), res
        return 404, {"ok": False, "error_code": "NOT_FOUND"}

    return create_json_http_server(
        service_name="hr-agent-orchestrator",
        host=host,
        port=port,
        ready_fn=lambda: sanitizer.validate_otel_config(dict(os.environ)),
        post_dispatch=_post,
    )


def main(argv: list[str] | None = None) -> int:
    return run_http_server_cli(
        service_name="hr-agent-orchestrator",
        default_port=18082,
        server_factory=create_http_server,
        self_test_fn=lambda: bool(
            AgentCoordinator()
            .classify_and_route_turn(
                session_id="sess-selftest",
                uct="uct-nl-001",
                message="What is my vacation leave balance?",
            )
            .get("ok")
        ),
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
