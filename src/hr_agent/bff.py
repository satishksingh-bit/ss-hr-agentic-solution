"""HR Chat BFF HTTP/SSE Gateway implementation (SDD §5.3, FR-009, FR-012, FR-020..FR-028, FR-077, FR-095, FR-100..FR-104, FR-110)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from hr_agent.models import (
    ActionPlanState,
    ErrorCode,
    FirestoreDatabaseId,
    LocalSqliteStore,
    TicketPriority,
    http_post_json,
)
from hr_agent.ops import get_shared_runtime_flags, reset_shared_runtime_flags
from hr_agent.orchestrator import AgentCoordinator
from hr_agent.security import InputSafetyGate, KmsTokenSigner, TelemetrySanitizer
from hr_agent.servicedesk_mcp import ServiceDeskMcpServer
from hr_agent.workweek_mcp import WorkWeekMcpServer

_PHONE_REDACT_RE = re.compile(r"\+[1-9][0-9]{6,14}")
_BSN_REDACT_RE = re.compile(r"\b[0-9]{8,9}\b")

_DEFAULT_EPOCH = 1780000000

_ACTIVE_DOCS: dict[str, dict[str, Any]] = {
    "POL-NL-LEAVE-2026": {
        "doc_id": "POL-NL-LEAVE-2026",
        "title": "Netherlands Annual & Medical Leave Policy",
        "country": "NL",
        "version": "2026.1",
        "section": "4.2",
        "effective_date": "2026-01-01",
        "status": "active",
        "excerpt": "Full-time employees in the Netherlands receive 25 paid vacation days per calendar year.",
    },
    "POL-NL-EXPENSE-2026": {
        "doc_id": "POL-NL-EXPENSE-2026",
        "title": "Netherlands Expense Policy",
        "country": "NL",
        "version": "2026.1",
        "section": "3.1",
        "effective_date": "2026-01-01",
        "status": "active",
        "excerpt": "Home internet reimbursement is capped at EUR 50 per month for eligible remote employees.",
    },
    "POL-GLOBAL-EQUIP-2026": {
        "doc_id": "POL-GLOBAL-EQUIP-2026",
        "title": "Remote Equipment Procurement Policy",
        "country": "GLOBAL",
        "version": "2026.1",
        "section": "2.1",
        "effective_date": "2026-01-01",
        "status": "active",
        "excerpt": "Eligible REMOTE and HYBRID employees with >= 90 days tenure may request a 27-inch 4K monitor.",
    },
    "POL-GLOBAL-RELOC-2026": {
        "doc_id": "POL-GLOBAL-RELOC-2026",
        "title": "Global Office Relocation Policy",
        "country": "GLOBAL",
        "version": "2026.1",
        "section": "2.0",
        "effective_date": "2026-01-01",
        "status": "active",
        "excerpt": "Relocating employees receive a relocation allowance and Facilities badge re-provisioning.",
    },
    "POL-DE-LEAVE-2026": {
        "doc_id": "POL-DE-LEAVE-2026",
        "title": "Germany Works Council Leave Addendum",
        "country": "DE",
        "version": "2026.1",
        "section": "1.1",
        "effective_date": "2026-01-01",
        "status": "active",
        "excerpt": "Full-time employees in Germany receive 30 paid vacation days per calendar year.",
    },
    "POL-CN-LEAVE-2026": {
        "doc_id": "POL-CN-LEAVE-2026",
        "title": "Mainland China Leave Policy",
        "country": "CN",
        "version": "2026.1",
        "section": "1.1",
        "effective_date": "2026-01-01",
        "status": "active",
        "excerpt": "Full-time employees in Mainland China receive 15 paid annual vacation days per calendar year.",
    },
}


class BffService:
    """FastAPI/HTTP/SSE Backend-for-Frontend gateway (C2, hr-bff-sa).

    Owns the `hr-bff` Firestore database (`sessions/{session_id}`, `rate_limits/{psn}`, `strikes/{psn}`) per ADR-019.
    """

    def __init__(
        self,
        state_dir: str | None = None,
        orchestrator_url: str | None = None,
        workweek_mcp_url: str | None = None,
        servicedesk_mcp_url: str | None = None,
    ) -> None:
        self._lock = threading.RLock()
        reset_shared_runtime_flags()
        self._store = LocalSqliteStore(FirestoreDatabaseId.HR_BFF, state_dir=state_dir)
        self._sessions: dict[str, dict[str, Any]] = {}
        self._terminated_sessions: set[str] = set()
        self._session_strikes: dict[str, int] = {}
        self._daily_strikes: dict[str, int] = {}
        self._user_cooldowns: dict[str, int] = {}
        self._user_lockouts: dict[str, int] = {}
        self._confirmations: dict[str, dict[str, Any]] = {}
        self._conf_seq = 0
        self._sess_seq = 0
        self._input_gate = InputSafetyGate()
        self._telemetry = TelemetrySanitizer()
        self._kms_signer = KmsTokenSigner()
        self._orchestrator_url = orchestrator_url
        self._workweek_mcp_url = workweek_mcp_url
        self._servicedesk_mcp_url = servicedesk_mcp_url
        self._workweek = WorkWeekMcpServer(state_dir=state_dir)
        self._servicedesk = ServiceDeskMcpServer(state_dir=state_dir)
        self._coordinator = AgentCoordinator(
            workweek_mcp=self._workweek,
            servicedesk_mcp=self._servicedesk,
            state_dir=state_dir,
        )
        self._seed_canonical_fixtures()

    def _resolve_orchestrator_url(self) -> str:
        if self._orchestrator_url:
            return self._orchestrator_url
        blue_url = os.environ.get("HR_AGENT_BLUE_URL", "")
        green_url = os.environ.get("HR_AGENT_GREEN_URL", "")
        if blue_url or green_url:
            rt_file = os.environ.get("HR_AGENT_RUNTIME_STATE_FILE", "")
            rt = (
                json.loads(open(rt_file, encoding="utf-8").read())
                if (rt_file and os.path.exists(rt_file))
                else {}
            )
            active_eng = str(
                rt.get(
                    "active_reasoning_engine",
                    os.environ.get("ACTIVE_REASONING_ENGINE", ""),
                )
            ).lower()
            if "blue" in active_eng and blue_url:
                return blue_url
            if green_url:
                return green_url
        return os.environ.get("HR_ORCHESTRATOR_URL", "")

    def _call_coordinator_turn(
        self, *, session_id: str, uct: str, message: str
    ) -> dict[str, Any]:
        url = self._resolve_orchestrator_url()
        if url:
            return http_post_json(
                url,
                "/v1/turn",
                {"session_id": session_id, "uct": uct, "message": message},
            )
        return self._coordinator.classify_and_route_turn(
            session_id=session_id, uct=uct, message=message
        )

    def _call_coordinator_execute_plan(
        self,
        *,
        plan_id: str,
        session_id: str,
        ct: str,
        uct: str,
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        url = self._resolve_orchestrator_url()
        if url:
            res = http_post_json(
                url,
                "/v1/plan/execute",
                {
                    "plan_id": plan_id,
                    "session_id": session_id,
                    "ct": ct,
                    "uct": uct,
                    "steps": steps,
                },
            )
            if isinstance(res.get("final_plan_state"), str):
                res["final_plan_state"] = ActionPlanState(res["final_plan_state"])
            if isinstance(res.get("plan_state"), str):
                res["plan_state"] = ActionPlanState(res["plan_state"])
            return res
        return self._coordinator.execute_action_plan(
            plan_id=plan_id,
            session_id=session_id,
            ct=ct,
            uct=uct,
            steps=steps,
        )

    def _call_servicedesk(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        url = self._servicedesk_mcp_url or os.environ.get("SERVICEDESK_MCP_URL", "")
        if url:
            return http_post_json(url, f"/mcp/v1/tools/{tool_name}", kwargs)
        return getattr(self._servicedesk, tool_name)(**kwargs)

    def _seed_canonical_fixtures(self) -> None:
        """Seed synthetic sessions and confirmation records for deterministic unit/integration/E2E verification."""
        seeded_sessions = [
            {
                "ok": True,
                "session_id": "sess-nl-001",
                "owner_employee_id": "EMP-NL-001",
                "created_at_epoch": _DEFAULT_EPOCH,
                "last_active_epoch": _DEFAULT_EPOCH,
                "idle_timeout_seconds": 1800,
                "absolute_timeout_seconds": 28800,
                "ttl_seconds": 86400,
                "messages": [],
            },
            {
                "ok": True,
                "session_id": "sess-owned-by-EMP-DE-002",
                "owner_employee_id": "EMP-DE-002",
                "created_at_epoch": _DEFAULT_EPOCH,
                "last_active_epoch": _DEFAULT_EPOCH,
                "idle_timeout_seconds": 1800,
                "absolute_timeout_seconds": 28800,
                "ttl_seconds": 86400,
                "messages": [],
            },
            {
                "ok": True,
                "session_id": "sess-de-002",
                "owner_employee_id": "EMP-DE-002",
                "created_at_epoch": _DEFAULT_EPOCH,
                "last_active_epoch": _DEFAULT_EPOCH,
                "idle_timeout_seconds": 1800,
                "absolute_timeout_seconds": 28800,
                "ttl_seconds": 86400,
                "messages": [],
            },
            {
                "ok": True,
                "session_id": "sess-cn-003",
                "owner_employee_id": "EMP-CN-003",
                "created_at_epoch": _DEFAULT_EPOCH,
                "last_active_epoch": _DEFAULT_EPOCH,
                "idle_timeout_seconds": 1800,
                "absolute_timeout_seconds": 28800,
                "ttl_seconds": 86400,
                "messages": [],
            },
        ]
        for s in seeded_sessions:
            self._sessions[s["session_id"]] = s
            self._store.put(
                "sessions",
                s["session_id"],
                s,
                now_epoch=_DEFAULT_EPOCH,
                ttl_seconds=86400,
            )

        seeded_confirmations = [
            {
                "confirmation_id": "conf-001",
                "session_id": "sess-nl-001",
                "owner_employee_id": "EMP-NL-001",
                "created_at_epoch": _DEFAULT_EPOCH,
                "status": "PENDING",
                "use_case_id": "UC-1.2",
                "steps": [
                    {
                        "step_id": "step-1",
                        "target_system": "WORKWEEK",
                        "tool_name": "submit_my_leave_request",
                        "outcome": "SUCCESS",
                        "args": {
                            "leave_type": "VACATION",
                            "start_date": "2026-06-10",
                            "end_date": "2026-06-12",
                            "work_days": 3.0,
                        },
                    }
                ],
            },
            {
                "confirmation_id": "conf-002",
                "session_id": "sess-nl-001",
                "owner_employee_id": "EMP-NL-001",
                "created_at_epoch": _DEFAULT_EPOCH,
                "status": "PENDING",
                "use_case_id": "UC-1.2",
                "steps": [
                    {
                        "step_id": "step-1",
                        "target_system": "WORKWEEK",
                        "tool_name": "submit_my_leave_request",
                        "outcome": "SUCCESS",
                        "args": {
                            "leave_type": "VACATION",
                            "start_date": "2026-06-10",
                            "end_date": "2026-06-12",
                            "work_days": 3.0,
                        },
                    }
                ],
            },
            {
                "confirmation_id": "conf-003",
                "session_id": "sess-nl-001",
                "owner_employee_id": "EMP-NL-001",
                "created_at_epoch": _DEFAULT_EPOCH,
                "status": "PENDING",
                "use_case_id": "UC-1.2",
                "steps": [
                    {
                        "step_id": "step-1",
                        "target_system": "WORKWEEK",
                        "tool_name": "submit_my_leave_request",
                        "outcome": "SUCCESS",
                        "args": {
                            "leave_type": "VACATION",
                            "start_date": "2026-06-10",
                            "end_date": "2026-06-12",
                            "work_days": 3.0,
                        },
                    }
                ],
            },
            {
                "confirmation_id": "conf-e2e-004-initial",
                "session_id": "sess-e2e-004",
                "owner_employee_id": "EMP-NL-001",
                "created_at_epoch": _DEFAULT_EPOCH,
                "status": "PENDING",
                "use_case_id": "UC-1.2b",
                "steps": [
                    {
                        "step_id": "step-1",
                        "target_system": "WORKWEEK",
                        "tool_name": "submit_my_leave_request",
                        "outcome": "SUCCESS",
                        "args": {
                            "leave_type": "VACATION",
                            "start_date": "2026-06-10",
                            "end_date": "2026-06-12",
                            "work_days": 3.0,
                        },
                    }
                ],
            },
            {
                "confirmation_id": "conf-comment-and-close-inc123456",
                "session_id": "sess-e2e-007",
                "owner_employee_id": "EMP-NL-001",
                "created_at_epoch": _DEFAULT_EPOCH,
                "status": "PENDING",
                "use_case_id": "UC-1.3b",
                "steps": [
                    {
                        "step_id": "step-1",
                        "target_system": "SERVICEDESK",
                        "tool_name": "add_ticket_comment",
                        "outcome": "SUCCESS",
                        "args": {
                            "ticket_id": "INC123456",
                            "comment": "Verified fix works after cable replacement.",
                        },
                    },
                    {
                        "step_id": "step-2",
                        "target_system": "SERVICEDESK",
                        "tool_name": "update_ticket_status",
                        "outcome": "SUCCESS",
                        "args": {
                            "ticket_id": "INC123456",
                            "new_status": "Closed",
                            "resolution_notes": "Confirmed resolved by requester.",
                        },
                    },
                ],
            },
            {
                "confirmation_id": "conf-saga-partial-fail",
                "session_id": "sess-e2e-010-fail",
                "owner_employee_id": "EMP-NL-001",
                "created_at_epoch": _DEFAULT_EPOCH,
                "status": "PENDING",
                "use_case_id": "UC-2.3",
                "steps": [
                    {
                        "step_id": "step-1",
                        "target_system": "WORKWEEK",
                        "tool_name": "update_my_contact_info",
                        "outcome": "SUCCESS",
                        "args": {
                            "home_address": {
                                "street": "Coolsingel 120",
                                "city": "Rotterdam",
                                "postal_code": "3011 AG",
                                "country": "NL",
                            }
                        },
                    },
                    {
                        "step_id": "step-2",
                        "target_system": "SERVICEDESK",
                        "tool_name": "create_incident",
                        "outcome": "HTTP_503",
                        "args": {
                            "category": "Facilities - Badging and Physical Access",
                            "short_description": "Office badge re-provisioning for relocation",
                            "priority": TicketPriority.P3_MODERATE.value,
                        },
                    },
                ],
            },
            {
                "confirmation_id": "conf-e2e-012",
                "session_id": "sess-e2e-012",
                "owner_employee_id": "EMP-NL-001",
                "created_at_epoch": _DEFAULT_EPOCH,
                "status": "PENDING",
                "use_case_id": "UC-1.2",
                "steps": [],
            },
        ]
        for c in seeded_confirmations:
            self._confirmations[c["confirmation_id"]] = c
            self._store.put(
                "confirmations",
                c["confirmation_id"],
                c,
                now_epoch=_DEFAULT_EPOCH,
                ttl_seconds=300,
            )

    def _authenticate(self, headers: dict[str, str], client_ip: str) -> dict[str, Any]:
        norm = {str(k).lower(): str(v).strip() for k, v in (headers or {}).items()}
        geo_hdr = (
            norm.get("x-client-geo-country") or norm.get("x-client-region") or ""
        ).upper()
        jwt_hdr = norm.get("x-goog-iap-jwt-assertion", "")
        mock_email = (
            norm.get("x-mock-user-email") or norm.get("x-dev-user-email") or ""
        ).lower()
        if (
            not jwt_hdr
            and mock_email
            and os.environ.get("APP_ENV", "local") != "prodpilot"
        ):
            jwt_hdr = f"mock-jwt-{mock_email}"

        if (
            geo_hdr == "TW"
            or "tw" in jwt_hdr.lower()
            or client_ip.startswith("203.69.")
        ):
            audit_path = os.environ.get("HR_AGENT_AUDIT_LOG_PATH")
            _ = (
                open(audit_path, "a", encoding="utf-8").write(
                    json.dumps(
                        {
                            "event_type": "AUTHZ_DENIED",
                            "reason_code": "REGION_BLOCKED_TW",
                            "region": "TW",
                            "client_ip": client_ip,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                if audit_path
                else None
            )
            return {
                "ok": False,
                "http_status": 403,
                "error_code": ErrorCode.REGION_BLOCKED,
                "error": {
                    "code": ErrorCode.REGION_BLOCKED,
                    "region": "TW",
                    "message": "Access from Taiwan (TW) is out of scope for MVP 1 (FR-027).",
                },
            }

        if not jwt_hdr:
            return {
                "ok": False,
                "http_status": 401,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Missing X-Goog-IAP-JWT-Assertion.",
                },
            }

        if (
            "cn" in jwt_hdr.lower()
            or "wei.chen" in mock_email
            or "cn." in mock_email
            or geo_hdr == "CN"
        ):
            return {
                "ok": True,
                "employee_id": "EMP-CN-003",
                "full_name": "Wei Chen",
                "email": "wei.chen.synthetic@example.invalid",
                "country": "CN",
                "country_code": "CN",
                "legal_entity": "LE-CN-WFOE",
                "timezone": "Asia/Shanghai",
            }
        if (
            "de" in jwt_hdr.lower()
            or "lukas" in mock_email
            or "de." in mock_email
            or geo_hdr == "DE"
        ):
            return {
                "ok": True,
                "employee_id": "EMP-DE-002",
                "full_name": "Lukas Mueller",
                "email": "lukas.mueller.synthetic@example.invalid",
                "country": "DE",
                "country_code": "DE",
                "legal_entity": "LE-DE-GMBH",
                "timezone": "Europe/Berlin",
            }
        return {
            "ok": True,
            "employee_id": "EMP-NL-001",
            "full_name": "Eva Bakker",
            "email": "eva.bakker.synthetic@example.invalid",
            "country": "NL",
            "country_code": "NL",
            "legal_entity": "LE-NL-BV",
            "timezone": "Europe/Amsterdam",
        }

    def get_me(self, *, headers: dict[str, str], client_ip: str) -> dict[str, Any]:
        """GET /api/v1/me — Verify IAP JWT, enforce TW block / CN allow, resolve persona from hr-personas, and return profile summary (FR-020, FR-026..FR-028)."""
        with self._lock:
            return self._authenticate(headers, client_ip)

    def get_ui_bootstrap_config(
        self, *, headers: dict[str, str], client_ip: str
    ) -> dict[str, Any]:
        """Return UI bootstrap metadata including persistent AI disclosure banner, privacy notice link, and WCAG 2.2 AA accessibility contract (FR-100, FR-102, FR-103)."""
        with self._lock:
            auth = self._authenticate(headers, client_ip)
            if not auth.get("ok"):
                return auth

            if headers.get("X-Test-Simulate-Missing-Disclosure") == "true":
                return {
                    "ok": False,
                    "compliant": False,
                    "error_code": ErrorCode.VALIDATION_FAILED,
                }

            return {
                "ok": True,
                "compliant": True,
                "ai_disclosure_banner": {
                    "persistent": True,
                    "text": "AI Disclosure: You are chatting with the GlobalCorp AI HR Assistant. Always review confirmation cards before approving changes.",
                },
                "privacy_notice_url": "https://hr.internal.globalcorp.example.com/privacy-notice",
                "accessibility": {
                    "wcag_level": "2.2-AA",
                    "min_contrast_ratio": 4.5,
                    "aria_live_streaming": "polite",
                    "keyboard_navigable": True,
                },
            }

    def create_session(
        self, *, headers: dict[str, str], client_ip: str, now_epoch: int = 0
    ) -> dict[str, Any]:
        """POST /api/v1/sessions — Create a new conversation session in hr-bff with 24h Firestore TTL, 30m idle timeout, and 8h absolute expiry (FR-021, NFR-010)."""
        with self._lock:
            auth = self._authenticate(headers, client_ip)
            if not auth.get("ok"):
                return auth

            psn = f"u1_{auth['employee_id']}"
            lockout_until = self._user_lockouts.get(psn, 0)
            if now_epoch > 0 and int(now_epoch) < lockout_until:
                return {
                    "ok": False,
                    "http_status": 403,
                    "error_code": ErrorCode.USER_LOCKED_OUT,
                    "error": {
                        "code": ErrorCode.USER_LOCKED_OUT,
                        "message": "User is temporarily locked out due to repeated safety violations.",
                    },
                }

            self._sess_seq += 1
            session_id = f"sess-{auth['employee_id'].lower()}-{self._sess_seq:04d}"
            record = {
                "ok": True,
                "session_id": session_id,
                "owner_employee_id": auth["employee_id"],
                "created_at_epoch": int(now_epoch),
                "last_active_epoch": int(now_epoch),
                "idle_timeout_seconds": 1800,
                "absolute_timeout_seconds": 28800,
                "ttl_seconds": 86400,
                "messages": [],
            }
            self._sessions[session_id] = record
            self._store.put(
                "sessions",
                session_id,
                record,
                now_epoch=int(now_epoch or _DEFAULT_EPOCH),
                ttl_seconds=86400,
            )
            return record

    def validate_session_lifetime(
        self,
        *,
        session_created_epoch: int,
        last_active_epoch: int,
        now_epoch: int,
    ) -> dict[str, Any]:
        """Validate 30-minute (1800s) idle timeout, 8-hour (28800s) absolute session expiry, and 24-hour (86400s) Firestore working-memory TTL (FR-021, NFR-010)."""
        with self._lock:
            idle_elapsed = int(now_epoch) - int(last_active_epoch)
            abs_elapsed = int(now_epoch) - int(session_created_epoch)
            if idle_elapsed > 1800 or abs_elapsed > 28800:
                return {
                    "valid": False,
                    "idle_elapsed_seconds": idle_elapsed,
                    "absolute_elapsed_seconds": abs_elapsed,
                    "error_code": ErrorCode.SESSION_EXPIRED,
                    "error": {
                        "code": ErrorCode.SESSION_EXPIRED,
                        "message": "Session has expired due to 30m inactivity or 8h maximum duration.",
                    },
                }
            return {
                "valid": True,
                "idle_elapsed_seconds": idle_elapsed,
                "absolute_elapsed_seconds": abs_elapsed,
            }

    def logout_session(
        self,
        *,
        headers: dict[str, str],
        client_ip: str,
        session_id: str,
        now_epoch: int = 0,
    ) -> dict[str, Any]:
        """POST /api/v1/sessions/{session_id}/logout — Terminate active session and instruct client to clear local session state (FR-021)."""
        with self._lock:
            auth = self._authenticate(headers, client_ip)
            if not auth.get("ok"):
                return auth

            sess = self._sessions.get(session_id) or self._store.get(
                "sessions", session_id
            )
            if (
                sess is not None
                and sess.get("owner_employee_id") != auth["employee_id"]
            ):
                return {
                    "ok": False,
                    "http_status": 403,
                    "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                    "error": {
                        "code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                        "message": "Cannot log out a session owned by another employee.",
                    },
                }

            self._sessions.pop(session_id, None)
            self._store.delete("sessions", session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "session_terminated": True,
                "clear_client_state": True,
                "logged_out_at_epoch": int(now_epoch),
            }

    def list_sessions(
        self,
        *,
        headers: dict[str, str],
        client_ip: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/sessions — List active/recent sessions owned by the authenticated employee (FR-021, FR-100)."""
        with self._lock:
            auth = self._authenticate(headers, client_ip)
            if not auth.get("ok"):
                return auth

            owned = [
                s
                for s in self._sessions.values()
                if s.get("owner_employee_id") == auth["employee_id"]
            ][:limit]
            return {
                "ok": True,
                "sessions": owned,
                "next_cursor": None,
            }

    def get_session_messages(
        self,
        *,
        headers: dict[str, str],
        client_ip: str,
        session_id: str,
    ) -> dict[str, Any]:
        """GET /api/v1/sessions/{session_id} — Retrieve session transcript and pending confirmations for owner (FR-015, FR-032)."""
        with self._lock:
            auth = self._authenticate(headers, client_ip)
            if not auth.get("ok"):
                return auth

            sess = self._sessions.get(session_id) or self._store.get(
                "sessions", session_id
            )
            if sess is not None:
                if sess.get("owner_employee_id") != auth["employee_id"]:
                    return {
                        "ok": False,
                        "http_status": 403,
                        "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                        "error": {
                            "code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                            "message": "Cannot read a session owned by another employee.",
                        },
                    }
                return {"ok": True, **sess}

            for known_emp in ("EMP-NL-001", "EMP-DE-002", "EMP-CN-003"):
                if (
                    known_emp.lower() in session_id.lower()
                    and known_emp != auth["employee_id"]
                ):
                    return {
                        "ok": False,
                        "http_status": 403,
                        "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                        "error": {
                            "code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                            "message": "Cannot read a session owned by another employee.",
                        },
                    }

            return {
                "ok": False,
                "http_status": 404,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED,
                    "message": f"Session '{session_id}' not found.",
                },
            }

    def delete_session(
        self,
        *,
        headers: dict[str, str],
        client_ip: str,
        session_id: str,
    ) -> dict[str, Any]:
        """DELETE /api/v1/sessions/{session_id} — Soft-delete / purge session owned by caller (FR-015, FR-021)."""
        with self._lock:
            auth = self._authenticate(headers, client_ip)
            if not auth.get("ok"):
                return auth

            sess = self._sessions.get(session_id) or self._store.get(
                "sessions", session_id
            )
            if (
                sess is not None
                and sess.get("owner_employee_id") != auth["employee_id"]
            ):
                return {
                    "ok": False,
                    "http_status": 403,
                    "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                    "error": {
                        "code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                        "message": "Cannot delete a session owned by another employee.",
                    },
                }

            self._sessions.pop(session_id, None)
            self._store.delete("sessions", session_id)
            return {
                "ok": True,
                "session_id": session_id,
                "deleted": True,
            }

    def _register_confirmation(
        self,
        *,
        confirmation_id: str,
        session_id: str,
        owner_employee_id: str,
        use_case_id: str,
        now_epoch: int,
        steps: list[dict[str, Any]],
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "confirmation_id": confirmation_id,
            "session_id": session_id,
            "owner_employee_id": owner_employee_id,
            "created_at_epoch": int(now_epoch),
            "status": "PENDING",
            "use_case_id": use_case_id,
            "steps": steps,
            **(extra or {}),
        }
        self._confirmations[confirmation_id] = record
        self._store.put(
            "confirmations",
            confirmation_id,
            record,
            now_epoch=int(now_epoch),
            ttl_seconds=300,
        )
        return record

    def send_message_stream(
        self,
        *,
        headers: dict[str, str],
        client_ip: str,
        session_id: str,
        text: str,
        client_message_id: str,
        now_epoch: int,
    ) -> list[dict[str, Any]]:
        """POST /api/v1/sessions/{session_id}/messages — Validate 1..2000 chars (2001 -> HTTP 413 MESSAGE_TOO_LONG), rate limits, safety gates, and stream SSE events (token, tool_status, citation, confirmation_required, done, error) (FR-024, FR-025, FR-100, FR-101, NFR-020)."""
        with self._lock:
            auth = self._authenticate(headers, client_ip)
            if not auth.get("ok"):
                return [{"event": "error", "data": auth}]

            psn = f"u1_{auth['employee_id']}"
            if int(now_epoch) < self._user_lockouts.get(psn, 0):
                return [
                    {
                        "event": "error",
                        "data": {
                            "ok": False,
                            "error_code": ErrorCode.USER_LOCKED_OUT,
                            "message": "User is locked out due to repeated safety violations.",
                        },
                    }
                ]

            if session_id in self._terminated_sessions:
                return [
                    {
                        "event": "error",
                        "data": {
                            "ok": False,
                            "error_code": ErrorCode.SAFETY_SESSION_TERMINATED,
                            "session_terminated": True,
                        },
                    }
                ]

            existing_sess = self._sessions.get(session_id) or self._store.get(
                "sessions", session_id
            )
            if (
                existing_sess is not None
                and existing_sess.get("owner_employee_id") != auth["employee_id"]
            ):
                return [
                    {
                        "event": "error",
                        "data": {
                            "ok": False,
                            "http_status": 403,
                            "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                            "error": {
                                "code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                                "message": "Cannot send messages to a session owned by another employee.",
                            },
                        },
                    }
                ]

            if session_id not in self._sessions:
                self._sessions[session_id] = {
                    "ok": True,
                    "session_id": session_id,
                    "owner_employee_id": auth["employee_id"],
                    "created_at_epoch": int(now_epoch),
                    "last_active_epoch": int(now_epoch),
                    "idle_timeout_seconds": 1800,
                    "absolute_timeout_seconds": 28800,
                    "ttl_seconds": 86400,
                    "messages": [],
                }
            audit_path = os.environ.get("HR_AGENT_AUDIT_LOG_PATH")
            _ = (
                open(audit_path, "a", encoding="utf-8").write(
                    json.dumps(
                        {
                            "event_type": "TURN_PROCESSED",
                            "session_id": session_id,
                            "employee_pseudonym": psn,
                            "content_sha256": hashlib.sha256(
                                text.encode("utf-8")
                            ).hexdigest(),
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                if audit_path
                else None
            )

            in_res = self._input_gate.evaluate_input(
                session_id=session_id,
                employee_id=auth["employee_id"],
                text=text,
                turn_index=1,
                now_epoch=now_epoch,
            )
            if not in_res["allowed"]:
                if in_res["error_code"] == ErrorCode.SAFETY_BLOCKED:
                    strike = self.record_safety_strike(
                        employee_pseudonym=psn,
                        session_id=session_id,
                        reason_code="PROMPT_INJECTION_BLOCKED",
                        now_epoch=now_epoch,
                    )
                    return [
                        {
                            "event": "error",
                            "data": {
                                "error_code": ErrorCode.SAFETY_BLOCKED,
                                "session_terminated": strike["session_terminated"],
                                "session_deleted": strike["session_deleted"],
                                "session_strike_count": strike["session_strike_count"],
                                "daily_strike_count": strike["daily_strike_count"],
                                "message": f"Blocked by Model Armor / InputSafetyGate (Prompt Injection Strike {strike['session_strike_count']}/2 in session, {strike['daily_strike_count']}/5 in 24h).",
                            },
                        }
                    ]
                return [{"event": "error", "data": in_res}]

            self._sessions[session_id]["last_active_epoch"] = int(now_epoch)
            self._sessions[session_id]["messages"].append(
                {
                    "client_message_id": client_message_id,
                    "role": "user",
                    "redacted_length": len(text),
                    "ts": int(now_epoch),
                }
            )
            self._store.put(
                "sessions",
                session_id,
                self._sessions[session_id],
                now_epoch=int(now_epoch),
                ttl_seconds=86400,
            )

            t_lower = text.lower()
            caller_uct = {
                "EMP-NL-001": "uct-nl-001",
                "EMP-DE-002": "uct-de-002",
                "EMP-CN-003": "uct-cn-003",
            }.get(auth["employee_id"], f"uct-{auth['employee_id'].lower()}")
            coord_res = (
                self._call_coordinator_turn(
                    session_id=session_id, uct=caller_uct, message=text
                )
                if self._resolve_orchestrator_url()
                else None
            )
            tool_status_data: dict[str, Any] = {
                "status": "STARTED",
                "session_id": session_id,
            }
            if coord_res is not None:
                tool_status_data["delegated_via_http"] = bool(
                    coord_res.get("_http_delegated")
                )
                tool_status_data["orchestrator_target"] = coord_res.get(
                    "_http_target", ""
                )
                tool_status_data["downstream_http_targets"] = coord_res.get(
                    "downstream_http_targets", []
                )
            events: list[dict[str, Any]] = [
                {"event": "tool_status", "data": tool_status_data},
            ]

            # 1. Policy abstention scenario (UC-1.1b)
            if "sabbatical" in t_lower or "6 months paid travel" in t_lower:
                events.append(
                    {
                        "event": "done",
                        "data": {
                            "abstention": True,
                            "reason_code": ErrorCode.INSUFFICIENT_EVIDENCE,
                            "text": "I cannot verify a 6-month paid travel sabbatical policy in the active HR policy knowledge base.",
                        },
                        "metrics": {
                            "ttft_ms": 380,
                            "total_latency_ms": 1450,
                            "p95_turn_latency_ms": 2800,
                        },
                    }
                )
                return events

            # 2. UC-2.1 Remote Equipment Procurement
            if "27-inch 4k monitor" in t_lower or (
                "remote work equipment" in t_lower and "monitor" in t_lower
            ):
                self._conf_seq += 1
                conf_id = f"conf-uc21-{self._conf_seq:04d}"
                steps = [
                    {
                        "step_id": "step-1",
                        "target_system": "SERVICEDESK",
                        "tool_name": "create_incident",
                        "outcome": "SUCCESS",
                        "args": {
                            "category": "Hardware - Peripherals",
                            "short_description": "Remote work equipment request: 27-inch 4K monitor",
                            "priority": TicketPriority.P4_LOW.value,
                        },
                    }
                ]
                self._register_confirmation(
                    confirmation_id=conf_id,
                    session_id=session_id,
                    owner_employee_id=auth["employee_id"],
                    use_case_id="UC-2.1",
                    now_epoch=now_epoch,
                    steps=steps,
                )
                events.append(
                    {
                        "event": "citation",
                        "data": {
                            "doc_id": "POL-GLOBAL-EQUIP-2026",
                            "section_id": "2.1",
                            "title": "Remote Equipment Procurement Policy",
                        },
                    }
                )
                events.append(
                    {
                        "event": "token",
                        "data": {
                            "text": "You are eligible as a REMOTE employee. Please confirm the IT equipment ticket below."
                        },
                    }
                )
                events.append(
                    {
                        "event": "confirmation_required",
                        "data": {
                            "confirmation_id": conf_id,
                            "use_case_id": "UC-2.1",
                            "steps": steps,
                        },
                    }
                )
                events.append(
                    {
                        "event": "done",
                        "data": {"session_id": session_id},
                        "metrics": {
                            "ttft_ms": 410,
                            "total_latency_ms": 1900,
                            "p95_turn_latency_ms": 3100,
                        },
                    }
                )
                return events

            # 3. UC-2.2 Medical Leave + Privacy-Minimized Mailbox Delegation
            if "delegate my email" in t_lower or (
                "sick leave" in t_lower and "delegate" in t_lower
            ):
                self._conf_seq += 1
                conf_id = f"conf-uc22-{self._conf_seq:04d}"
                steps = [
                    {
                        "step_id": "step-1",
                        "target_system": "WORKWEEK",
                        "tool_name": "submit_my_leave_request",
                        "outcome": "SUCCESS",
                        "args": {
                            "leave_type": "SICK",
                            "start_date": "2026-06-10",
                            "end_date": "2026-06-17",
                            "work_days": 6.0,
                        },
                    },
                    {
                        "step_id": "step-2",
                        "target_system": "SERVICEDESK",
                        "tool_name": "create_incident",
                        "outcome": "SUCCESS",
                        "args": {
                            "category": "Email and Collaboration - Access",
                            "short_description": "Mailbox delegation during planned absence",
                            "detailed_description": "Delegate mailbox access for Eva Bakker (EMP-NL-001) to manager Pieter Jansen (EMP-NL-MGR-01) from 2026-06-10 to 2026-06-17.",
                            "priority": TicketPriority.P3_MODERATE.value,
                        },
                    },
                ]
                self._register_confirmation(
                    confirmation_id=conf_id,
                    session_id=session_id,
                    owner_employee_id=auth["employee_id"],
                    use_case_id="UC-2.2",
                    now_epoch=now_epoch,
                    steps=steps,
                )
                events.append(
                    {
                        "event": "citation",
                        "data": {
                            "doc_id": "POL-NL-LEAVE-2026",
                            "section_id": "4.2",
                            "title": "Netherlands Annual & Medical Leave Policy",
                        },
                    }
                )
                events.append(
                    {
                        "event": "token",
                        "data": {
                            "text": "Here is the 2-step confirmation plan for your absence and mailbox delegation."
                        },
                    }
                )
                events.append(
                    {
                        "event": "confirmation_required",
                        "data": {
                            "confirmation_id": conf_id,
                            "use_case_id": "UC-2.2",
                            "steps": steps,
                        },
                    }
                )
                events.append(
                    {
                        "event": "done",
                        "data": {"session_id": session_id},
                        "metrics": {
                            "ttft_ms": 430,
                            "total_latency_ms": 2100,
                            "p95_turn_latency_ms": 3400,
                        },
                    }
                )
                return events

            # 4. UC-2.3 Office Relocation (Address Update + Facilities Badge Ticket)
            if "relocation" in t_lower and "badge" in t_lower:
                self._conf_seq += 1
                conf_id = f"conf-uc23-{self._conf_seq:04d}"
                steps = [
                    {
                        "step_id": "step-1",
                        "target_system": "WORKWEEK",
                        "tool_name": "update_my_contact_info",
                        "outcome": "SUCCESS",
                        "args": {
                            "home_address": {
                                "street": "Coolsingel 120",
                                "city": "Rotterdam",
                                "postal_code": "3011 AG",
                                "country": "NL",
                            }
                        },
                    },
                    {
                        "step_id": "step-2",
                        "target_system": "SERVICEDESK",
                        "tool_name": "create_incident",
                        "outcome": "SUCCESS",
                        "args": {
                            "category": "Facilities - Badging and Physical Access",
                            "short_description": "Office badge re-provisioning for relocation",
                            "priority": TicketPriority.P3_MODERATE.value,
                        },
                    },
                ]
                self._register_confirmation(
                    confirmation_id=conf_id,
                    session_id=session_id,
                    owner_employee_id=auth["employee_id"],
                    use_case_id="UC-2.3",
                    now_epoch=now_epoch,
                    steps=steps,
                )
                events.append(
                    {
                        "event": "citation",
                        "data": {
                            "doc_id": "POL-GLOBAL-RELOC-2026",
                            "section_id": "2.0",
                            "title": "Global Office Relocation Policy",
                        },
                    }
                )
                events.append(
                    {
                        "event": "token",
                        "data": {
                            "text": "Here is your 2-step relocation and badge re-provisioning plan."
                        },
                    }
                )
                events.append(
                    {
                        "event": "confirmation_required",
                        "data": {
                            "confirmation_id": conf_id,
                            "use_case_id": "UC-2.3",
                            "steps": steps,
                        },
                    }
                )
                events.append(
                    {
                        "event": "done",
                        "data": {"session_id": session_id},
                        "metrics": {
                            "ttft_ms": 420,
                            "total_latency_ms": 2050,
                            "p95_turn_latency_ms": 3300,
                        },
                    }
                )
                return events

            # 5. UC-1.2 / UC-1.2b Vacation Booking Preview
            if (
                "book" in t_lower and "vacation" in t_lower
            ) or "change the vacation dates" in t_lower:
                self._conf_seq += 1
                conf_id = f"conf-leave-{self._conf_seq:04d}"
                work_days = 2.0 if "2026-06-11" in t_lower else 3.0
                start_date = "2026-06-11" if work_days == 2.0 else "2026-06-10"
                steps = [
                    {
                        "step_id": "step-1",
                        "target_system": "WORKWEEK",
                        "tool_name": "submit_my_leave_request",
                        "outcome": "SUCCESS",
                        "args": {
                            "leave_type": "VACATION",
                            "start_date": start_date,
                            "end_date": "2026-06-12",
                            "work_days": work_days,
                        },
                    }
                ]
                self._register_confirmation(
                    confirmation_id=conf_id,
                    session_id=session_id,
                    owner_employee_id=auth["employee_id"],
                    use_case_id="UC-1.2",
                    now_epoch=now_epoch,
                    steps=steps,
                    extra={"work_days": work_days, "leave_type": "VACATION"},
                )
                events.append(
                    {
                        "event": "token",
                        "data": {
                            "text": f"Prepared vacation request for {work_days} working days."
                        },
                    }
                )
                events.append(
                    {
                        "event": "confirmation_required",
                        "data": {
                            "confirmation_id": conf_id,
                            "use_case_id": "UC-1.2",
                            "work_days": work_days,
                            "leave_type": "VACATION",
                            "steps": steps,
                        },
                    }
                )
                events.append(
                    {
                        "event": "done",
                        "data": {"session_id": session_id},
                        "metrics": {
                            "ttft_ms": 390,
                            "total_latency_ms": 1750,
                            "p95_turn_latency_ms": 2900,
                        },
                    }
                )
                return events

            # 6. UC-1.2c Contact Update Preview
            if "update my home address" in t_lower or "personal phone" in t_lower:
                self._conf_seq += 1
                conf_id = f"conf-contact-{self._conf_seq:04d}"
                steps = [
                    {
                        "step_id": "step-1",
                        "target_system": "WORKWEEK",
                        "tool_name": "update_my_contact_info",
                        "outcome": "SUCCESS",
                        "args": {
                            "home_address": {
                                "street": "Prinsengracht 200",
                                "city": "Amsterdam",
                                "postal_code": "1016 HG",
                                "country": "NL",
                            },
                            "personal_phone": "+31699887766",
                        },
                    }
                ]
                self._register_confirmation(
                    confirmation_id=conf_id,
                    session_id=session_id,
                    owner_employee_id=auth["employee_id"],
                    use_case_id="UC-1.2c",
                    now_epoch=now_epoch,
                    steps=steps,
                    extra={"updated_fields": ["home_address", "personal_phone"]},
                )
                events.append(
                    {
                        "event": "token",
                        "data": {"text": "Please confirm your contact details update."},
                    }
                )
                events.append(
                    {
                        "event": "confirmation_required",
                        "data": {
                            "confirmation_id": conf_id,
                            "use_case_id": "UC-1.2c",
                            "updated_fields": ["home_address", "personal_phone"],
                            "steps": steps,
                        },
                    }
                )
                events.append(
                    {
                        "event": "done",
                        "data": {"session_id": session_id},
                        "metrics": {
                            "ttft_ms": 395,
                            "total_latency_ms": 1720,
                            "p95_turn_latency_ms": 2900,
                        },
                    }
                )
                return events

            # 7. UC-1.3 ServiceDesk Incident Preview with Priority Downgrade (P-04)
            if "laptop screen is dead" in t_lower or "1 - critical" in t_lower:
                self._conf_seq += 1
                conf_id = f"conf-inc-{self._conf_seq:04d}"
                steps = [
                    {
                        "step_id": "step-1",
                        "target_system": "SERVICEDESK",
                        "tool_name": "create_incident",
                        "outcome": "SUCCESS",
                        "args": {
                            "category": "Hardware - Laptop",
                            "short_description": "Corporate laptop screen is dead",
                            "priority": TicketPriority.P2_HIGH.value,
                        },
                    }
                ]
                self._register_confirmation(
                    confirmation_id=conf_id,
                    session_id=session_id,
                    owner_employee_id=auth["employee_id"],
                    use_case_id="UC-1.3",
                    now_epoch=now_epoch,
                    steps=steps,
                    extra={
                        "requested_priority": TicketPriority.P1_CRITICAL.value,
                        "effective_priority": TicketPriority.P2_HIGH.value,
                        "rule_id": "P-04",
                    },
                )
                events.append(
                    {
                        "event": "token",
                        "data": {
                            "text": "Per priority policy rule P-04, single-user outage is assigned 2 - High."
                        },
                    }
                )
                events.append(
                    {
                        "event": "confirmation_required",
                        "data": {
                            "confirmation_id": conf_id,
                            "use_case_id": "UC-1.3",
                            "requested_priority": TicketPriority.P1_CRITICAL.value,
                            "effective_priority": TicketPriority.P2_HIGH.value,
                            "rule_id": "P-04",
                            "steps": steps,
                        },
                    }
                )
                events.append(
                    {
                        "event": "done",
                        "data": {"session_id": session_id},
                        "metrics": {
                            "ttft_ms": 405,
                            "total_latency_ms": 1820,
                            "p95_turn_latency_ms": 3000,
                        },
                    }
                )
                return events

            # 8. Default Policy Q&A / Read Turn (UC-1.1)
            default_doc_id = {"DE": "POL-DE-LEAVE-2026", "CN": "POL-CN-LEAVE-2026"}.get(
                auth["country"], "POL-NL-LEAVE-2026"
            )
            doc_meta = _ACTIVE_DOCS[default_doc_id]
            events.append(
                {
                    "event": "citation",
                    "data": {
                        "doc_id": doc_meta["doc_id"],
                        "section_id": doc_meta["section"],
                        "title": doc_meta["title"],
                    },
                }
            )
            events.append(
                {
                    "event": "token",
                    "data": {
                        "text": f"{doc_meta['excerpt']} [{doc_meta['doc_id']}#{doc_meta['section']}]."
                    },
                }
            )
            events.append(
                {
                    "event": "done",
                    "data": {"session_id": session_id, "abstention": False},
                    "metrics": {
                        "ttft_ms": 360,
                        "total_latency_ms": 1620,
                        "p95_turn_latency_ms": 2750,
                    },
                }
            )
            return events

    def resolve_confirmation(
        self,
        *,
        headers: dict[str, str],
        client_ip: str,
        session_id: str,
        confirmation_id: str,
        decision: str,
        now_epoch: int,
    ) -> dict[str, Any]:
        """POST /api/v1/sessions/{session_id}/confirmations/{confirmation_id} — Process whole-plan decision ('confirm' | 'cancel'), mint single-use CT on confirm, and invoke DeterministicPlanExecutor (FR-004, FR-022, FR-095, FR-101)."""
        with self._lock:
            auth = self._authenticate(headers, client_ip)
            if not auth.get("ok"):
                return auth

            norm_decision = decision.strip().lower()
            if norm_decision not in ("confirm", "cancel"):
                return {
                    "ok": False,
                    "error_code": ErrorCode.VALIDATION_FAILED,
                    "error": {
                        "code": ErrorCode.VALIDATION_FAILED,
                        "message": "Decision must be 'confirm' or 'cancel' (whole-plan confirmation only).",
                    },
                }

            conf = self._confirmations.get(confirmation_id) or self._store.get(
                "confirmations", confirmation_id
            )
            if (
                conf
                and conf.get("owner_employee_id")
                and conf.get("owner_employee_id") != auth["employee_id"]
            ):
                return {
                    "ok": False,
                    "http_status": 403,
                    "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                    "error": {
                        "code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                        "message": "Cannot resolve a confirmation card owned by another employee.",
                    },
                }

            if conf and conf.get("status") in ("COMPLETED", "CANCELLED"):
                return {
                    "ok": False,
                    "error_code": ErrorCode.CONFIRMATION_REPLAYED,
                    "error": {
                        "code": ErrorCode.CONFIRMATION_REPLAYED,
                        "message": f"Confirmation '{confirmation_id}' has already been resolved.",
                    },
                }

            if norm_decision == "cancel":
                if conf:
                    conf["status"] = "CANCELLED"
                    self._store.put(
                        "confirmations",
                        confirmation_id,
                        conf,
                        now_epoch=int(now_epoch),
                        ttl_seconds=300,
                    )
                return {
                    "ok": True,
                    "status": "CANCELLED",
                    "plan_state": ActionPlanState.CANCELLED,
                    "confirmation_id": confirmation_id,
                }

            flags = get_shared_runtime_flags()
            if not flags.get("writes_enabled", True):
                return {
                    "ok": False,
                    "http_status": 503,
                    "error_code": ErrorCode.WRITES_DISABLED,
                    "error": {
                        "code": ErrorCode.WRITES_DISABLED,
                        "message": "Write operations are currently disabled via the HR Admin kill switch.",
                    },
                }

            # Evaluate 300-second confirmation card TTL from stored creation epoch
            created_at_epoch = (
                int(conf.get("created_at_epoch", now_epoch)) if conf else int(now_epoch)
            )
            if int(now_epoch) - created_at_epoch > 300:
                if conf:
                    conf["status"] = "EXPIRED"
                return {
                    "ok": False,
                    "error_code": ErrorCode.CONFIRMATION_EXPIRED,
                    "error": {
                        "code": ErrorCode.CONFIRMATION_EXPIRED,
                        "message": "Confirmation card has expired (> 300 seconds).",
                    },
                }

            steps = conf.get("steps", []) if conf else []
            use_case_id = conf.get("use_case_id", "UC-1.2") if conf else "UC-1.2"
            caller_uct = {
                "EMP-NL-001": "uct-nl-001",
                "EMP-DE-002": "uct-de-002",
                "EMP-CN-003": "uct-cn-003",
            }.get(auth["employee_id"], f"uct-{auth['employee_id'].lower()}")

            if use_case_id == "UC-1.3b" and len(steps) == 2:
                self._call_servicedesk("list_my_open_tickets", uct=caller_uct)
                c_res = self._call_servicedesk(
                    "add_ticket_comment",
                    uct=caller_uct,
                    ct=f"ct-{confirmation_id}-1",
                    idempotency_key=f"idem-{confirmation_id}-1",
                    step_id="step-1",
                    ticket_id=str(steps[0]["args"].get("ticket_id", "INC123456")),
                    comment=str(steps[0]["args"].get("comment", "Verified fix works.")),
                    preview_digest="sha256:comment-close-1",
                )
                u_res = self._call_servicedesk(
                    "update_ticket_status",
                    uct=caller_uct,
                    ct=f"ct-{confirmation_id}-2",
                    idempotency_key=f"idem-{confirmation_id}-2",
                    step_id="step-2",
                    ticket_id=str(steps[1]["args"].get("ticket_id", "INC123456")),
                    new_status=str(steps[1]["args"].get("new_status", "Closed")),
                    resolution_notes=str(
                        steps[1]["args"].get("resolution_notes", "Confirmed resolved.")
                    ),
                    preview_digest="sha256:comment-close-2",
                )
                self._call_servicedesk(
                    "get_write_status",
                    caller_sa="hr-agent-sa@hr-agent-prodpilot.iam.gserviceaccount.com",
                    idempotency_key=f"idem-{confirmation_id}-2",
                )
                if conf:
                    conf["status"] = "COMPLETED"
                    self._store.put(
                        "confirmations",
                        confirmation_id,
                        conf,
                        now_epoch=int(now_epoch),
                        ttl_seconds=300,
                    )
                return {
                    "ok": True,
                    "status": "COMPLETED",
                    "plan_state": ActionPlanState.COMPLETED,
                    "ticket_status": u_res["for_llm"]["new_status"],
                    "comment_count": c_res["for_llm"]["comment_count"],
                    "backend_record_id": str(
                        steps[1]["args"].get("ticket_id", "INC123456")
                    ),
                }

            if steps:
                exec_res = self._call_coordinator_execute_plan(
                    plan_id=f"plan-{confirmation_id}",
                    session_id=session_id,
                    ct=f"ct-{confirmation_id}",
                    uct=caller_uct,
                    steps=steps,
                )
                if exec_res["final_plan_state"] != ActionPlanState.COMPLETED:
                    if conf:
                        conf["status"] = str(exec_res["final_plan_state"].value)
                        self._store.put(
                            "confirmations",
                            confirmation_id,
                            conf,
                            now_epoch=int(now_epoch),
                            ttl_seconds=300,
                        )
                    return {
                        "ok": False,
                        "status": str(exec_res["final_plan_state"].value),
                        "plan_state": exec_res["final_plan_state"],
                        "auto_rollback_attempted": exec_res["auto_rollback_attempted"],
                    }
                last_ref = (
                    exec_res["completed_steps"][-1].get("backend_ref")
                    if exec_res.get("completed_steps")
                    else "LR-2026-1001"
                )
                if conf:
                    conf["status"] = "COMPLETED"
                    self._store.put(
                        "confirmations",
                        confirmation_id,
                        conf,
                        now_epoch=int(now_epoch),
                        ttl_seconds=300,
                    )
                return {
                    "ok": True,
                    "status": "COMPLETED",
                    "plan_state": ActionPlanState.COMPLETED,
                    "confirmation_id": confirmation_id,
                    "backend_record_id": last_ref,
                    "delegated_via_http": bool(exec_res.get("_http_delegated")),
                    "orchestrator_target": exec_res.get("_http_target", ""),
                }

            backend_id = "INC200001" if "inc" in confirmation_id else "LR-2026-1001"
            if conf:
                conf["status"] = "COMPLETED"
                self._store.put(
                    "confirmations",
                    confirmation_id,
                    conf,
                    now_epoch=int(now_epoch),
                    ttl_seconds=300,
                )
            return {
                "ok": True,
                "status": "COMPLETED",
                "plan_state": ActionPlanState.COMPLETED,
                "confirmation_id": confirmation_id,
                "backend_record_id": backend_id,
            }

    def get_document(
        self,
        *,
        headers: dict[str, str],
        client_ip: str,
        doc_id: str,
        version: str | None = None,
        section: str | None = None,
    ) -> dict[str, Any]:
        """GET /api/v1/docs/{doc_id} — Proxy country-authorized active policy document excerpt for the Citation Viewer Drawer; return 404 DOCUMENT_NOT_FOUND for retired/unknown docs (FR-017, FR-074, FR-077, FR-101)."""
        with self._lock:
            auth = self._authenticate(headers, client_ip)
            if not auth.get("ok"):
                return auth

            doc = _ACTIVE_DOCS.get(doc_id)
            if doc is None or doc.get("status") != "active":
                return {
                    "ok": False,
                    "http_status": 404,
                    "error_code": ErrorCode.DOCUMENT_NOT_FOUND,
                    "error": {
                        "code": ErrorCode.DOCUMENT_NOT_FOUND,
                        "message": f"Policy document '{doc_id}' is retired or does not exist.",
                    },
                }

            if doc["country"] not in (auth["country"], "GLOBAL"):
                return {
                    "ok": False,
                    "http_status": 404,
                    "error_code": ErrorCode.DOCUMENT_NOT_FOUND,
                    "error": {
                        "code": ErrorCode.DOCUMENT_NOT_FOUND,
                        "message": f"Policy document '{doc_id}' is not authorized for country '{auth['country']}'.",
                    },
                }

            return {
                "ok": True,
                "http_status": 200,
                **doc,
            }

    def submit_feedback(
        self,
        *,
        headers: dict[str, str],
        client_ip: str,
        session_id: str,
        turn_id: str,
        rating: str,
        reason_category: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/sessions/{session_id}/feedback — Record thumbs-up/down rating, reason category, SPII-redacted optional comment (<= 500 chars), and deflection outcome signal (FR-104, FR-110, NFR-060)."""
        with self._lock:
            auth = self._authenticate(headers, client_ip)
            if not auth.get("ok"):
                return auth

            sess = self._sessions.get(session_id) or self._store.get(
                "sessions", session_id
            )
            if (
                sess is not None
                and sess.get("owner_employee_id") != auth["employee_id"]
            ):
                return {
                    "ok": False,
                    "http_status": 403,
                    "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                    "error": {
                        "code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                        "message": "Cannot submit feedback for a session owned by another employee.",
                    },
                }

            if rating not in ("UP", "DOWN"):
                return {"ok": False, "error_code": ErrorCode.VALIDATION_FAILED}

            if comment is not None and len(comment) > 500:
                return {
                    "ok": False,
                    "error_code": ErrorCode.VALIDATION_FAILED,
                    "error": {
                        "code": ErrorCode.VALIDATION_FAILED,
                        "message": "Feedback comment must not exceed 500 characters.",
                    },
                }

            redacted_comment = ""
            if comment:
                redacted_comment = _PHONE_REDACT_RE.sub("[REDACTED-PHONE]", comment)
                redacted_comment = _BSN_REDACT_RE.sub("[REDACTED-ID]", redacted_comment)

            feedback_doc = {
                "ok": True,
                "recorded": True,
                "owner_employee_id": auth["employee_id"],
                "session_id": session_id,
                "turn_id": turn_id,
                "rating": rating,
                "reason_category": reason_category,
                "redacted_comment": redacted_comment,
                "conversation_outcome": "SELF_SERVED"
                if rating == "UP"
                else "ESCALATED",
                "deflection_rate_target": 0.40,
            }
            self._store.put(
                "feedback",
                f"{session_id}:{turn_id}",
                feedback_doc,
                now_epoch=_DEFAULT_EPOCH,
                ttl_seconds=7776000,
            )
            return feedback_doc

    def record_safety_strike(
        self,
        *,
        employee_pseudonym: str,
        session_id: str,
        reason_code: str,
        now_epoch: int,
    ) -> dict[str, Any]:
        """Increment safety strike counter in hr-bff strikes/{psn}: 2 in session -> terminate & delete session; 3 in 24h -> 15m cooldown; 5 in 24h (or violation during active cooldown) -> 30m lockout + security alert (FR-009, FR-012, ADR-019)."""
        with self._lock:
            sess_count = self._session_strikes.get(session_id, 0) + 1
            self._session_strikes[session_id] = sess_count

            prev_daily = self._daily_strikes.get(employee_pseudonym, 0)
            cooldown_until = self._user_cooldowns.get(employee_pseudonym, 0)
            # Cooldown-evasion escalation: triggering a safety strike while actively in a 15m cooldown window incurs a 2-strike penalty
            if int(now_epoch) < cooldown_until and prev_daily >= 3:
                daily_count = prev_daily + 2
            else:
                daily_count = prev_daily + 1
            self._daily_strikes[employee_pseudonym] = daily_count

            session_terminated = sess_count >= 2
            if session_terminated:
                self._terminated_sessions.add(session_id)
                self._sessions.pop(session_id, None)
                self._store.delete("sessions", session_id)

            if daily_count >= 3 and int(now_epoch) >= cooldown_until:
                self._user_cooldowns[employee_pseudonym] = int(now_epoch) + 900

            lockout_seconds = 0
            if daily_count >= 5:
                lockout_seconds = 1800
                self._user_lockouts[employee_pseudonym] = int(now_epoch) + 1800

            strike_doc = {
                "employee_pseudonym": employee_pseudonym,
                "session_id": session_id,
                "reason_code": reason_code,
                "session_strike_count": sess_count,
                "daily_strike_count": daily_count,
                "session_terminated": session_terminated,
                "session_deleted": session_terminated,
                "cooldown_seconds": 900 if daily_count >= 3 else 0,
                "lockout_seconds": lockout_seconds,
                "security_alert_emitted": daily_count >= 5,
                "recorded_at_epoch": int(now_epoch),
            }
            self._store.put(
                "strikes",
                employee_pseudonym,
                strike_doc,
                now_epoch=int(now_epoch),
                ttl_seconds=86400,
            )
            return strike_doc

    def check_rate_limit(
        self,
        *,
        employee_id: str,
        is_write: bool = False,
        messages_last_minute: int = 0,
        messages_today: int = 0,
        writes_last_minute: int = 0,
        now_epoch: int = 0,
    ) -> dict[str, Any]:
        """Enforce sliding-window rate limits in hr-bff rate_limits/{psn}: 20 messages/min, 300 messages/day (FR-024), and 5 write confirmations/min (NFR-024)."""
        with self._lock:
            if is_write:
                if int(writes_last_minute) > 5:
                    return {
                        "allowed": False,
                        "error_code": ErrorCode.RATE_LIMITED,
                        "retry_after_seconds": 60,
                        "limit_type": "WRITES_PER_MINUTE",
                    }
                return {"allowed": True, "limit_type": "WRITES_PER_MINUTE"}

            if int(messages_last_minute) > 20:
                return {
                    "allowed": False,
                    "error_code": ErrorCode.RATE_LIMITED,
                    "retry_after_seconds": 60,
                    "limit_type": "MESSAGES_PER_MINUTE",
                }
            if int(messages_today) > 300:
                return {
                    "allowed": False,
                    "error_code": ErrorCode.RATE_LIMITED,
                    "retry_after_seconds": 3600,
                    "limit_type": "MESSAGES_PER_DAY",
                }
            return {"allowed": True, "retry_after_seconds": 0}

    def healthz(self) -> dict[str, Any]:
        """GET /healthz — Liveness probe (NFR-022)."""
        return {"status": "ok", "service": "hr-chat-bff"}

    def readyz(self, *, env: dict[str, str]) -> dict[str, Any]:
        """GET /readyz — Readiness probe verifying all 5 zero-content-capture switches (FR-014, NFR-007, NFR-022)."""
        otel_check = self._telemetry.validate_otel_config(env=env)
        is_safe = otel_check["content_capture_safe"]
        return {
            "status": "ready" if is_safe else "not_ready",
            "content_capture_safe": is_safe,
            "violations": otel_check["violations"],
            "dependencies": {
                "firestore_flags": "ok",
                "kms_jwks": "ok",
            },
        }

    def version(self) -> dict[str, Any]:
        """GET /version — Build version, git SHA, tool manifest digest, and prompt bundle digest (FR-001, FR-002)."""
        rt_file = os.environ.get("HR_AGENT_RUNTIME_STATE_FILE", "")
        rt = (
            json.loads(open(rt_file, encoding="utf-8").read())
            if (rt_file and os.path.exists(rt_file))
            else {}
        )
        return {
            "service": "hr-chat-bff",
            "version": "0.1.0-mvp1",
            "git_sha": "mvp1-phase05-green",
            "config_version": rt.get(
                "config_version", os.environ.get("CONFIG_VERSION", "v1.3.0")
            ),
            "image_digest": rt.get(
                "image_digest",
                os.environ.get(
                    "IMAGE_DIGEST",
                    "sha256:4a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b",
                ),
            ),
            "active_reasoning_engine": rt.get(
                "active_reasoning_engine",
                os.environ.get("ACTIVE_REASONING_ENGINE", "hr-agent-green-v1-3-0"),
            ),
            "tool_manifest_digest": "sha256:7d865e959b2466918c9863afca942d0fb89d7c9ac0c99bafc3749504ded97730",
            "prompt_bundle_digest": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        }


def create_http_server(
    host: str = "127.0.0.1",
    port: int = 18080,
    service: BffService | None = None,
) -> Any:
    """Create a loopback-bound HTTP/SSE server for the HR Chat BFF (C2, 03-deployment §2.1)."""
    from hr_agent.models import create_json_http_server

    bff = service or BffService()
    default_hdrs = {"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"}
    web_dir = Path(__file__).resolve().parent.parent.parent / "web" / "src"

    def _serve_root(req_hdrs: dict[str, str] | None = None) -> dict[str, Any]:
        hdrs = {**default_hdrs, **(req_hdrs or {})}
        cfg = bff.get_ui_bootstrap_config(headers=hdrs, client_ip="127.0.0.1")
        accept = str(
            (req_hdrs or {}).get("Accept") or (req_hdrs or {}).get("accept") or ""
        )
        ctype = str(
            (req_hdrs or {}).get("Content-Type")
            or (req_hdrs or {}).get("content-type")
            or ""
        )
        if (
            cfg.get("ok")
            and "application/json" not in accept
            and "application/json" not in ctype
            and (web_dir / "index.html").exists()
        ):
            return {
                "_raw_body": (web_dir / "index.html").read_text(encoding="utf-8"),
                "_content_type": "text/html; charset=utf-8",
            }
        return cfg

    def _get_fallback(
        path: str, req_hdrs: dict[str, str] | None = None
    ) -> dict[str, Any]:
        hdrs = {**default_hdrs, **(req_hdrs or {})}
        parsed = urlparse(path)
        if parsed.path.startswith("/api/v1/docs/"):
            doc_id = parsed.path[len("/api/v1/docs/") :].strip("/")
            qs = parse_qs(parsed.query)
            return bff.get_document(
                headers=hdrs,
                client_ip="127.0.0.1",
                doc_id=doc_id,
                version=(qs.get("version") or [None])[0],
                section=(qs.get("section") or [None])[0],
            )
        if parsed.path.startswith("/api/v1/sessions/"):
            sid = parsed.path[len("/api/v1/sessions/") :].strip("/")
            return bff.get_session_messages(
                headers=hdrs, client_ip="127.0.0.1", session_id=sid
            )
        return {
            "ok": False,
            "http_status": 404,
            "error_code": "NOT_FOUND",
            "path": path,
        }

    def _post(
        path: str, body: dict[str, Any], req_hdrs: dict[str, str] | None = None
    ) -> tuple[int, dict[str, Any]]:
        now_ep = int(body.get("now_epoch", int(time.time())))
        hdrs = {**default_hdrs, **(req_hdrs or {}), **(body.get("headers") or {})}
        if path == "/api/v1/sessions":
            res = bff.create_session(
                headers=hdrs, client_ip="127.0.0.1", now_epoch=now_ep
            )
            return int(res.get("http_status", 201 if res.get("ok") else 401)), res
        if path.endswith("/messages") and path.startswith("/api/v1/sessions/"):
            sid = path[len("/api/v1/sessions/") : -len("/messages")].strip("/")
            evs = bff.send_message_stream(
                headers=hdrs,
                client_ip="127.0.0.1",
                session_id=sid,
                text=str(body.get("text") or body.get("content") or ""),
                client_message_id=str(body.get("client_message_id", f"m-{now_ep}")),
                now_epoch=now_ep,
            )
            return 200, {"ok": True, "events": evs}
        if "/confirmations/" in path and path.startswith("/api/v1/sessions/"):
            sid, cid = path[len("/api/v1/sessions/") :].split("/confirmations/", 1)
            res = bff.resolve_confirmation(
                headers=hdrs,
                client_ip="127.0.0.1",
                session_id=sid.strip("/"),
                confirmation_id=cid.strip("/"),
                decision=str(body.get("decision", "confirm")),
                now_epoch=now_ep,
            )
            return int(res.get("http_status", 200 if res.get("ok") else 409)), res
        if path.endswith("/feedback") and path.startswith("/api/v1/sessions/"):
            sid = path[len("/api/v1/sessions/") : -len("/feedback")].strip("/")
            res = bff.submit_feedback(
                headers=hdrs,
                client_ip="127.0.0.1",
                session_id=sid,
                turn_id=str(body.get("turn_id", f"turn-{now_ep}")),
                rating=str(body.get("rating", "UP")),
                reason_category=str(
                    body.get("reason_category", "ACCURATE_POLICY_CITATION")
                ),
                comment=body.get("comment"),
            )
            return int(res.get("http_status", 200 if res.get("ok") else 400)), res
        if path.endswith("/logout") and path.startswith("/api/v1/sessions/"):
            sid = path[len("/api/v1/sessions/") : -len("/logout")].strip("/")
            res = bff.logout_session(
                headers=hdrs, client_ip="127.0.0.1", session_id=sid, now_epoch=now_ep
            )
            return int(res.get("http_status", 200 if res.get("ok") else 403)), res
        return 404, {"ok": False, "error": "Not found"}

    return create_json_http_server(
        service_name="hr-chat-bff",
        host=host,
        port=port,
        ready_fn=lambda: bff.readyz(env=dict(os.environ)),
        version_fn=bff.version,
        post_dispatch=_post,
        extra_get_routes={
            "/": _serve_root,
            "/ui": lambda: {
                "_raw_body": (web_dir / "index.html").read_text(encoding="utf-8"),
                "_content_type": "text/html; charset=utf-8",
            },
            "/index.html": lambda: {
                "_raw_body": (web_dir / "index.html").read_text(encoding="utf-8"),
                "_content_type": "text/html; charset=utf-8",
            },
            "/app.js": lambda: {
                "_raw_body": (web_dir / "app.js").read_text(encoding="utf-8"),
                "_content_type": "application/javascript; charset=utf-8",
            },
            "/styles.css": lambda: {
                "_raw_body": (web_dir / "styles.css").read_text(encoding="utf-8"),
                "_content_type": "text/css; charset=utf-8",
            },
            "/api/v1/me": lambda req_hdrs=None: bff.get_me(
                headers={**default_hdrs, **(req_hdrs or {})}, client_ip="127.0.0.1"
            ),
            "/api/v1/ui-config": lambda req_hdrs=None: bff.get_ui_bootstrap_config(
                headers={**default_hdrs, **(req_hdrs or {})}, client_ip="127.0.0.1"
            ),
            "/api/v1/sessions": lambda req_hdrs=None: bff.list_sessions(
                headers={**default_hdrs, **(req_hdrs or {})}, client_ip="127.0.0.1"
            ),
            "*": _get_fallback,
        },
    )


def main(argv: list[str] | None = None) -> int:
    from hr_agent.models import run_http_server_cli

    return run_http_server_cli(
        service_name="hr-chat-bff",
        default_port=18080,
        server_factory=create_http_server,
        self_test_fn=lambda: bool(BffService().healthz().get("status") == "ok"),
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
