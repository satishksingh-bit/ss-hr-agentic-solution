"""Domain models, enumerations, token claims, error codes, and thread-safe SQLite store for HR Agentic Solution (MVP 1)."""

from __future__ import annotations

import atexit
import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

SUPPORTED_COUNTRIES: tuple[str, ...] = (
    "NL",
    "DE",
    "GB",
    "BE",
    "FR",
    "ES",
    "IT",
    "IE",
    "PL",
    "SE",
    "CH",
    "AT",
    "DK",
    "FI",
    "NO",
    "PT",
    "US",
    "CA",
    "SG",
    "IN",
    "AU",
    "JP",
    "KR",
    "BR",
    "MX",
    "AE",
    "ZA",
    "CN",
)

ALLOWED_PROFILE_COUNTRIES: tuple[str, ...] = (
    "GB",
    "IE",
    "NL",
    "DE",
    "FR",
    "ES",
    "IT",
    "BE",
    "US",
    "CN",
    "SG",
)

BLOCKED_COUNTRIES: tuple[str, ...] = ("TW",)

# MVP 1 is strictly English-only (CON-005, FR-034).
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en",)

OUT_OF_SCOPE_DOMAINS: tuple[str, ...] = (
    "PAYROLL",
    "COMPENSATION",
    "STOCK_EQUITY",
    "TAX_ADVICE",
    "PERFORMANCE_REVIEWS",
    "BENEFITS_ENROLLMENT",
    "LEGAL_DISPUTES",
    "TERMINATION",
)

PRIMARY_COORDINATOR_MODEL: str = "gemini-3.8-flash"
FALLBACK_COORDINATOR_MODEL: str = "gemini-3.5-flash"


class RoleName(StrEnum):
    """Canonical RBAC roles defined in SDD §4.1 and FR-018."""

    EMPLOYEE = "employee"
    KB_ADMIN = "kb_admin"
    OPS_VIEWER = "ops_viewer"
    OPS_ONCALL = "ops_oncall"
    AUDITOR = "auditor"


class ActorType(StrEnum):
    """Canonical audit actor types defined in SDD §4.7 (FR-007, NFR-004)."""

    END_USER = "END_USER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class ErrorCode(StrEnum):
    """Canonical error codes specified in SDD §5.3, §5.4, §5.6, and §5.7."""

    AUTHN_FAILED = "AUTHN_FAILED"
    AUTHZ_DENIED = "AUTHZ_DENIED"
    CROSS_USER_ACCESS_DENIED = "CROSS_USER_ACCESS_DENIED"
    REGION_BLOCKED = "REGION_BLOCKED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    SAFETY_SESSION_TERMINATED = "SAFETY_SESSION_TERMINATED"
    SESSION_TERMINATED_SAFETY = "SESSION_TERMINATED_SAFETY"
    USER_LOCKED_OUT = "USER_LOCKED_OUT"
    TOOL_BLOCKED = "TOOL_BLOCKED"
    INVALID_CONFIRMATION_TOKEN = "INVALID_CONFIRMATION_TOKEN"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    CONFIRMATION_REPLAYED = "CONFIRMATION_REPLAYED"
    CONFIRMATION_PAYLOAD_MISMATCH = "CONFIRMATION_PAYLOAD_MISMATCH"
    CONFIRM_TOKEN_EXPIRED = "CONFIRM_TOKEN_EXPIRED"
    CONFIRM_TOKEN_REPLAYED = "CONFIRM_TOKEN_REPLAYED"
    CONFIRM_PREVIEW_MISMATCH = "CONFIRM_PREVIEW_MISMATCH"
    PREVIEW_MISMATCH = "PREVIEW_MISMATCH"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    TURN_LIMIT_REACHED = "TURN_LIMIT_REACHED"
    DEADLINE_TOO_SHORT = "DEADLINE_TOO_SHORT"
    OUT_OF_SCOPE_DOMAIN = "OUT_OF_SCOPE_DOMAIN"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    # WorkWeek domain error codes (SDD §5.4.2 / §5.4.4)
    START_IN_PAST = "START_IN_PAST"
    END_BEFORE_START = "END_BEFORE_START"
    BEYOND_HORIZON = "BEYOND_HORIZON"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    OVERLAPPING_LEAVE = "OVERLAPPING_LEAVE"
    IN_FLIGHT_DUPLICATE = "IN_FLIGHT_DUPLICATE"
    INVALID_LEAVE_TYPE = "INVALID_LEAVE_TYPE"
    ZERO_WORK_DAYS = "ZERO_WORK_DAYS"
    INVALID_PHONE_FORMAT = "INVALID_PHONE_FORMAT"
    INVALID_EMAIL_FORMAT = "INVALID_EMAIL_FORMAT"
    INVALID_ADDRESS = "INVALID_ADDRESS"
    UNSUPPORTED_COUNTRY = "UNSUPPORTED_COUNTRY"
    NO_FIELDS_PROVIDED = "NO_FIELDS_PROVIDED"
    # ServiceImmediately domain error codes (SDD §5.4.3 / §5.4.4)
    INVALID_TICKET_ID = "INVALID_TICKET_ID"
    TICKET_NOT_FOUND = "TICKET_NOT_FOUND"
    TRANSITION_NOT_ALLOWED = "TRANSITION_NOT_ALLOWED"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    INVALID_PRIORITY_FOR_IMPACT = "INVALID_PRIORITY_FOR_IMPACT"
    # Policy RAG & Document viewer error codes (SDD §5.3 / §5.5)
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    # Resilience, Idempotency & Operations error codes (SDD §5.6 / §5.7 / §5.8)
    DUPLICATE_IN_PROGRESS = "DUPLICATE_IN_PROGRESS"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    WRITES_DISABLED = "WRITES_DISABLED"
    ASSISTANT_DISABLED = "ASSISTANT_DISABLED"
    RATE_LIMITED = "RATE_LIMITED"


class IdempotencyStatus(StrEnum):
    """Idempotency record states per SDD §5.6 (FR-023)."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class ActionPlanState(StrEnum):
    """Multi-step saga action plan states per SDD §5.7 (FR-090..FR-096)."""

    DRAFT = "DRAFT"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class LeaveType(StrEnum):
    """Supported WorkWeek leave types (SDD §5.4.2)."""

    VACATION = "VACATION"
    SICK = "SICK"
    PARENTAL = "PARENTAL"
    BEREAVEMENT = "BEREAVEMENT"
    STUDY = "STUDY"


class TicketPriority(StrEnum):
    """ServiceImmediately deterministic priority levels (FR-067, SDD §5.4.4)."""

    P1_CRITICAL = "1 - Critical"
    P2_HIGH = "2 - High"
    P3_MODERATE = "3 - Moderate"
    P4_LOW = "4 - Low"


class TicketState(StrEnum):
    """ServiceImmediately ticket lifecycle states (FR-064, FR-065, SDD §5.4.4)."""

    NEW = "New"
    IN_PROGRESS = "In Progress"
    ON_HOLD = "On Hold"
    RESOLVED = "Resolved"
    CLOSED = "Closed"


class FirestoreDatabaseId(StrEnum):
    """The 6 isolated Firestore databases per ADR-019 and Architecture §4."""

    HR_PERSONAS = "hr-personas"
    HR_FLAGS = "hr-flags"
    HR_PLANS = "hr-plans"
    HR_BFF = "hr-bff"
    HR_IDEM_WW = "hr-idem-ww"
    HR_IDEM_SI = "hr-idem-si"


@dataclass(frozen=True)
class EmployeePersona:
    """Synthetic employee identity record in hr-personas (FR-020, FR-026, FR-041, FR-052)."""

    employee_id: str
    email: str
    full_name: str
    country: str
    legal_entity: str
    department: str
    role: str
    manager_employee_id: str
    manager_name: str
    manager_email: str
    hire_date: str
    fte: float = 1.0
    preferred_language: str = "en"
    timezone: str = "Europe/Amsterdam"
    work_location_type: str = "HYBRID"
    personal_phone: str = "+31612345678"
    home_address: dict[str, str] = field(
        default_factory=lambda: {
            "street": "Keizersgracht 100",
            "city": "Amsterdam",
            "postal_code": "1015 AA",
            "country": "NL",
        }
    )
    roles: tuple[str, ...] = ("employee",)


@dataclass(frozen=True)
class UserContextTokenClaims:
    """Claims inside the KMS-signed ES256 UserContextToken (UCT) per SDD §4.3.1."""

    iss: str
    sub: str
    aud: str
    email: str
    country: str
    legal_entity: str
    manager_id: str
    fte: float
    preferred_language: str
    session_id: str
    iat: int
    exp: int
    jti: str
    roles: tuple[str, ...] = ("employee",)


@dataclass(frozen=True)
class StepDigest:
    """Per-step digest bound inside a ConfirmationToken (CT) per SDD §4.3.2."""

    step_id: str
    tool_name: str
    arg_digest: str


@dataclass(frozen=True)
class ConfirmationTokenClaims:
    """Claims inside the KMS-signed ES256 ConfirmationToken (CT) per SDD §4.3.2."""

    iss: str
    sub: str
    aud: str
    session_id: str
    plan_id: str
    action_kind: str
    steps_hash: str
    step_digests: tuple[StepDigest, ...]
    preview_digest: str
    iat: int
    exp: int
    jti: str


@dataclass(frozen=True)
class IdempotencyRecord:
    """Firestore idempotency ledger entry in hr-idem-ww or hr-idem-si per SDD §5.6."""

    idempotency_key: str
    employee_id: str
    tool_name: str
    arg_digest: str
    match_key: str
    match_key_version: int
    status: IdempotencyStatus
    lease_expires_at: int
    ttl_expires_at: int
    backend_record_id: str | None = None
    response_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditEvent:
    """Structured immutable audit log event per SDD §4.7 and §4.8 (FR-007, NFR-004)."""

    event_id: str
    timestamp_utc: str
    event_type: str
    actor_type: str
    actor_id: str
    on_behalf_of: str
    agent_principal: str
    actor_country: str
    session_id: str
    turn_id: str
    trace_id: str
    target_system: str
    tool_name: str
    outcome: str
    reason_code: str
    content_sha256: str
    latency_ms: int
    retention_days: int = 400


class LocalSqliteStore:
    """Thread-safe parameterized SQLite persistence adapter for the 6 isolated Firestore DBs (ADR-019, ADR-022).

    Uses exclusively `?` parameterized SQL queries (`CREATE TABLE`, `SELECT`, `INSERT OR REPLACE`,
    `INSERT OR IGNORE`, `DELETE`) protected by `threading.RLock()` to guarantee thread safety
    under concurrent `ThreadPoolExecutor` load (TC-PERF-004).
    """

    def __init__(
        self,
        db_id: FirestoreDatabaseId | str,
        state_dir: str | Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        db_name = db_id.value if isinstance(db_id, FirestoreDatabaseId) else str(db_id)
        self.db_name = db_name
        resolved_dir = state_dir or os.environ.get("HR_AGENT_SQLITE_DIR")
        if resolved_dir:
            Path(resolved_dir).mkdir(parents=True, exist_ok=True)
        self.db_path = (
            str(Path(resolved_dir) / f"{db_name}.sqlite")
            if resolved_dir
            else ":memory:"
        )
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        atexit.register(self._conn.close)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS documents ("
                "collection_name TEXT NOT NULL, "
                "doc_id TEXT NOT NULL, "
                "payload_json TEXT NOT NULL, "
                "PRIMARY KEY (collection_name, doc_id)"
                ")"
            )
            self._conn.commit()

    def put_document(
        self, collection: str, doc_id: str, payload: dict[str, Any]
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO documents (collection_name, doc_id, payload_json) VALUES (?, ?, ?)",
                (collection, doc_id, json.dumps(payload, sort_keys=True, default=str)),
            )
            self._conn.commit()

    def create_document_if_absent(
        self, collection: str, doc_id: str, payload: dict[str, Any]
    ) -> bool:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO documents (collection_name, doc_id, payload_json) VALUES (?, ?, ?)",
                (collection, doc_id, json.dumps(payload, sort_keys=True, default=str)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get_document(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT payload_json FROM documents WHERE collection_name = ? AND doc_id = ?",
                (collection, doc_id),
            )
            row = cur.fetchone()
            return dict(json.loads(str(row[0]))) if row is not None else None

    def list_documents(self, collection: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT doc_id, payload_json FROM documents WHERE collection_name = ?",
                (collection,),
            )
            return {
                str(doc_id): dict(json.loads(str(payload_json)))
                for doc_id, payload_json in cur.fetchall()
            }

    def delete_document(self, collection: str, doc_id: str) -> bool:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "DELETE FROM documents WHERE collection_name = ? AND doc_id = ?",
                (collection, doc_id),
            )
            deleted = cur.rowcount > 0
            self._conn.commit()
            return deleted

    def put(
        self,
        collection: str,
        doc_id: str,
        payload: dict[str, Any],
        *,
        now_epoch: int | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        doc = dict(payload)
        if now_epoch is not None and ttl_seconds is not None:
            doc["_ttl_expires_at"] = int(now_epoch) + int(ttl_seconds)
        self.put_document(collection, doc_id, doc)

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return self.get_document(collection, doc_id)

    def delete(self, collection: str, doc_id: str) -> bool:
        return self.delete_document(collection, doc_id)


def http_post_json(
    base_url: str, path: str, payload: dict[str, Any], timeout_s: float = 5.0
) -> dict[str, Any]:
    """Send an HTTP POST JSON request to a loopback/downstream service and return parsed JSON."""
    import urllib.error
    import urllib.request

    target = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    raw_req = json.dumps(payload, default=str).encode("utf-8")
    req = urllib.request.Request(
        target,
        data=raw_req,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as err:
        data = json.loads(err.read().decode("utf-8") or "{}")
    if isinstance(data, dict):
        data["_http_delegated"] = True
        data["_http_target"] = target
    return data


def create_json_http_server(
    *,
    service_name: str,
    host: str,
    port: int,
    ready_fn: Any | None = None,
    version_fn: Any | None = None,
    post_dispatch: Any | None = None,
    extra_get_routes: dict[str, Any] | None = None,
) -> Any:
    """Create a loopback-bound ThreadingHTTPServer with /healthz, /readyz, /version, and custom routes."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    routes, counts = dict(extra_get_routes or {}), {"requests": 0, "post_requests": 0}

    class _ServiceHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, body: dict[str, Any]) -> None:
            is_raw = isinstance(body, dict) and "_raw_body" in body
            raw = (
                str(body["_raw_body"]).encode("utf-8")
                if is_raw
                else json.dumps(body, default=str, sort_keys=True).encode("utf-8")
            )
            ctype = (
                str(body.get("_content_type", "text/html; charset=utf-8"))
                if is_raw
                else "application/json; charset=utf-8"
            )
            for k, v in [
                ("Content-Type", ctype),
                ("Content-Length", str(len(raw))),
                ("X-Content-Type-Options", "nosniff"),
                ("X-Frame-Options", "DENY"),
                ("Cache-Control", "no-store"),
                ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
                (
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
                ),
            ]:
                if k == "Content-Type":
                    self.send_response(status)
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            counts["requests"] += 1
            if self.path == "/healthz":
                return self._send_json(
                    200,
                    {
                        "status": "ok",
                        "service": service_name,
                        "request_count": counts["requests"],
                        "post_request_count": counts["post_requests"],
                    },
                )
            if self.path == "/readyz":
                res = (
                    ready_fn()
                    if callable(ready_fn)
                    else {"status": "ready", "content_capture_safe": True}
                )
                return self._send_json(
                    200 if res.get("content_capture_safe", True) else 503, res
                )
            if self.path == "/version":
                return self._send_json(
                    200,
                    version_fn()
                    if callable(version_fn)
                    else {"service": service_name, "version": "0.1.0-mvp1"},
                )
            if self.path in routes or "*" in routes:
                fn = routes.get(self.path) or routes["*"]
                res = (
                    (
                        fn(self.path, dict(self.headers.items()))
                        if self.path not in routes
                        else (
                            fn(dict(self.headers.items()))
                            if getattr(getattr(fn, "__code__", None), "co_argcount", 0)
                            > 0
                            else fn()
                        )
                    )
                    if callable(fn)
                    else fn
                )
                return self._send_json(
                    int(res.get("http_status", 200)) if isinstance(res, dict) else 200,
                    res,
                )
            return self._send_json(
                404, {"ok": False, "error_code": "NOT_FOUND", "path": self.path}
            )

        def do_POST(self) -> None:
            counts["requests"] += 1
            counts["post_requests"] += 1
            length = int(self.headers.get("Content-Length", "0"))
            payload = (
                json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if length > 0
                else {}
            )
            status, body = (
                (
                    post_dispatch(self.path, payload, dict(self.headers.items()))
                    if getattr(
                        getattr(post_dispatch, "__code__", None), "co_argcount", 0
                    )
                    > 2
                    else post_dispatch(self.path, payload)
                )
                if callable(post_dispatch)
                else (200, {"ok": True, "path": self.path, "received": payload})
            )
            self._send_json(status, body)

        def do_PATCH(self) -> None:
            self.do_POST()

    return ThreadingHTTPServer((host, port), _ServiceHandler)


def run_http_server_cli(
    *,
    service_name: str,
    default_port: int,
    server_factory: Any,
    self_test_fn: Any | None = None,
    argv: list[str] | None = None,
) -> int:
    """Parse --host, --port, and --self-test CLI flags and run the HTTP server."""
    import argparse

    parser = argparse.ArgumentParser(description=f"Run {service_name} HTTP server")
    parser.add_argument(
        "--host",
        default=os.environ.get("BIND_HOST", os.environ.get("HOST", "127.0.0.1")),
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PORT", str(default_port)))
    )
    parser.add_argument(
        "--self-test", action="store_true", help="Run startup self-check and exit"
    )
    args = parser.parse_args(argv)
    if args.self_test:
        ok = bool(self_test_fn()) if callable(self_test_fn) else True
        print(json.dumps({"service": service_name, "self_test_ok": ok}))
        return 0 if ok else 1
    srv = server_factory(host=args.host, port=args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
