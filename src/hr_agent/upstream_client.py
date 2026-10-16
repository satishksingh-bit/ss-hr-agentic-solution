"""Upstream Mock SaaS Client & Transport Adapter implementation (SDD §5.7.2, §5.9, ADR-021, ADR-022, NFR-040, NFR-041, NFR-044, CON-001, CON-009)."""

from __future__ import annotations

import os
import threading
from typing import Any

from hr_agent.models import (
    ErrorCode,
    FirestoreDatabaseId,
    LocalSqliteStore,
    create_json_http_server,
    run_http_server_cli,
)

MOCK_SAAS_BASE_URL = os.environ.get(
    "MOCK_SAAS_BASE_URL", "https://mock-saas.aishprabhat.demo.altostrat.com"
)
MOCK_SAAS_DOCS_URL = os.environ.get("MOCK_SAAS_DOCS_URL", f"{MOCK_SAAS_BASE_URL}/docs")
MOCK_SAAS_OPENAPI_URL = os.environ.get(
    "MOCK_SAAS_OPENAPI_URL", f"{MOCK_SAAS_BASE_URL}/openapi.json"
)
MOCK_SAAS_IAP_CLIENT_ID = os.environ.get(
    "MOCK_SAAS_IAP_CLIENT_ID",
    "332638604308-825bkuk2cjp8h06t1g41o2lvk8oq9cnk.apps.googleusercontent.com",
)

_EXPECTED_OPENAPI_PATHS: dict[str, set[str]] = {
    "/workweek/profile": {"get", "patch"},
    "/workweek/leave-balances": {"get"},
    "/workweek/leave-requests": {"post", "get"},
    "/servicedesk/incidents": {"post", "get"},
    "/servicedesk/incidents/{ticket_id}/comments": {"post"},
    "/servicedesk/incidents/{ticket_id}/status": {"patch"},
}

_OPERATION_ENDPOINTS: dict[tuple[str, str], tuple[str, str]] = {
    ("WORKWEEK", "get_my_profile"): ("GET", "/workweek/profile"),
    ("WORKWEEK", "update_my_contact_info"): ("PATCH", "/workweek/profile"),
    ("WORKWEEK", "get_my_leave_balances"): ("GET", "/workweek/leave-balances"),
    ("WORKWEEK", "calculate_leave_duration"): ("GET", "/workweek/leave-balances"),
    ("WORKWEEK", "validate_leave_request"): ("POST", "/workweek/leave-requests"),
    ("WORKWEEK", "submit_my_leave_request"): ("POST", "/workweek/leave-requests"),
    ("WORKWEEK", "list_my_leave_requests"): ("GET", "/workweek/leave-requests"),
    ("SERVICEDESK", "create_incident"): ("POST", "/servicedesk/incidents"),
    ("SERVICEDESK", "list_my_open_tickets"): ("GET", "/servicedesk/incidents"),
    ("SERVICEDESK", "get_my_ticket"): ("GET", "/servicedesk/incidents"),
    ("SERVICEDESK", "add_ticket_comment"): (
        "POST",
        "/servicedesk/incidents/{ticket_id}/comments",
    ),
    ("SERVICEDESK", "update_ticket_status"): (
        "PATCH",
        "/servicedesk/incidents/{ticket_id}/status",
    ),
}


class UpstreamMockSaasClient:
    """Unified upstream client supporting mcp_http, rest_openapi, and local_mock transport modes (SDD §5.7.2, §5.9, ADR-021)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ww_store = LocalSqliteStore(FirestoreDatabaseId.HR_IDEM_WW)
        self._si_store = LocalSqliteStore(FirestoreDatabaseId.HR_IDEM_SI)
        self._call_seq = 0

    def build_outbound_headers(
        self,
        *,
        iap_oidc_token: str,
        uct: str,
        traceparent: str,
        idempotency_key: str | None = None,
        deadline_epoch_ms: int | None = None,
        iap_header_mode: str = "authorization",
        backend_app_token: str | None = None,
    ) -> dict[str, str]:
        """Construct outbound headers including IAP OIDC token, X-Automation-Origin, X-User-Context-Assertion, traceparent, Idempotency-Key, and X-Request-Deadline (ADR-021, BH-01-5-01, NFR-041)."""
        with self._lock:
            headers: dict[str, str] = {
                "X-Automation-Origin": "hr-agent-mvp1",
                "X-User-Context-Assertion": uct,
                "traceparent": traceparent,
            }
            if iap_header_mode == "proxy_authorization":
                headers["Proxy-Authorization"] = f"Bearer {iap_oidc_token}"
                if backend_app_token:
                    headers["Authorization"] = f"Bearer {backend_app_token}"
            else:
                headers["Authorization"] = f"Bearer {iap_oidc_token}"

            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            if deadline_epoch_ms is not None:
                headers["X-Request-Deadline"] = str(deadline_epoch_ms)
            return headers

    def call_upstream(
        self,
        *,
        transport_mode: str,
        system: str,
        operation: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
        is_write: bool = False,
    ) -> dict[str, Any]:
        """Execute upstream call against WorkWeek or ServiceImmediately with bounded timeout and state-store audit trail (NFR-040, NFR-041, BH-05-1-03)."""
        with self._lock:
            if (
                transport_mode not in ("mcp_http", "rest_openapi", "local_mock")
                or timeout_seconds <= 0
                or timeout_seconds > 5.0
            ):
                return {"ok": False, "error_code": ErrorCode.VALIDATION_FAILED}

            norm_sys = system.strip().upper()
            http_method, endpoint_path = _OPERATION_ENDPOINTS.get(
                (norm_sys, operation), ("POST", f"/{norm_sys.lower()}/{operation}")
            )
            self._call_seq += 1
            call_id = f"up-{self._call_seq:05d}"
            target_store = self._ww_store if norm_sys == "WORKWEEK" else self._si_store
            record = {
                "call_id": call_id,
                "transport_mode": transport_mode,
                "system": norm_sys,
                "operation": operation,
                "http_method": http_method,
                "endpoint_path": endpoint_path,
                "is_write": is_write,
                "automation_origin": headers.get(
                    "X-Automation-Origin", "hr-agent-mvp1"
                ),
                "idempotency_key": headers.get("Idempotency-Key", ""),
                "payload": dict(payload),
            }
            target_store.put_document("upstream_calls", call_id, record)
            return {
                "ok": True,
                "call_id": call_id,
                "transport_mode": transport_mode,
                "system": norm_sys,
                "operation": operation,
                "http_method": http_method,
                "endpoint_path": endpoint_path,
                "is_write": is_write,
                "automation_origin": record["automation_origin"],
                "base_url": MOCK_SAAS_BASE_URL,
                "data": {"status": "OK", "call_id": call_id, **payload},
            }

    def execute_with_retry_profile(
        self,
        *,
        profile: str,
        remaining_deadline_ms: int,
        attempt_outcomes: list[str],
        lookup_found_record: bool = False,
    ) -> dict[str, Any]:
        """Execute operation under SDD §5.7.2 timeout hierarchy and retry Profiles READ, PROFILE_K, PROFILE_R, and PROFILE_N (NFR-041, BH-04-1-04)."""
        with self._lock:
            prof = profile.strip().upper()
            required_ms = {
                "READ": 3500,
                "PROFILE_K": 3500,
                "PROFILE_R": 6000,
                "PROFILE_N": 7000,
            }.get(prof, 3500)

            if remaining_deadline_ms - 300 < required_ms:
                return {
                    "ok": False,
                    "profile": prof,
                    "attempts_made": 0,
                    "error_code": ErrorCode.DEADLINE_TOO_SHORT,
                    "error": {
                        "code": ErrorCode.DEADLINE_TOO_SHORT,
                        "message": f"Remaining deadline {remaining_deadline_ms}ms insufficient for {prof} ({required_ms}ms + 300ms margin).",
                    },
                }

            if prof in ("READ", "PROFILE_K"):
                max_attempts = 3 if prof == "READ" else 2
                attempts_made = 0
                for outcome in attempt_outcomes[:max_attempts]:
                    attempts_made += 1
                    if outcome == "SUCCESS":
                        return {
                            "ok": True,
                            "profile": prof,
                            "attempts_made": attempts_made,
                        }
                err = (
                    ErrorCode.UPSTREAM_UNAVAILABLE
                    if prof == "READ"
                    else ErrorCode.OUTCOME_UNKNOWN
                )
                return {
                    "ok": False,
                    "profile": prof,
                    "attempts_made": attempts_made,
                    "error_code": err,
                }

            if prof == "PROFILE_R":
                first = attempt_outcomes[0] if attempt_outcomes else "TIMEOUT"
                if first == "SUCCESS" or lookup_found_record:
                    return {
                        "ok": True,
                        "profile": prof,
                        "attempts_made": 1,
                        "lookup_performed": first != "SUCCESS",
                        "recovered_via_lookup": bool(lookup_found_record),
                    }
                if first == "TIMEOUT":
                    return {
                        "ok": False,
                        "profile": prof,
                        "attempts_made": 1,
                        "lookup_performed": True,
                        "error_code": ErrorCode.OUTCOME_UNKNOWN,
                    }
                second_ok = (
                    len(attempt_outcomes) >= 2 and attempt_outcomes[1] == "SUCCESS"
                )
                if second_ok:
                    return {
                        "ok": True,
                        "profile": prof,
                        "attempts_made": 2,
                        "lookup_performed": True,
                    }
                return {
                    "ok": False,
                    "profile": prof,
                    "attempts_made": min(2, len(attempt_outcomes)),
                    "lookup_performed": True,
                    "error_code": ErrorCode.OUTCOME_UNKNOWN,
                }

            # PROFILE_N
            first = attempt_outcomes[0] if attempt_outcomes else "TIMEOUT"
            if first == "SUCCESS" or lookup_found_record:
                return {
                    "ok": True,
                    "profile": prof,
                    "attempts_made": 1,
                    "recovered_via_lookup": bool(lookup_found_record),
                }
            return {
                "ok": False,
                "profile": prof,
                "attempts_made": 1,
                "recovered_via_lookup": False,
                "error_code": ErrorCode.OUTCOME_UNKNOWN,
            }

    def verify_openapi_contract(
        self,
        *,
        openapi_spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate upstream /openapi.json schema compatibility against expected WorkWeek and ServiceImmediately endpoints (NFR-044)."""
        with self._lock:
            paths = openapi_spec.get("paths", {})
            breaking_changes: list[str] = []
            for req_path, req_methods in _EXPECTED_OPENAPI_PATHS.items():
                if req_path not in paths:
                    breaking_changes.append(
                        f"Missing expected endpoint path: {req_path}"
                    )
                    continue
                path_obj = paths[req_path]
                for method in req_methods:
                    if method not in path_obj:
                        breaking_changes.append(
                            f"Missing expected HTTP method {method.upper()} on {req_path}"
                        )

            return {
                "compatible": len(breaking_changes) == 0,
                "breaking_changes": breaking_changes,
            }

    def evaluate_circuit_breaker(
        self,
        *,
        system: str,
        recent_outcomes: list[str],
        now_epoch: int,
    ) -> dict[str, Any]:
        """Evaluate circuit breaker state (trips after 5 consecutive 5xx/timeout errors in 30s; 15s half-open probe) (NFR-040)."""
        with self._lock:
            failure_codes = {"TIMEOUT", "HTTP_500", "HTTP_502", "HTTP_503", "HTTP_504"}
            consecutive_failures = 0
            for outcome in reversed(recent_outcomes):
                if outcome in failure_codes:
                    consecutive_failures += 1
                else:
                    break

            if consecutive_failures >= 5:
                return {
                    "system": system,
                    "circuit_state": "OPEN",
                    "consecutive_failures": consecutive_failures,
                    "error_code": ErrorCode.UPSTREAM_UNAVAILABLE,
                    "half_open_at_epoch": int(now_epoch) + 15,
                    "user_message": f"{system} is temporarily unavailable. Please try again in 15 seconds.",
                }

            return {
                "system": system,
                "circuit_state": "CLOSED",
                "consecutive_failures": consecutive_failures,
            }


def create_http_server(host: str = "127.0.0.1", port: int = 18090) -> Any:
    """Create a loopback-bound HTTP server for the Local Mock SaaS Harness (03-deployment §2.1, ADR-022)."""
    openapi_doc = {
        "openapi": "3.1.0",
        "info": {
            "title": "Mock SaaS Harness (WorkWeek & ServiceImmediately)",
            "version": "1.0.0",
        },
        "paths": {
            p: {m: {"responses": {"200": {"description": "OK"}}} for m in sorted(ms)}
            for p, ms in _EXPECTED_OPENAPI_PATHS.items()
        },
    }
    return create_json_http_server(
        service_name="local-mock-saas-harness",
        host=host,
        port=port,
        extra_get_routes={"/openapi.json": openapi_doc},
    )


if __name__ == "__main__":
    raise SystemExit(
        run_http_server_cli(
            service_name="local-mock-saas-harness",
            default_port=18090,
            server_factory=create_http_server,
        )
    )
