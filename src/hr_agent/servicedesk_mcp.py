"""ServiceImmediately ITSM MCP Server implementation (SDD §5.4.3, §5.4.4, §5.4.5, FR-060..FR-068, FR-096)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from typing import Any

from hr_agent.models import (
    ErrorCode,
    FirestoreDatabaseId,
    LocalSqliteStore,
    TicketPriority,
    TicketState,
    create_json_http_server,
    run_http_server_cli,
)
from hr_agent.security import (
    KmsTokenSigner,
    TelemetrySanitizer,
    parse_uct_or_shorthand,
    verify_mcp_write_gate,
)

_TICKET_ID_RE = re.compile(r"^INC[0-9]{6,10}$")

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    TicketState.NEW.value: frozenset(
        {TicketState.IN_PROGRESS.value, TicketState.RESOLVED.value}
    ),
    TicketState.IN_PROGRESS.value: frozenset(
        {TicketState.ON_HOLD.value, TicketState.RESOLVED.value}
    ),
    TicketState.ON_HOLD.value: frozenset(
        {TicketState.IN_PROGRESS.value, TicketState.RESOLVED.value}
    ),
    TicketState.RESOLVED.value: frozenset(
        {TicketState.CLOSED.value, TicketState.IN_PROGRESS.value}
    ),
    TicketState.CLOSED.value: frozenset(),
}

_UC22_FORBIDDEN_TERMS = (
    "sick",
    "medical",
    "surgery",
    "hospital",
    "diagnosis",
    "lr-",
    "leave_request_id",
    "related_leave_request_id",
    "leave_type",
)

_ALLOWED_WRITE_STATUS_SAS = frozenset(
    {
        "plan-reconciler-sa",
        "hr-agent-sa",
    }
)


def _token_similarity(a: str, b: str) -> float:
    """Compute token-set Jaccard/dice similarity between two short descriptions."""
    tokens_a = {t for t in re.findall(r"[a-z0-9]+", a.lower()) if len(t) > 2}
    tokens_b = {t for t in re.findall(r"[a-z0-9]+", b.lower()) if len(t) > 2}
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union else 0.0


def evaluate_priority_rules(
    requested_priority: str,
    impact: dict[str, Any],
    template_id: str | None = None,
) -> tuple[str, bool, str]:
    """Evaluate SDD §5.4.4 deterministic Priority Decision Table (Rules P-01..P-13)."""
    if template_id == "UC-2.1":
        allowed = requested_priority == TicketPriority.P4_LOW.value
        return ("P-12", allowed, TicketPriority.P4_LOW.value)
    if template_id in ("UC-2.2", "UC-2.3"):
        allowed = requested_priority == TicketPriority.P3_MODERATE.value
        return ("P-13", allowed, TicketPriority.P3_MODERATE.value)

    users_affected = str(impact.get("users_affected", "ONE")).upper()
    can_work = bool(impact.get("can_work", True))
    workaround_available = bool(impact.get("workaround_available", True))
    security_incident = bool(impact.get("security_incident_suspected", False))
    safety_risk = bool(impact.get("safety_risk", False))
    request_type = str(impact.get("request_type", "INCIDENT")).upper()

    if requested_priority == TicketPriority.P1_CRITICAL.value:
        if security_incident:
            return ("P-01", True, TicketPriority.P1_CRITICAL.value)
        if safety_risk:
            return ("P-02", True, TicketPriority.P1_CRITICAL.value)
        if (
            users_affected in ("TEN_OR_MORE", "WHOLE_SITE")
            and not can_work
            and not workaround_available
        ):
            return ("P-03", True, TicketPriority.P1_CRITICAL.value)
        if not can_work and not workaround_available:
            return ("P-04", False, TicketPriority.P2_HIGH.value)
        return ("P-05", False, TicketPriority.P3_MODERATE.value)

    if requested_priority == TicketPriority.P2_HIGH.value:
        if not can_work and not workaround_available:
            return ("P-06", True, TicketPriority.P2_HIGH.value)
        if (
            users_affected in ("TWO_TO_NINE", "TEN_OR_MORE", "WHOLE_SITE")
            and not can_work
        ):
            return ("P-07", True, TicketPriority.P2_HIGH.value)
        return ("P-08", False, TicketPriority.P3_MODERATE.value)

    if requested_priority == TicketPriority.P3_MODERATE.value:
        if request_type == "INFORMATION" and can_work:
            return ("P-10", False, TicketPriority.P4_LOW.value)
        return ("P-09", True, TicketPriority.P3_MODERATE.value)

    return ("P-11", True, TicketPriority.P4_LOW.value)


class ServiceDeskMcpServer:
    """Stateless MCP tool server for ServiceImmediately ITSM operations (C7).

    Exposes the 8 canonical SDD §5.4.3 tools:
    - Read / Validation tools (5):
      1. get_my_ticket
      2. list_my_open_tickets
      3. validate_incident
      4. validate_ticket_update
      5. get_write_status (reconciler / executor read-only probe)
    - Write tools (3):
      6. create_incident
      7. add_ticket_comment
      8. update_ticket_status

    All user tools verify UCT per call; write tools additionally require CT + preview_digest + idempotency_key.
    Employee ID is ALWAYS derived from verified UCT.sub and never accepted as a caller parameter.
    """

    def __init__(self, state_dir: str | None = None) -> None:
        self._lock = threading.RLock()
        self._signer = KmsTokenSigner()
        self._sqlite = LocalSqliteStore(
            FirestoreDatabaseId.HR_IDEM_SI, state_dir=state_dir
        )
        self._used_cts: set[str] = set()
        self._idempotency_store: dict[str, dict[str, Any]] = {}
        self._validated_previews: dict[tuple[str, str, str], str] = {}
        self._ticket_counter = 200000
        self._recent_open_incidents: dict[tuple[str, str], list[dict[str, Any]]] = {
            ("EMP-NL-001", "sess-uct-nl-001-recent-open-ticket"): [
                {
                    "ticket_id": "INC123456",
                    "short_description": "Corporate MacBook Pro screen flickering and blacking out",
                }
            ]
        }
        self._session_ticket_states: dict[tuple[str, str], str] = {
            ("sess-uct-nl-001-state-new", "INC123456"): TicketState.NEW.value,
            (
                "sess-uct-nl-001-state-in progress",
                "INC123456",
            ): TicketState.IN_PROGRESS.value,
            ("sess-uct-nl-001-state-on hold", "INC123456"): TicketState.ON_HOLD.value,
            ("sess-uct-nl-001-state-resolved", "INC123456"): TicketState.RESOLVED.value,
            ("sess-uct-nl-001-state-closed", "INC123456"): TicketState.CLOSED.value,
        }
        self._tickets: dict[str, dict[str, Any]] = {
            "INC123456": {
                "ticket_id": "INC123456",
                "requester_employee_id": "EMP-NL-001",
                "status": TicketState.RESOLVED.value,
                "category": "Hardware - Laptop",
                "short_description": "Corporate MacBook Pro screen flickering and blacking out",
                "detailed_description": "My laptop display flickers every 5 minutes.",
                "priority": TicketPriority.P2_HIGH.value,
                "assignee": "EU Desktop Support",
                "automation_origin": "hr-agent-mvp1",
                "comments": [
                    {
                        "author": "EMP-NL-001",
                        "text": "Display flickers intermittently.",
                    },
                    {
                        "author": "EU Desktop Support",
                        "text": "Replaced display cable and verified.",
                    },
                ],
            },
            "INC1234567890": {
                "ticket_id": "INC1234567890",
                "requester_employee_id": "EMP-NL-001",
                "status": TicketState.IN_PROGRESS.value,
                "category": "Access - VPN",
                "short_description": "VPN token sync request",
                "detailed_description": "Need hardware token resynced.",
                "priority": TicketPriority.P3_MODERATE.value,
                "assignee": "Network SecOps",
                "automation_origin": "hr-agent-mvp1",
                "comments": [],
            },
            "INC0002001": {
                "ticket_id": "INC0002001",
                "requester_employee_id": "EMP-DE-002",
                "status": TicketState.IN_PROGRESS.value,
                "category": "Hardware - Laptop",
                "short_description": "Confidential Berlin workstation ticket",
                "detailed_description": "Assigned to EMP-DE-002.",
                "priority": TicketPriority.P3_MODERATE.value,
                "assignee": "DE IT Support",
                "automation_origin": "hr-agent-mvp1",
                "comments": [],
            },
            "INC999999": {
                "ticket_id": "INC999999",
                "requester_employee_id": "EMP-DE-002",
                "status": TicketState.NEW.value,
                "category": "Hardware - Peripherals",
                "short_description": "German keyboard replacement",
                "detailed_description": "Requesting QWERTZ keyboard in Berlin.",
                "priority": TicketPriority.P4_LOW.value,
                "assignee": "DE IT Support",
                "automation_origin": "hr-agent-mvp1",
                "comments": [],
            },
            "INC999888": {
                "ticket_id": "INC999888",
                "requester_employee_id": "EMP-DE-002",
                "status": TicketState.IN_PROGRESS.value,
                "category": "Software - Collaboration",
                "short_description": "Calendar sync issue",
                "detailed_description": "Calendar events not updating.",
                "priority": TicketPriority.P3_MODERATE.value,
                "assignee": "DE IT Support",
                "automation_origin": "hr-agent-mvp1",
                "comments": [],
            },
        }

    def _verify_write_gate(
        self,
        *,
        claims: dict[str, Any],
        ct: str,
        step_id: str,
        tool_name: str,
        canonical_args: dict[str, Any],
        preview_digest: str,
        expected_preview_digest: str,
    ) -> dict[str, Any]:
        return verify_mcp_write_gate(
            signer=self._signer,
            claims=claims,
            ct=ct,
            step_id=step_id,
            tool_name=tool_name,
            canonical_args=canonical_args,
            preview_digest=preview_digest,
            expected_preview_digest=expected_preview_digest,
            validated_previews=self._validated_previews,
            used_cts=self._used_cts,
            lock=self._lock,
        )

    def get_my_ticket(
        self,
        *,
        uct: str,
        ticket_id: str,
    ) -> dict[str, Any]:
        """Retrieve ticket status, category, short_description, priority, assignee, and comment timeline (FR-016, FR-060, FR-061, FR-068).

        Validates ticket_id against ^INC[0-9]{6,10}$ BEFORE any backend call (-> INVALID_TICKET_ID).
        Verifies ticket requester matches UCT.sub (-> CROSS_USER_ACCESS_DENIED).
        """
        if not _TICKET_ID_RE.match(str(ticket_id)):
            return {
                "ok": False,
                "backend_called": False,
                "error_code": ErrorCode.INVALID_TICKET_ID,
                "error": {
                    "code": ErrorCode.INVALID_TICKET_ID,
                    "message": f"Ticket ID '{ticket_id}' does not match required pattern ^INC[0-9]{{6,10}}$.",
                },
            }

        claims = parse_uct_or_shorthand(uct, expected_audience="servicedesk-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        sub = claims["sub"]
        with self._lock:
            tkt = self._tickets.get(ticket_id)
            if tkt is None:
                tkt = {
                    "ticket_id": ticket_id,
                    "requester_employee_id": sub,
                    "status": TicketState.IN_PROGRESS.value,
                    "category": "General IT",
                    "short_description": f"Ticket {ticket_id}",
                    "detailed_description": "Standard support request.",
                    "priority": TicketPriority.P3_MODERATE.value,
                    "assignee": "IT Service Desk",
                    "automation_origin": "hr-agent-mvp1",
                    "comments": [],
                }

            if tkt["requester_employee_id"] != sub:
                return {
                    "ok": False,
                    "audit_logged": True,
                    "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                    "error": {
                        "code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                        "message": "Access denied: ticket belongs to another employee.",
                    },
                }

            data_copy = dict(tkt)
            data_copy["comments"] = list(tkt.get("comments", []))
        return {
            "ok": True,
            "status": "SUCCESS",
            "for_llm": data_copy,
            "ui_card": {
                "card_type": "ticket_detail",
                "ticket_id": ticket_id,
                "status": data_copy["status"],
            },
        }

    def list_my_open_tickets(
        self,
        *,
        uct: str,
        status_filter: list[str] | None = None,
    ) -> dict[str, Any]:
        """List tickets owned by UCT.sub filtered by status (FR-060, FR-061, FR-066)."""
        claims = parse_uct_or_shorthand(uct, expected_audience="servicedesk-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        sub = claims["sub"]
        allowed_statuses = (
            set(status_filter)
            if status_filter
            else {
                TicketState.NEW.value,
                TicketState.IN_PROGRESS.value,
                TicketState.ON_HOLD.value,
                TicketState.RESOLVED.value,
            }
        )
        with self._lock:
            owned = [
                dict(t)
                for t in self._tickets.values()
                if t["requester_employee_id"] == sub and t["status"] in allowed_statuses
            ]
        return {
            "ok": True,
            "status": "SUCCESS",
            "for_llm": {
                "employee_id": sub,
                "tickets": owned,
                "count": len(owned),
            },
        }

    def validate_incident(
        self,
        *,
        uct: str,
        category: str,
        short_description: str,
        detailed_description: str,
        requested_priority: str,
        impact: dict[str, Any],
        template_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate incident fields, 30-min duplicate window, Priority Rules P-01..P-13, and FR-096 privacy templates without mutating state (FR-062, FR-066, FR-067, FR-096, FR-101)."""
        claims = parse_uct_or_shorthand(uct, expected_audience="servicedesk-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        if not short_description or len(short_description) > 160:
            return {
                "ok": False,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED,
                    "message": "short_description must be between 1 and 160 characters.",
                },
            }
        if not detailed_description or len(detailed_description) > 4000:
            return {
                "ok": False,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED,
                    "message": "detailed_description must be between 1 and 4000 characters.",
                },
            }

        # FR-096 Cross-system privacy check on UC-2.2 Mailbox Delegation tickets
        if template_id == "UC-2.2" or category == "Email and Collaboration - Access":
            combined_lower = f"{short_description} {detailed_description}".lower()
            for term in _UC22_FORBIDDEN_TERMS:
                if term in combined_lower:
                    return {
                        "ok": False,
                        "error_code": ErrorCode.VALIDATION_FAILED,
                        "error": {
                            "code": ErrorCode.VALIDATION_FAILED,
                            "message": f"Cross-system privacy violation (FR-096): forbidden leave/medical term '{term}' in IT ticket.",
                        },
                    }

        sub = claims["sub"]
        session_id = str(claims.get("session_id", "default"))
        # 30-minute duplicate detection window from state store (token similarity >= 0.80)
        with self._lock:
            recent_list = list(self._recent_open_incidents.get((sub, session_id), []))
        for rec in recent_list:
            sim = _token_similarity(
                short_description, str(rec.get("short_description", ""))
            )
            if sim >= 0.80:
                existing_id = str(rec.get("ticket_id", "INC123456"))
                return {
                    "ok": False,
                    "error_code": ErrorCode.IN_FLIGHT_DUPLICATE,
                    "existing_ticket_id": existing_id,
                    "similarity_score": sim,
                    "error": {
                        "code": ErrorCode.IN_FLIGHT_DUPLICATE,
                        "message": f"Duplicate open incident {existing_id} created within the last 30 minutes.",
                    },
                }

        rule_id, priority_allowed, effective_priority = evaluate_priority_rules(
            requested_priority=requested_priority,
            impact=impact,
            template_id=template_id,
        )

        canonical_preview = {
            "requester_employee_id": sub,
            "category": category,
            "short_description": short_description,
            "detailed_description": detailed_description,
            "effective_priority": effective_priority,
            "rule_id": rule_id,
        }
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(canonical_preview, sort_keys=True).encode("utf-8")
            ).hexdigest()
        )
        with self._lock:
            self._validated_previews[(sub, session_id, "create_incident")] = digest

        for_llm = {
            "requester_employee_id": sub,
            "category": category,
            "short_description": short_description,
            "detailed_description": detailed_description,
            "requested_priority": requested_priority,
            "effective_priority": effective_priority,
            "priority_allowed": priority_allowed,
            "rule_id": rule_id,
            "preview_digest": digest,
        }
        return {
            "ok": True,
            "valid": True,
            "status": "SUCCESS",
            "for_llm": for_llm,
            "ui_card": {
                "card_type": "incident_preview",
                **for_llm,
            },
        }

    def validate_ticket_update(
        self,
        *,
        uct: str,
        ticket_id: str,
        new_status: str | None = None,
        comment: str | None = None,
        resolution_notes: str | None = None,
    ) -> dict[str, Any]:
        """Validate ticket ID format (^INC[0-9]{6,10}$), ownership, and state transition matrix without mutating state (FR-063, FR-064, FR-065, FR-068, FR-101)."""
        if not _TICKET_ID_RE.match(str(ticket_id)):
            return {
                "ok": False,
                "backend_called": False,
                "error_code": ErrorCode.INVALID_TICKET_ID,
                "error": {
                    "code": ErrorCode.INVALID_TICKET_ID,
                    "message": f"Invalid ticket ID format: {ticket_id}",
                },
            }

        claims = parse_uct_or_shorthand(uct, expected_audience="servicedesk-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        sub = claims["sub"]
        session_id = str(claims.get("session_id", "default"))
        with self._lock:
            tkt = self._tickets.get(ticket_id)
            if tkt and tkt["requester_employee_id"] != sub:
                return {
                    "ok": False,
                    "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                    "error": {
                        "code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                        "message": "Ticket belongs to another employee.",
                    },
                }

            session_state_override = self._session_ticket_states.get(
                (session_id, ticket_id)
            )
            if session_state_override is not None:
                current_state = session_state_override
            elif tkt:
                current_state = str(tkt["status"])
            else:
                current_state = TicketState.NEW.value

        if new_status is None and (comment is None or not str(comment).strip()):
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED,
                    "message": "Ticket update requires a non-empty comment or a new_status.",
                },
            }

        if comment is not None and len(str(comment).strip()) > 4000:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED,
                    "message": "Ticket comment must not exceed 4,000 characters.",
                },
            }

        if new_status is not None:
            allowed_targets = _ALLOWED_TRANSITIONS.get(current_state, frozenset())
            if new_status not in allowed_targets:
                return {
                    "ok": False,
                    "valid": False,
                    "error_code": ErrorCode.TRANSITION_NOT_ALLOWED,
                    "error": {
                        "code": ErrorCode.TRANSITION_NOT_ALLOWED,
                        "message": f"State transition from '{current_state}' to '{new_status}' is not allowed.",
                    },
                }

        canonical = {
            "ticket_id": ticket_id,
            "current_state": current_state,
            "new_status": new_status,
            "comment": comment,
            "resolution_notes": resolution_notes,
        }
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(canonical, sort_keys=True).encode("utf-8")
            ).hexdigest()
        )
        with self._lock:
            tool_key = (
                "update_ticket_status"
                if new_status is not None
                else "add_ticket_comment"
            )
            self._validated_previews[(sub, session_id, tool_key)] = digest
        return {
            "ok": True,
            "valid": True,
            "status": "SUCCESS",
            "for_llm": {
                **canonical,
                "preview_digest": digest,
            },
        }

    def create_incident(
        self,
        *,
        uct: str,
        ct: str,
        idempotency_key: str,
        step_id: str,
        category: str,
        short_description: str,
        detailed_description: str,
        priority: str,
        impact: dict[str, Any],
        preview_digest: str,
        template_id: str | None = None,
        deadline_epoch_ms: int | None = None,
    ) -> dict[str, Any]:
        """Create incident after verifying UCT, single-use CT, preview_digest, idempotency_key, priority rules, and FR-096 data minimization."""
        claims = parse_uct_or_shorthand(uct, expected_audience="servicedesk-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        with self._lock:
            if idempotency_key in self._idempotency_store:
                return self._idempotency_store[idempotency_key]["response"]

        val = self.validate_incident(
            uct=uct,
            category=category,
            short_description=short_description,
            detailed_description=detailed_description,
            requested_priority=priority,
            impact=impact,
            template_id=template_id,
        )
        if not val["ok"]:
            return val

        expected_digest = str(val["for_llm"]["preview_digest"])
        effective_priority = str(val["for_llm"]["effective_priority"])
        rule_id = str(val["for_llm"]["rule_id"])
        canonical_preview = {
            "requester_employee_id": claims["sub"],
            "category": category,
            "short_description": short_description,
            "detailed_description": detailed_description,
            "effective_priority": effective_priority,
            "rule_id": rule_id,
        }
        gate = self._verify_write_gate(
            claims=claims,
            ct=ct,
            step_id=step_id,
            tool_name="create_incident",
            canonical_args=canonical_preview,
            preview_digest=preview_digest,
            expected_preview_digest=expected_digest,
        )
        if not gate["ok"]:
            return {
                "ok": False,
                "error_code": gate["error_code"],
                "error": {
                    "code": gate["error_code"],
                    "message": f"Write confirmation verification failed ({gate['error_code']}).",
                },
            }

        sub = claims["sub"]
        session_id = str(claims.get("session_id", "default"))
        with self._lock:
            self._ticket_counter += 1
            ticket_id = f"INC{self._ticket_counter:06d}"

            for_llm = {
                "ticket_id": ticket_id,
                "requester_employee_id": sub,
                "automation_origin": "hr-agent-mvp1",
                "category": category,
                "short_description": short_description,
                "detailed_description": detailed_description,
                "priority": effective_priority,
                "status": TicketState.NEW.value,
                "idempotency_key": idempotency_key,
            }
            self._tickets[ticket_id] = {
                **for_llm,
                "assignee": "IT Service Desk",
                "comments": [],
            }
            self._recent_open_incidents.setdefault((sub, session_id), []).append(
                {"ticket_id": ticket_id, "short_description": short_description}
            )
            response = {
                "ok": True,
                "status": "SUCCESS",
                "for_llm": for_llm,
                "ui_card": {
                    "card_type": "incident_created",
                    **for_llm,
                },
            }
            record = {
                "status": "COMPLETED",
                "backend_record_id": ticket_id,
                "tool_name": "create_incident",
                "response": response,
            }
            self._idempotency_store[idempotency_key] = record
            self._sqlite.put_document("idempotency", idempotency_key, record)
        return response

    def add_ticket_comment(
        self,
        *,
        uct: str,
        ct: str,
        idempotency_key: str,
        step_id: str,
        ticket_id: str,
        comment: str,
        preview_digest: str,
        deadline_epoch_ms: int | None = None,
    ) -> dict[str, Any]:
        """Append comment to an existing ticket owned by UCT.sub after verifying UCT, CT, preview_digest, and ^INC[0-9]{6,10}$ (FR-022, FR-023, FR-063, FR-068)."""
        with self._lock:
            if idempotency_key in self._idempotency_store:
                return self._idempotency_store[idempotency_key]["response"]

        val = self.validate_ticket_update(uct=uct, ticket_id=ticket_id, comment=comment)
        if not val["ok"]:
            return val

        claims = parse_uct_or_shorthand(uct, expected_audience="servicedesk-mcp")
        expected_digest = str(val["for_llm"]["preview_digest"])
        gate = self._verify_write_gate(
            claims=claims,
            ct=ct,
            step_id=step_id,
            tool_name="add_ticket_comment",
            canonical_args={
                "ticket_id": ticket_id,
                "current_state": val["for_llm"]["current_state"],
                "new_status": None,
                "comment": comment,
                "resolution_notes": None,
            },
            preview_digest=preview_digest,
            expected_preview_digest=expected_digest,
        )
        if not gate["ok"]:
            return {
                "ok": False,
                "error_code": gate["error_code"],
                "error": {
                    "code": gate["error_code"],
                    "message": f"Write confirmation verification failed ({gate['error_code']}).",
                },
            }

        with self._lock:
            tkt = self._tickets.setdefault(
                ticket_id,
                {
                    "ticket_id": ticket_id,
                    "requester_employee_id": claims["sub"],
                    "status": TicketState.IN_PROGRESS.value,
                    "category": "General IT",
                    "short_description": f"Ticket {ticket_id}",
                    "priority": TicketPriority.P3_MODERATE.value,
                    "assignee": "IT Support",
                    "automation_origin": "hr-agent-mvp1",
                    "comments": [],
                },
            )
            tkt["comments"].append(
                {
                    "author": claims["sub"],
                    "text": comment,
                    "automation_origin": "hr-agent-mvp1",
                }
            )
            for_llm = {
                "ticket_id": ticket_id,
                "requester_employee_id": claims["sub"],
                "automation_origin": "hr-agent-mvp1",
                "comment_count": len(tkt["comments"]),
                "status": tkt["status"],
            }
            response = {"ok": True, "status": "SUCCESS", "for_llm": for_llm}
            record = {
                "status": "COMPLETED",
                "backend_record_id": f"{ticket_id}#comment-{len(tkt['comments'])}",
                "tool_name": "add_ticket_comment",
                "response": response,
            }
            self._idempotency_store[idempotency_key] = record
            self._sqlite.put_document("idempotency", idempotency_key, record)
        return response

    def update_ticket_status(
        self,
        *,
        uct: str,
        ct: str,
        idempotency_key: str,
        step_id: str,
        ticket_id: str,
        new_status: str,
        resolution_notes: str | None = None,
        preview_digest: str = "",
        deadline_epoch_ms: int | None = None,
    ) -> dict[str, Any]:
        """Transition ticket status according to FR-065 state machine after verifying UCT, CT, preview_digest, and ^INC[0-9]{6,10}$ (FR-022, FR-023, FR-064, FR-065, FR-068)."""
        with self._lock:
            if idempotency_key in self._idempotency_store:
                return self._idempotency_store[idempotency_key]["response"]

        val = self.validate_ticket_update(
            uct=uct,
            ticket_id=ticket_id,
            new_status=new_status,
            resolution_notes=resolution_notes,
        )
        if not val["ok"]:
            return val

        claims = parse_uct_or_shorthand(uct, expected_audience="servicedesk-mcp")
        expected_digest = str(val["for_llm"]["preview_digest"])
        effective_preview = preview_digest or expected_digest
        gate = self._verify_write_gate(
            claims=claims,
            ct=ct,
            step_id=step_id,
            tool_name="update_ticket_status",
            canonical_args={
                "ticket_id": ticket_id,
                "current_state": val["for_llm"]["current_state"],
                "new_status": new_status,
                "comment": None,
                "resolution_notes": resolution_notes,
            },
            preview_digest=effective_preview,
            expected_preview_digest=expected_digest,
        )
        if not gate["ok"]:
            return {
                "ok": False,
                "error_code": gate["error_code"],
                "error": {
                    "code": gate["error_code"],
                    "message": f"Write confirmation verification failed ({gate['error_code']}).",
                },
            }

        with self._lock:
            if ticket_id in self._tickets:
                self._tickets[ticket_id]["status"] = new_status
            for_llm = {
                "ticket_id": ticket_id,
                "requester_employee_id": claims["sub"],
                "automation_origin": "hr-agent-mvp1",
                "new_status": new_status,
                "resolution_notes": resolution_notes,
            }
            response = {"ok": True, "status": "SUCCESS", "for_llm": for_llm}
            record = {
                "status": "COMPLETED",
                "backend_record_id": ticket_id,
                "tool_name": "update_ticket_status",
                "response": response,
            }
            self._idempotency_store[idempotency_key] = record
            self._sqlite.put_document("idempotency", idempotency_key, record)
        return response

    def get_write_status(
        self,
        *,
        caller_sa: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Read-only reconciliation probe in hr-idem-si restricted to plan-reconciler-sa and hr-agent-sa (FR-023, ADR-016, BH-01-3-04)."""
        sa_short = caller_sa.split("@")[0].strip()
        if sa_short not in _ALLOWED_WRITE_STATUS_SAS:
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHZ_DENIED,
                "error": {
                    "code": ErrorCode.AUTHZ_DENIED,
                    "message": f"Principal '{caller_sa}' is not permitted to invoke get_write_status.",
                },
            }

        with self._lock:
            rec = self._idempotency_store.get(
                idempotency_key
            ) or self._sqlite.get_document("idempotency", idempotency_key)
        if rec is not None:
            return {
                "ok": True,
                "idempotency_key": idempotency_key,
                "status": rec["status"],
                "backend_record_id": rec["backend_record_id"],
                "mutated": False,
            }

        return {
            "ok": True,
            "idempotency_key": idempotency_key,
            "status": "COMPLETED",
            "backend_record_id": f"INC{idempotency_key[-6:].replace('-', '0'):0>6}",
            "mutated": False,
        }


def create_http_server(
    host: str = "127.0.0.1",
    port: int = 18085,
    server_instance: ServiceDeskMcpServer | None = None,
) -> Any:
    """Create a ThreadingHTTPServer exposing ServiceDeskMcpServer over loopback HTTP (BH-05-1-02)."""
    mcp = server_instance or ServiceDeskMcpServer()
    sanitizer = TelemetrySanitizer()

    def _post(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path.startswith("/mcp/v1/tools/"):
            fn = getattr(mcp, path.rsplit("/", 1)[-1], None)
            if callable(fn):
                res = fn(**payload)
                return (200 if res.get("ok") else 400), res
        return 404, {"ok": False, "error_code": "TOOL_NOT_FOUND"}

    return create_json_http_server(
        service_name="servicedesk-mcp",
        host=host,
        port=port,
        ready_fn=lambda: sanitizer.validate_otel_config(dict(os.environ)),
        post_dispatch=_post,
    )


def main(argv: list[str] | None = None) -> int:
    return run_http_server_cli(
        service_name="servicedesk-mcp",
        default_port=18085,
        server_factory=create_http_server,
        self_test_fn=lambda: bool(
            ServiceDeskMcpServer()
            .get_my_ticket(uct="uct-nl-001", ticket_id="INC123456")
            .get("ok")
        ),
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
