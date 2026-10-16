"""Phase 06 Unit Testing Gap-Closure Suite (TC-UT-061..TC-UT-075).

Covers:
- DEF-06-001: Cross-user session logout, session delete, and confirmation resolution denial in BffService.
- DEF-06-002: Invalid LeaveType, malformed ISO dates, invalid IANA timezone, and DST boundary handling in WorkWeekMcpServer.
- DEF-06-003: Unauthenticated grounding verification rejection and malformed hire_date handling in PolicyRagService.
- DEF-06-004: Empty ticket update and oversized comment (> 4000 chars) rejection in ServiceDeskMcpServer.
- DEF-06-005: Non-dict JSON JWT header/payload handling and full ES256 CT/UCT failure branches in KmsTokenSigner.
- Unit-level coverage of LocalSqliteStore, loopback HTTP servers, UpstreamMockSaasClient resilience, AgentCoordinator multi-agent sagas, concurrent IdempotencyStateMachine lease contention, ops services, engine_drift_job, and synthetic_prober.
"""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest

import hr_agent
from hr_agent.bff import BffService
from hr_agent.bff import create_http_server as create_bff_http_server
from hr_agent.bff import main as bff_main
from hr_agent.models import (
    ActionPlanState,
    ErrorCode,
    FirestoreDatabaseId,
    LeaveType,
    LocalSqliteStore,
    TicketPriority,
    TicketState,
    run_http_server_cli,
)
from hr_agent.ops import (
    DeploymentTopologyValidator,
    DsarToolService,
    FinOpsCostTracker,
    HrAdminService,
    get_shared_runtime_flags,
    reset_shared_runtime_flags,
)
from hr_agent.ops import (
    create_http_server as create_ops_http_server,
)
from hr_agent.ops import (
    main as ops_main,
)
from hr_agent.orchestrator import (
    AgentCoordinator,
    IdempotencyStateMachine,
    PlanReconcilerWorker,
)
from hr_agent.orchestrator import (
    create_http_server as create_orch_http_server,
)
from hr_agent.orchestrator import (
    main as orch_main,
)
from hr_agent.policy_rag import PolicyRagService
from hr_agent.policy_rag import main as policy_rag_main
from hr_agent.security import (
    KmsTokenSigner,
    OutputSafetyGate,
    RbacAuthorizer,
    RegionAccessGuard,
    TelemetrySanitizer,
    TokenVerificationFailure,
    parse_uct_or_shorthand,
    verify_mcp_write_gate,
)
from hr_agent.servicedesk_mcp import (
    ServiceDeskMcpServer,
)
from hr_agent.servicedesk_mcp import (
    create_http_server as create_sd_http_server,
)
from hr_agent.servicedesk_mcp import (
    main as sd_main,
)
from hr_agent.tools.engine_drift_job import main as drift_main
from hr_agent.tools.engine_drift_job import run_drift_check
from hr_agent.tools.synthetic_prober import (
    evaluate_slo_window,
    execute_synthetic_probe,
)
from hr_agent.tools.synthetic_prober import (
    main as prober_main,
)
from hr_agent.upstream_client import (
    UpstreamMockSaasClient,
)
from hr_agent.upstream_client import (
    create_http_server as create_upstream_http_server,
)
from hr_agent.workweek_mcp import (
    WorkWeekMcpServer,
)
from hr_agent.workweek_mcp import (
    create_http_server as create_ww_http_server,
)
from hr_agent.workweek_mcp import (
    main as ww_main,
)

pytestmark = pytest.mark.unit


def _http_request(
    url: str, method: str = "GET", payload: dict | None = None
) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8"))


def test_tc_ut_061_def_06_001_bff_cross_user_session_and_confirmation_isolation(
    tmp_path,
):
    """TC-UT-061 (FR-004, FR-015, FR-021, FR-022, DEF-06-001): Prevent cross-user session logout, deletion, and confirmation resolution."""
    bff = BffService(state_dir=str(tmp_path))
    hdr_nl = {"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"}
    hdr_de = {"X-Goog-IAP-JWT-Assertion": "jwt-de-002"}

    sess = bff.create_session(
        headers=hdr_nl, client_ip="127.0.0.1", now_epoch=1780000000
    )
    assert sess["ok"] is True
    sid = sess["session_id"]

    # Stream a leave booking preview as EMP-NL-001 to mint a pending confirmation card
    events = bff.send_message_stream(
        headers=hdr_nl,
        client_ip="127.0.0.1",
        session_id=sid,
        text="Please book vacation from 2026-06-10 to 2026-06-12",
        client_message_id="msg-nl-1",
        now_epoch=1780000010,
    )
    conf_evt = next(e for e in events if e["event"] == "confirmation_required")
    conf_id = conf_evt["data"]["confirmation_id"]

    # EMP-DE-002 attempts to resolve (confirm or cancel) EMP-NL-001's confirmation card -> 403 CROSS_USER_ACCESS_DENIED
    cross_confirm = bff.resolve_confirmation(
        headers=hdr_de,
        client_ip="127.0.0.1",
        session_id=sid,
        confirmation_id=conf_id,
        decision="confirm",
        now_epoch=1780000020,
    )
    assert cross_confirm["ok"] is False
    assert cross_confirm["http_status"] == 403
    assert cross_confirm["error_code"] == ErrorCode.CROSS_USER_ACCESS_DENIED

    cross_cancel = bff.resolve_confirmation(
        headers=hdr_de,
        client_ip="127.0.0.1",
        session_id=sid,
        confirmation_id=conf_id,
        decision="cancel",
        now_epoch=1780000021,
    )
    assert cross_cancel["ok"] is False
    assert cross_cancel["error_code"] == ErrorCode.CROSS_USER_ACCESS_DENIED

    # EMP-DE-002 attempts to logout or delete EMP-NL-001's session -> 403 CROSS_USER_ACCESS_DENIED
    cross_logout = bff.logout_session(
        headers=hdr_de,
        client_ip="127.0.0.1",
        session_id=sid,
        now_epoch=1780000022,
    )
    assert cross_logout["ok"] is False
    assert cross_logout["http_status"] == 403
    assert cross_logout["error_code"] == ErrorCode.CROSS_USER_ACCESS_DENIED

    cross_delete = bff.delete_session(
        headers=hdr_de,
        client_ip="127.0.0.1",
        session_id=sid,
    )
    assert cross_delete["ok"] is False
    assert cross_delete["http_status"] == 403
    assert cross_delete["error_code"] == ErrorCode.CROSS_USER_ACCESS_DENIED

    # EMP-DE-002 attempts to send message or submit feedback on EMP-NL-001's session -> 403 CROSS_USER_ACCESS_DENIED
    cross_msg = bff.send_message_stream(
        headers=hdr_de,
        client_ip="127.0.0.1",
        session_id=sid,
        text="Try to hijack session",
        client_message_id="msg-de-hijack",
        now_epoch=1780000023,
    )
    assert cross_msg[0]["event"] == "error"
    assert cross_msg[0]["data"]["http_status"] == 403
    assert cross_msg[0]["data"]["error_code"] == ErrorCode.CROSS_USER_ACCESS_DENIED

    cross_fb = bff.submit_feedback(
        headers=hdr_de,
        client_ip="127.0.0.1",
        session_id=sid,
        turn_id="turn-1",
        rating="DOWN",
        reason_category="INACCURATE_POLICY",
        comment="Spoofed feedback",
    )
    assert cross_fb["ok"] is False
    assert cross_fb["http_status"] == 403
    assert cross_fb["error_code"] == ErrorCode.CROSS_USER_ACCESS_DENIED

    # Owner EMP-NL-001 can still resolve the confirmation and logout/delete the session
    own_confirm = bff.resolve_confirmation(
        headers=hdr_nl,
        client_ip="127.0.0.1",
        session_id=sid,
        confirmation_id=conf_id,
        decision="confirm",
        now_epoch=1780000025,
    )
    assert own_confirm["ok"] is True
    assert own_confirm["status"] == "COMPLETED"

    # Non-NL owner (EMP-DE-002) creates and resolves their own confirmation using uct-de-002
    sess_de = bff.create_session(
        headers=hdr_de, client_ip="127.0.0.1", now_epoch=1780000026
    )
    sid_de = sess_de["session_id"]
    ev_de = bff.send_message_stream(
        headers=hdr_de,
        client_ip="127.0.0.1",
        session_id=sid_de,
        text="Please book vacation from 2026-07-01 to 2026-07-03",
        client_message_id="msg-de-1",
        now_epoch=1780000027,
    )
    conf_id_de = next(e for e in ev_de if e["event"] == "confirmation_required")[
        "data"
    ]["confirmation_id"]
    de_confirm = bff.resolve_confirmation(
        headers=hdr_de,
        client_ip="127.0.0.1",
        session_id=sid_de,
        confirmation_id=conf_id_de,
        decision="confirm",
        now_epoch=1780000028,
    )
    assert de_confirm["ok"] is True
    assert de_confirm["status"] == "COMPLETED"

    own_logout = bff.logout_session(
        headers=hdr_nl,
        client_ip="127.0.0.1",
        session_id=sid,
        now_epoch=1780000030,
    )
    assert own_logout["ok"] is True
    assert own_logout["session_terminated"] is True


def test_tc_ut_062_def_06_002_workweek_invalid_leave_type_dates_timezones_and_dst(
    tmp_path,
):
    """TC-UT-062 (FR-046, FR-047, FR-051, DEF-06-002): Validate LeaveType enum, malformed ISO dates, invalid IANA timezone, and DST boundaries."""
    ww = WorkWeekMcpServer(state_dir=str(tmp_path))

    # Unknown leave_type -> INVALID_LEAVE_TYPE
    bad_type = ww.validate_leave_request(
        uct="uct-nl-001",
        leave_type="UNPAID_SABBATICAL",
        start_date="2026-06-15",
        end_date="2026-06-16",
    )
    assert bad_type["ok"] is False
    assert bad_type["error_code"] == ErrorCode.INVALID_LEAVE_TYPE

    # Malformed start_date / end_date / today_local -> VALIDATION_FAILED (no unhandled ValueError)
    bad_date = ww.validate_leave_request(
        uct="uct-nl-001",
        leave_type=LeaveType.VACATION.value,
        start_date="2026-13-45",
        end_date="2026-06-16",
    )
    assert bad_date["ok"] is False
    assert bad_date["error_code"] == ErrorCode.VALIDATION_FAILED

    # Invalid IANA timezone and malformed UTC timestamp in resolve_relative_dates -> VALIDATION_FAILED
    bad_tz = ww.resolve_relative_dates(
        expression="tomorrow",
        user_timezone="Invalid/Timezone_Name",
        now_utc_iso="2026-06-01T12:00:00Z",
    )
    assert bad_tz["ok"] is False
    assert bad_tz["error_code"] == ErrorCode.VALIDATION_FAILED

    bad_iso = ww.resolve_relative_dates(
        expression="tomorrow",
        user_timezone="Europe/Amsterdam",
        now_utc_iso="not-an-iso-timestamp",
    )
    assert bad_iso["ok"] is False
    assert bad_iso["error_code"] == ErrorCode.VALIDATION_FAILED

    # DST Spring-Forward boundary in Europe/Amsterdam (2026-03-29T00:30:00Z -> CET/CEST transition)
    dst_spring = ww.resolve_relative_dates(
        expression="Monday",
        user_timezone="Europe/Amsterdam",
        now_utc_iso="2026-03-29T00:30:00Z",
    )
    assert dst_spring["ok"] is True
    assert dst_spring["local_today"] == "2026-03-29"
    assert dst_spring["start_date"] == "2026-03-30"
    assert dst_spring["work_days"] == 1.0

    # Asia/Shanghai late-UTC boundary (2026-06-01T22:30:00Z is already 2026-06-02 in Shanghai)
    cn_res = ww.resolve_relative_dates(
        expression="tomorrow",
        user_timezone="Asia/Shanghai",
        now_utc_iso="2026-06-01T22:30:00Z",
    )
    assert cn_res["ok"] is True
    assert cn_res["local_today"] == "2026-06-02"
    assert cn_res["start_date"] == "2026-06-03"


def test_tc_ut_063_def_06_003_policy_rag_uct_enforcement_and_malformed_hire_date():
    """TC-UT-063 (FR-011, FR-052, FR-073, DEF-06-003): Reject unauthenticated verify_grounding_and_repair and malformed hire_date in evaluate_eligibility."""
    rag = PolicyRagService()

    # Empty/invalid UCT in verify_grounding_and_repair -> AUTHN_FAILED
    unauth = rag.verify_grounding_and_repair(
        uct="",
        query="What is the NL vacation allowance?",
        draft_answer="25 days [POL-NL-LEAVE-2026#4.2].",
        retrieved_chunks=[
            {
                "doc_id": "POL-NL-LEAVE-2026",
                "section_id": "4.2",
                "status": "active",
                "text": "25 days paid leave.",
            }
        ],
    )
    assert unauth["ok"] is False
    assert unauth["verdict"] == "ABSTAIN"
    assert unauth["error_code"] == ErrorCode.AUTHN_FAILED

    # Malformed hire_date in evaluate_eligibility -> VALIDATION_FAILED
    bad_hire = rag.evaluate_eligibility(
        uct="uct-nl-001",
        benefit_or_policy_topic="home_office_stipend",
        policy_chunks=[
            {"doc_id": "POL-GLOBAL-EQUIP-2026", "section_id": "2.1", "status": "active"}
        ],
        employee_profile={
            "work_location_type": "REMOTE",
            "hire_date": "not-a-date",
            "fte": 1.0,
        },
    )
    assert bad_hire["ok"] is False
    assert bad_hire["error_code"] == ErrorCode.VALIDATION_FAILED

    # Unmet criteria (OFFICE location, <90d tenure, 0.0 FTE) vs exact 89d/90d REMOTE tenure boundaries
    unmet = rag.evaluate_eligibility(
        uct="uct-nl-001",
        benefit_or_policy_topic="home_office_stipend",
        policy_chunks=[
            {"doc_id": "POL-GLOBAL-EQUIP-2026", "section_id": "2.1", "status": "active"}
        ],
        employee_profile={
            "work_location_type": "OFFICE",
            "hire_date": "2026-05-15",
            "fte": 0.0,
        },
    )
    assert unmet["ok"] is True
    assert unmet["eligible"] is False
    assert set(unmet["unmet_criteria"]) == {"work_location_type", "tenure_days", "fte"}

    boundary_89d = rag.evaluate_eligibility(
        uct="uct-nl-001",
        benefit_or_policy_topic="home_office_stipend",
        policy_chunks=[
            {"doc_id": "POL-GLOBAL-EQUIP-2026", "section_id": "2.1", "status": "active"}
        ],
        employee_profile={
            "work_location_type": "REMOTE",
            "hire_date": "2026-03-04",
            "fte": 1.0,
        },
    )
    assert boundary_89d["eligible"] is False and boundary_89d["unmet_criteria"] == [
        "tenure_days"
    ]

    boundary_90d = rag.evaluate_eligibility(
        uct="uct-nl-001",
        benefit_or_policy_topic="home_office_stipend",
        policy_chunks=[
            {"doc_id": "POL-GLOBAL-EQUIP-2026", "section_id": "2.1", "status": "active"}
        ],
        employee_profile={
            "work_location_type": "REMOTE",
            "hire_date": "2026-03-03",
            "fte": 1.0,
        },
    )
    assert boundary_90d["eligible"] is True and boundary_90d["unmet_criteria"] == []

    # CLI entrypoints (--self-test, --mode sync, --mode verify-grounding)
    assert policy_rag_main(["--self-test"]) == 0
    assert policy_rag_main(["--mode", "sync"]) == 0
    assert policy_rag_main(["--mode", "verify-grounding"]) == 0


def test_tc_ut_064_def_06_004_servicedesk_validate_ticket_update_boundaries(tmp_path):
    """TC-UT-064 (FR-063, FR-064, FR-068, DEF-06-004): Reject empty ticket updates and oversized comments (> 4000 chars)."""
    sd = ServiceDeskMcpServer(state_dir=str(tmp_path))

    # Empty update (both new_status=None and comment=None/whitespace) -> VALIDATION_FAILED
    empty_res = sd.validate_ticket_update(
        uct="uct-nl-001",
        ticket_id="INC123456",
        new_status=None,
        comment="   ",
    )
    assert empty_res["ok"] is False
    assert empty_res["error_code"] == ErrorCode.VALIDATION_FAILED

    # Comment at exact 4000-char boundary -> allowed
    ok_4000 = sd.validate_ticket_update(
        uct="uct-nl-001",
        ticket_id="INC123456",
        comment="A" * 4000,
    )
    assert ok_4000["ok"] is True

    # Comment at 4001 chars -> VALIDATION_FAILED
    too_long = sd.validate_ticket_update(
        uct="uct-nl-001",
        ticket_id="INC123456",
        comment="A" * 4001,
    )
    assert too_long["ok"] is False
    assert too_long["error_code"] == ErrorCode.VALIDATION_FAILED


def test_tc_ut_065_def_06_005_kms_token_signer_adversarial_jwt_and_ct_branches(
    persona_nl,
):
    """TC-UT-065 (FR-004, FR-005, FR-016, FR-022, NFR-003, DEF-06-005): Exercise adversarial JWT structures and CT/UCT verification failure paths."""
    signer = KmsTokenSigner()

    # Non-dict JSON header/payload (e.g., JSON list [1,2,3]) must return TokenVerificationFailure, not raise AttributeError
    non_dict_seg = base64.urlsafe_b64encode(b"[1,2,3]").decode("ascii").rstrip("=")
    malformed_jwt = f"{non_dict_seg}.{non_dict_seg}.fakesig"

    uct_fail = signer.verify_uct(
        malformed_jwt, expected_audience="workweek-mcp", now_epoch=1780000000
    )
    assert isinstance(uct_fail, TokenVerificationFailure)
    assert uct_fail.error_code == ErrorCode.AUTHN_FAILED

    ct_fail = signer.verify_and_consume_ct(
        token=malformed_jwt,
        expected_sub="EMP-NL-001",
        expected_session_id="sess-1",
        expected_plan_id="plan-1",
        expected_step_id="step-1",
        expected_tool_name="submit_my_leave_request",
        expected_arg_digest="sha256:arg",
        expected_preview_digest="sha256:prev",
        now_epoch=1780000000,
    )
    assert isinstance(ct_fail, TokenVerificationFailure)
    assert ct_fail.error_code == ErrorCode.INVALID_CONFIRMATION_TOKEN

    # Valid CT minting + expired, wrong identity, wrong preview_digest, wrong step_digest, and valid consumption + replay
    valid_ct = signer.mint_ct(
        sub=persona_nl.employee_id,
        session_id="sess-ct-1",
        plan_id="plan-ct-1",
        action_kind="SINGLE_WRITE",
        steps_hash="sha256:steps",
        step_digests=[
            {
                "step_id": "step-1",
                "tool_name": "submit_my_leave_request",
                "arg_digest": "sha256:arg1",
            }
        ],
        preview_digest="sha256:prev1",
        now_epoch=1780000000,
        ttl_seconds=300,
    )

    exp_res = signer.verify_and_consume_ct(
        token=valid_ct,
        expected_sub=persona_nl.employee_id,
        expected_session_id="sess-ct-1",
        expected_plan_id="plan-ct-1",
        expected_step_id="step-1",
        expected_tool_name="submit_my_leave_request",
        expected_arg_digest="sha256:arg1",
        expected_preview_digest="sha256:prev1",
        now_epoch=1780000305,
    )
    assert isinstance(exp_res, TokenVerificationFailure)
    assert exp_res.error_code == ErrorCode.INVALID_CONFIRMATION_TOKEN

    sub_mismatch = signer.verify_and_consume_ct(
        token=valid_ct,
        expected_sub="EMP-DE-002",
        expected_session_id="sess-ct-1",
        expected_plan_id="plan-ct-1",
        expected_step_id="step-1",
        expected_tool_name="submit_my_leave_request",
        expected_arg_digest="sha256:arg1",
        expected_preview_digest="sha256:prev1",
        now_epoch=1780000010,
    )
    assert isinstance(sub_mismatch, TokenVerificationFailure)
    assert sub_mismatch.error_code == ErrorCode.INVALID_CONFIRMATION_TOKEN

    step_mismatch = signer.verify_and_consume_ct(
        token=valid_ct,
        expected_sub=persona_nl.employee_id,
        expected_session_id="sess-ct-1",
        expected_plan_id="plan-ct-1",
        expected_step_id="step-1",
        expected_tool_name="submit_my_leave_request",
        expected_arg_digest="sha256:tampered",
        expected_preview_digest="sha256:prev1",
        now_epoch=1780000010,
    )
    assert isinstance(step_mismatch, TokenVerificationFailure)
    assert step_mismatch.error_code == ErrorCode.PREVIEW_MISMATCH

    # Public key coordinates export verification
    qx, qy = signer.uct_public_key_xy
    cx, cy = signer.ct_public_key_xy
    assert isinstance(qx, int) and isinstance(qy, int) and qx > 0 and qy > 0
    assert (qx, qy) != (cx, cy)


def test_tc_ut_066_package_lazy_loading_and_local_sqlite_store_lifecycle(tmp_path):
    """TC-UT-066 (ADR-019, ADR-022, BH-05-1-03): Verify hr_agent PEP 562 lazy attribute loading and LocalSqliteStore TTL/CRUD operations."""
    # PEP 562 lazy imports and __dir__
    exported = dir(hr_agent)
    assert "BffService" in exported
    assert "WorkWeekMcpServer" in exported
    assert hr_agent.BffService is BffService
    with pytest.raises(AttributeError):
        _ = hr_agent.NonExistentSymbolXYZ

    # LocalSqliteStore CRUD + TTL metadata + list_documents
    store = LocalSqliteStore(FirestoreDatabaseId.HR_BFF, state_dir=str(tmp_path))
    store.put(
        "sessions",
        "s1",
        {"session_id": "s1", "owner": "EMP-NL-001"},
        now_epoch=1000,
        ttl_seconds=60,
    )
    store.put(
        "sessions",
        "s2",
        {"session_id": "s2", "owner": "EMP-DE-002"},
        now_epoch=1000,
        ttl_seconds=600,
    )

    s1_doc = store.get("sessions", "s1")
    assert s1_doc is not None and s1_doc["_ttl_expires_at"] == 1060
    assert store.get_document("sessions", "s1") is not None
    assert len(store.list_documents("sessions")) == 2
    assert store.delete("sessions", "s1") is True
    assert store.get_document("sessions", "s1") is None
    assert len(store.list_documents("sessions")) == 1

    # create_document_if_absent idempotency
    assert (
        store.create_document_if_absent("flags", "global", {"writes_enabled": True})
        is True
    )
    assert (
        store.create_document_if_absent("flags", "global", {"writes_enabled": False})
        is False
    )
    assert store.get("flags", "global")["writes_enabled"] is True
    assert store.delete("sessions", "s2") is True
    assert store.delete("sessions", "s2") is False


def test_tc_ut_067_unicode_contact_validation_and_workweek_write_execution(tmp_path):
    """TC-UT-067 (FR-040..FR-047, NFR-003): Exercise Unicode contact fields, live leave submission, contact update, and get_write_status in WorkWeekMcpServer."""
    reset_shared_runtime_flags()
    ww = WorkWeekMcpServer(state_dir=str(tmp_path))

    # Unicode street/city validation
    val_contact = ww.validate_contact_update(
        uct="uct-nl-001",
        home_address={
            "street": "Prinsengracht 263-bis — Café Münchner Straße 🇳🇱",
            "city": "Amsterdam-Zuid",
            "postal_code": "1016 GV",
            "country": "NL",
        },
        personal_phone="+31612345678",
    )
    assert val_contact["ok"] is True
    p_digest = val_contact["for_llm"]["preview_digest"]

    # Execute contact update and replay idempotency key
    upd = ww.update_my_contact_info(
        uct="uct-nl-001",
        ct="ct-valid-contact-1",
        idempotency_key="idem-ww-contact-unicode-1",
        step_id="step-1",
        home_address={
            "street": "Prinsengracht 263-bis — Café Münchner Straße 🇳🇱",
            "city": "Amsterdam-Zuid",
            "postal_code": "1016 GV",
            "country": "NL",
        },
        personal_phone="+31612345678",
        preview_digest=p_digest,
    )
    assert upd["ok"] is True
    replay_upd = ww.update_my_contact_info(
        uct="uct-nl-001",
        ct="ct-valid-contact-1",
        idempotency_key="idem-ww-contact-unicode-1",
        step_id="step-1",
        home_address={
            "street": "Prinsengracht 263-bis",
            "city": "Amsterdam",
            "postal_code": "1016 GV",
            "country": "NL",
        },
        preview_digest=p_digest,
    )
    assert replay_upd["ok"] is True
    assert (
        replay_upd["for_llm"]["backend_record_id"]
        == upd["for_llm"]["backend_record_id"]
    )

    # Verify in-flight duplicate rejection on seeded 2026-08-03..2026-08-05 and submit clean 2026-08-10..2026-08-12 leave
    dup_leave = ww.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2026-08-03",
        end_date="2026-08-05",
    )
    assert dup_leave["ok"] is False
    assert dup_leave["error_code"] == ErrorCode.IN_FLIGHT_DUPLICATE

    val_leave = ww.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2026-08-10",
        end_date="2026-08-12",
    )
    assert val_leave["ok"] is True
    sub_leave = ww.submit_my_leave_request(
        uct="uct-nl-001",
        ct="ct-valid-leave-1",
        idempotency_key="idem-ww-leave-aug-1",
        step_id="step-1",
        leave_type="VACATION",
        start_date="2026-08-10",
        end_date="2026-08-12",
        expected_work_days=3.0,
        preview_digest=val_leave["for_llm"]["preview_digest"],
    )
    assert sub_leave["ok"] is True

    ws_ok = ww.get_write_status(
        caller_sa="plan-reconciler-sa@project.iam.gserviceaccount.com",
        idempotency_key="idem-ww-leave-aug-1",
    )
    assert ws_ok["ok"] is True
    assert ws_ok["status"] == "COMPLETED"

    ws_denied = ww.get_write_status(
        caller_sa="hr-bff-sa@project.iam.gserviceaccount.com",
        idempotency_key="idem-ww-leave-aug-1",
    )
    assert ws_denied["ok"] is False
    assert ws_denied["error_code"] == ErrorCode.AUTHZ_DENIED


def test_tc_ut_068_servicedesk_ticket_lifecycle_and_write_tools(tmp_path):
    """TC-UT-068 (FR-060..FR-069, FR-096): Exercise ServiceDeskMcpServer read/validate/write tools, idempotent replay, and get_write_status."""
    reset_shared_runtime_flags()
    sd = ServiceDeskMcpServer(state_dir=str(tmp_path))

    # Read open tickets and single ticket
    open_tkts = sd.list_my_open_tickets(uct="uct-nl-001")
    assert open_tkts["ok"] is True
    assert len(open_tkts["for_llm"]["tickets"]) >= 1

    tkt = sd.get_my_ticket(uct="uct-nl-001", ticket_id="INC123456")
    assert tkt["ok"] is True
    assert tkt["for_llm"]["ticket_id"] == "INC123456"

    # Validate and create incident + idempotent replay
    val_inc = sd.validate_incident(
        uct="uct-nl-001",
        category="Hardware - Peripherals",
        short_description="External dock port issue",
        detailed_description="USB-C dock HDMI output flickering",
        requested_priority=TicketPriority.P4_LOW.value,
        impact={
            "users_affected": "ONE",
            "can_work": True,
            "workaround_available": True,
        },
    )
    assert val_inc["ok"] is True
    inc_res = sd.create_incident(
        uct="uct-nl-001",
        ct="ct-sd-inc-1",
        idempotency_key="idem-sd-inc-1",
        step_id="step-1",
        category="Hardware - Peripherals",
        short_description="External dock port issue",
        detailed_description="USB-C dock HDMI output flickering",
        priority=TicketPriority.P4_LOW.value,
        impact={
            "users_affected": "ONE",
            "can_work": True,
            "workaround_available": True,
        },
        preview_digest=val_inc["for_llm"]["preview_digest"],
    )
    assert inc_res["ok"] is True
    inc_replay = sd.create_incident(
        uct="uct-nl-001",
        ct="ct-sd-inc-1",
        idempotency_key="idem-sd-inc-1",
        step_id="step-1",
        category="Hardware - Peripherals",
        short_description="External dock port issue",
        detailed_description="USB-C dock HDMI output flickering",
        priority=TicketPriority.P4_LOW.value,
        impact={
            "users_affected": "ONE",
            "can_work": True,
            "workaround_available": True,
        },
        preview_digest=val_inc["for_llm"]["preview_digest"],
    )
    assert inc_replay["ok"] is True
    assert inc_replay["for_llm"]["ticket_id"] == inc_res["for_llm"]["ticket_id"]

    # Add comment and update status on INC123456
    val_c = sd.validate_ticket_update(
        uct="uct-nl-001", ticket_id="INC123456", comment="Tested new dock firmware."
    )
    assert val_c["ok"] is True
    c_res = sd.add_ticket_comment(
        uct="uct-nl-001",
        ct="ct-sd-comment-1",
        idempotency_key="idem-sd-comment-1",
        step_id="step-1",
        ticket_id="INC123456",
        comment="Tested new dock firmware.",
        preview_digest=val_c["for_llm"]["preview_digest"],
    )
    assert c_res["ok"] is True

    val_s = sd.validate_ticket_update(
        uct="uct-nl-001",
        ticket_id="INC123456",
        new_status=TicketState.CLOSED.value,
        resolution_notes="Confirmed fixed.",
    )
    assert val_s["ok"] is True
    s_res = sd.update_ticket_status(
        uct="uct-nl-001",
        ct="ct-sd-status-1",
        idempotency_key="idem-sd-status-1",
        step_id="step-2",
        ticket_id="INC123456",
        new_status=TicketState.CLOSED.value,
        resolution_notes="Confirmed fixed.",
        preview_digest=val_s["for_llm"]["preview_digest"],
    )
    assert s_res["ok"] is True

    ws = sd.get_write_status(
        caller_sa="hr-agent-sa@project.iam.gserviceaccount.com",
        idempotency_key="idem-sd-status-1",
    )
    assert ws["ok"] is True
    assert ws["status"] == "COMPLETED"


def test_tc_ut_069_orchestrator_composite_sagas_and_concurrent_idempotency(tmp_path):
    """TC-UT-069 (FR-023, FR-090..FR-096): Verify AgentCoordinator composite sagas (UC-2.2, UC-2.3), PlanReconcilerWorker, and concurrent IdempotencyStateMachine lease contention."""
    reset_shared_runtime_flags()
    coord = AgentCoordinator(state_dir=str(tmp_path))

    # Build and execute UC-2.3 relocation plan
    plan = coord.build_action_plan(
        session_id="sess-uc23",
        uct="uct-nl-001",
        use_case_id="UC-2.3",
        proposed_steps=[
            {
                "step_id": "step-1",
                "target_system": "WORKWEEK",
                "tool_name": "update_my_contact_info",
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
                "depends_on": ["step-1"],
                "args": {
                    "category": "Facilities - Badging and Physical Access",
                    "short_description": "Badge re-provisioning",
                    "priority": "3 - Moderate",
                },
            },
        ],
    )
    assert plan["ok"] is True
    assert plan["state"] == ActionPlanState.AWAITING_CONFIRMATION

    exec_uc23 = coord.execute_action_plan(
        session_id="sess-uc23",
        uct="uct-nl-001",
        ct="ct-uc23-confirm",
        plan_id="plan-uc23-reloc",
        decision="confirm",
    )
    assert exec_uc23["ok"] is True
    assert exec_uc23["plan_state"] == ActionPlanState.COMPLETED

    # Cancel decision on built plan
    cancel_res = coord.execute_action_plan(
        session_id="sess-uc23",
        uct="uct-nl-001",
        ct="ct-uc23-cancel",
        plan_id=plan["plan_id"],
        decision="cancel",
    )
    assert cancel_res["ok"] is True
    assert cancel_res["plan_state"] == ActionPlanState.CANCELLED

    # Concurrent threads racing on IdempotencyStateMachine.acquire_or_inspect -> exactly 1 thread acquires lease
    idem = IdempotencyStateMachine(state_dir=str(tmp_path))
    keys = idem.compute_idempotency_key(
        session_id="sess-race",
        plan_id="plan-race",
        step_id="step-1",
        tool_name="submit_my_leave_request",
        canonical_args={"start_date": "2026-08-10", "end_date": "2026-08-12"},
    )

    def _race_acquire(_: int) -> dict:
        return idem.acquire_or_inspect(
            db_id=FirestoreDatabaseId.HR_IDEM_WW.value,
            idempotency_key=keys["idempotency_key"],
            employee_id="EMP-NL-001",
            tool_name="submit_my_leave_request",
            arg_digest=keys["arg_digest"],
            match_key=keys["match_key"],
            match_key_version=1,
            now_epoch=1780000000,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(_race_acquire, range(8)))
    winners = [o for o in outcomes if o["ok"] is True]
    losers = [
        o
        for o in outcomes
        if not o["ok"] and o.get("error_code") == ErrorCode.DUPLICATE_IN_PROGRESS
    ]
    assert len(winners) == 1
    assert len(losers) == 7

    # Reconciler worker
    reconciler = PlanReconcilerWorker(state_dir=str(tmp_path))
    assert (
        reconciler.reconcile_stale_plans(
            caller_sa="unauthorized-sa", now_epoch=1780001000
        )
        == []
    )
    reconciled = reconciler.reconcile_stale_plans(
        caller_sa="plan-reconciler-sa", now_epoch=1780001000
    )
    assert len(reconciled) == 2


def test_tc_ut_070_upstream_client_transport_retry_profiles_and_circuit_breaker():
    """TC-UT-070 (NFR-040, NFR-041, NFR-044, ADR-021): Exercise UpstreamMockSaasClient headers, call_upstream, retry profiles, OpenAPI verifier, and circuit breaker."""
    client = UpstreamMockSaasClient()

    hdrs_proxy = client.build_outbound_headers(
        iap_oidc_token="iap-jwt",
        uct="uct-jwt",
        traceparent="00-trace-span-01",
        idempotency_key="idem-1",
        deadline_epoch_ms=17800005000,
        iap_header_mode="proxy_authorization",
        backend_app_token="backend-jwt",
    )
    assert hdrs_proxy["Proxy-Authorization"] == "Bearer iap-jwt"
    assert hdrs_proxy["Authorization"] == "Bearer backend-jwt"
    assert hdrs_proxy["Idempotency-Key"] == "idem-1"
    assert hdrs_proxy["X-Request-Deadline"] == "17800005000"

    call_ok = client.call_upstream(
        transport_mode="mcp_http",
        system="WORKWEEK",
        operation="get_my_profile",
        payload={"employee_id": "EMP-NL-001"},
        headers=hdrs_proxy,
        timeout_seconds=3.0,
        is_write=False,
    )
    assert call_ok["ok"] is True
    assert call_ok["http_method"] == "GET"

    call_bad = client.call_upstream(
        transport_mode="invalid_mode",
        system="WORKWEEK",
        operation="get_my_profile",
        payload={},
        headers=hdrs_proxy,
        timeout_seconds=10.0,
    )
    assert call_bad["ok"] is False
    assert call_bad["error_code"] == ErrorCode.VALIDATION_FAILED

    # Retry profiles: READ, PROFILE_K, PROFILE_R, PROFILE_N, and DEADLINE_TOO_SHORT
    assert (
        client.execute_with_retry_profile(
            profile="READ", remaining_deadline_ms=2000, attempt_outcomes=["SUCCESS"]
        )["error_code"]
        == ErrorCode.DEADLINE_TOO_SHORT
    )

    assert (
        client.execute_with_retry_profile(
            profile="READ",
            remaining_deadline_ms=5000,
            attempt_outcomes=["TIMEOUT", "SUCCESS"],
        )["ok"]
        is True
    )
    assert (
        client.execute_with_retry_profile(
            profile="PROFILE_K",
            remaining_deadline_ms=5000,
            attempt_outcomes=["TIMEOUT", "TIMEOUT"],
        )["error_code"]
        == ErrorCode.OUTCOME_UNKNOWN
    )

    assert (
        client.execute_with_retry_profile(
            profile="PROFILE_R",
            remaining_deadline_ms=7000,
            attempt_outcomes=["HTTP_503", "SUCCESS"],
        )["ok"]
        is True
    )
    assert (
        client.execute_with_retry_profile(
            profile="PROFILE_R",
            remaining_deadline_ms=7000,
            attempt_outcomes=["TIMEOUT"],
        )["error_code"]
        == ErrorCode.OUTCOME_UNKNOWN
    )

    assert (
        client.execute_with_retry_profile(
            profile="PROFILE_N",
            remaining_deadline_ms=8000,
            attempt_outcomes=["TIMEOUT"],
            lookup_found_record=True,
        )["ok"]
        is True
    )
    assert (
        client.execute_with_retry_profile(
            profile="PROFILE_N",
            remaining_deadline_ms=8000,
            attempt_outcomes=["TIMEOUT"],
            lookup_found_record=False,
        )["error_code"]
        == ErrorCode.OUTCOME_UNKNOWN
    )

    # Circuit breaker CLOSED vs OPEN
    cb_closed = client.evaluate_circuit_breaker(
        system="WORKWEEK",
        recent_outcomes=["SUCCESS", "HTTP_503", "HTTP_503"],
        now_epoch=1780000000,
    )
    assert cb_closed["circuit_state"] == "CLOSED"

    cb_open = client.evaluate_circuit_breaker(
        system="WORKWEEK",
        recent_outcomes=[
            "SUCCESS",
            "TIMEOUT",
            "HTTP_502",
            "HTTP_503",
            "HTTP_504",
            "HTTP_500",
        ],
        now_epoch=1780000000,
    )
    assert cb_open["circuit_state"] == "OPEN"
    assert cb_open["error_code"] == ErrorCode.UPSTREAM_UNAVAILABLE


def test_tc_ut_071_bff_end_to_end_turn_branches_and_session_lifecycle(tmp_path):
    """TC-UT-071 (FR-006..FR-028, FR-100..FR-106): Exercise BffService persona resolution, session listing/messages, UC-1.1/1.1b/1.2c/1.3/2.1/2.2/2.3 streams, docs, and feedback."""
    reset_shared_runtime_flags()
    bff = BffService(state_dir=str(tmp_path))

    # Auth variations: CN, DE, TW block, missing JWT
    assert (
        bff.get_me(
            headers={"X-Goog-IAP-JWT-Assertion": "jwt-cn-003"}, client_ip="127.0.0.1"
        )["employee_id"]
        == "EMP-CN-003"
    )
    assert (
        bff.get_me(
            headers={"X-Goog-IAP-JWT-Assertion": "jwt-de-002"}, client_ip="127.0.0.1"
        )["employee_id"]
        == "EMP-DE-002"
    )
    assert (
        bff.get_me(headers={"X-Client-Geo-Country": "TW"}, client_ip="203.69.1.1")[
            "error_code"
        ]
        == ErrorCode.REGION_BLOCKED
    )
    assert (
        bff.get_me(headers={}, client_ip="127.0.0.1")["error_code"]
        == ErrorCode.AUTHN_FAILED
    )

    hdr_nl = {"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"}
    sess = bff.create_session(
        headers=hdr_nl, client_ip="127.0.0.1", now_epoch=1780000000
    )
    sid = sess["session_id"]
    assert (
        len(bff.list_sessions(headers=hdr_nl, client_ip="127.0.0.1")["sessions"]) >= 1
    )

    # Stream each use-case branch in send_message_stream
    for idx, prompt in enumerate(
        [
            "What is the 6 months paid travel sabbatical policy?",
            "I need a 27-inch 4K monitor for remote work equipment",
            "I need sick leave and to delegate my email",
            "I have an office relocation and need a new badge",
            "Please update my home address and personal phone",
            "My laptop screen is dead, priority 1 - Critical",
            "How many vacation days do I get in the Netherlands?",
        ],
        start=1,
    ):
        evs = bff.send_message_stream(
            headers=hdr_nl,
            client_ip="127.0.0.1",
            session_id=sid,
            text=prompt,
            client_message_id=f"m-{idx}",
            now_epoch=1780000000 + idx,
        )
        assert evs[-1]["event"] == "done"

    # Session messages retrieval (owner vs non-existent)
    msg_res = bff.get_session_messages(
        headers=hdr_nl, client_ip="127.0.0.1", session_id=sid
    )
    assert msg_res["ok"] is True
    assert len(msg_res["messages"]) == 7

    not_found = bff.get_session_messages(
        headers=hdr_nl, client_ip="127.0.0.1", session_id="sess-unknown-999"
    )
    assert not_found["http_status"] == 404

    # Document proxy: active NL doc, retired doc, cross-country DE doc
    assert (
        bff.get_document(
            headers=hdr_nl, client_ip="127.0.0.1", doc_id="POL-NL-LEAVE-2026"
        )["ok"]
        is True
    )
    assert (
        bff.get_document(
            headers=hdr_nl, client_ip="127.0.0.1", doc_id="POL-NL-LEAVE-2023-RETIRED"
        )["http_status"]
        == 404
    )
    assert (
        bff.get_document(
            headers=hdr_nl, client_ip="127.0.0.1", doc_id="POL-DE-LEAVE-2026"
        )["http_status"]
        == 404
    )

    # Feedback submission with phone/BSN redaction
    fb = bff.submit_feedback(
        headers=hdr_nl,
        client_ip="127.0.0.1",
        session_id=sid,
        turn_id="turn-1",
        rating="UP",
        reason_category="ACCURATE",
        comment="Call me at +31612345678 or BSN 123456789",
    )
    assert fb["ok"] is True
    assert "[REDACTED-PHONE]" in fb["redacted_comment"]
    assert "[REDACTED-ID]" in fb["redacted_comment"]


def test_tc_ut_072_ops_kill_switch_dsar_finops_and_topology_validators(tmp_path):
    """TC-UT-072 (FR-012, FR-013, FR-097, NFR-004, NFR-022, NFR-045, NFR-051, ADR-019): Exercise HrAdminService, DsarToolService, FirestoreIamMatrixVerifier, FinOpsCostTracker, and DeploymentTopologyValidator."""
    admin = HrAdminService(state_dir=str(tmp_path))
    seed = admin.seed_runtime_flags_if_absent(now_epoch=1780000000)
    assert seed["ok"] is True

    # Unauthorized SA vs invalid flag vs valid toggle
    assert (
        admin.set_kill_switch(
            flag_name="writes_enabled",
            enabled=False,
            reason="test",
            caller_sa="hr-bff-sa",
            now_epoch=1780000010,
        )["error_code"]
        == ErrorCode.AUTHZ_DENIED
    )
    assert (
        admin.set_kill_switch(
            flag_name="invalid_flag",
            enabled=False,
            reason="test",
            caller_sa="hr-admin-sa",
            now_epoch=1780000010,
        )["error_code"]
        == ErrorCode.VALIDATION_FAILED
    )

    toggled = admin.set_kill_switch(
        flag_name="writes_enabled",
        enabled=False,
        reason="drill",
        caller_sa="hr-admin-sa",
        now_epoch=1780000010,
    )
    assert toggled["ok"] is True
    assert get_shared_runtime_flags()["writes_enabled"] is False
    reset_shared_runtime_flags()

    # DSAR deletion (unauthorized vs authorized + unseeded employee)
    dsar = DsarToolService(state_dir=str(tmp_path))
    assert (
        dsar.execute_dsar_deletion(
            employee_id="EMP-NL-001", caller_sa="hr-admin-sa", now_epoch=1780000000
        )["error_code"]
        == ErrorCode.AUTHZ_DENIED
    )
    del_nl = dsar.execute_dsar_deletion(
        employee_id="EMP-NL-001", caller_sa="dsar-tool-sa", now_epoch=1780000000
    )
    assert del_nl["ok"] is True
    assert del_nl["sessions_purged"] >= 2
    del_cn = dsar.execute_dsar_deletion(
        employee_id="EMP-CN-003", caller_sa="dsar-tool-sa", now_epoch=1780000010
    )
    assert del_cn["ok"] is True
    assert del_cn["sessions_purged"] == 2

    # FinOps cost tracker across pro, 3.5-flash, and 3.8-flash + budget alert thresholds
    finops = FinOpsCostTracker()
    assert (
        finops.evaluate_turn_cost_and_budget(
            model_id="gemini-2.5-pro",
            input_tokens=1000,
            output_tokens=500,
            monthly_projected_usd=4500.0,
        )["alert_triggered"]
        == "BUDGET_50_PCT"
    )
    assert (
        finops.evaluate_turn_cost_and_budget(
            model_id="gemini-3.5-flash",
            input_tokens=1000,
            output_tokens=500,
            monthly_projected_usd=7000.0,
        )["alert_triggered"]
        == "BUDGET_80_PCT"
    )
    assert (
        finops.evaluate_turn_cost_and_budget(
            model_id="gemini-3.8-flash",
            input_tokens=1000,
            output_tokens=500,
            monthly_projected_usd=9000.0,
        )["alert_triggered"]
        == "BUDGET_100_PCT"
    )

    # Topology validator violations
    topo = DeploymentTopologyValidator()
    bad_manifest = topo.verify_deployment_invariants(
        deployment_manifest={
            "environment": "prod",
            "min_tls_version": "1.2",
            "cloud_run_ingress": "INGRESS_TRAFFIC_ALL",
            "cmek_enabled": False,
            "min_instances_bff": 0,
            "min_instances_mcp": 0,
            "concurrent_sessions_supported": 100,
            "availability_slo_pct": 99.0,
            "env_vars": {"API_SECRET_KEY": "plaintext-secret"},
        }
    )
    assert bad_manifest["compliant"] is False
    assert len(bad_manifest["violations"]) >= 6


def test_tc_ut_073_engine_drift_job_and_synthetic_prober_unit_execution():
    """TC-UT-073 (FR-002, NFR-022, NFR-044): Unit test engine_drift_job (C11) and synthetic_prober (C10) functions and CLIs."""
    # Engine drift job: clean vs drifted OpenAPI spec
    clean_drift = run_drift_check()
    assert clean_drift["ok"] is True
    assert drift_main(["--env=local-staging", "--config=config/engine.yaml"]) == 0

    drifted = run_drift_check(openapi_spec={"openapi": "3.1.0", "paths": {}})
    assert drifted["ok"] is False
    assert len(drifted["openapi_breaking_changes"]) >= 1

    # Synthetic prober: healthy window, two-probe failure, and gated real-turn breach
    good_probe = execute_synthetic_probe()
    assert good_probe["ok"] is True
    assert prober_main(["--env=local-staging", "--interval=60s"]) == 0

    two_probe_fail = evaluate_slo_window(
        probe_1_passed=False,
        probe_2_after_30s_passed=False,
        probe_latency_seconds=25.0,
        real_turns=10,
        error_turns=0,
    )
    assert two_probe_fail["bad_window"] is True
    assert two_probe_fail["reason"] == "TWO_PROBE_FAILURE"

    real_turn_breach = evaluate_slo_window(
        probe_1_passed=True,
        probe_2_after_30s_passed=None,
        probe_latency_seconds=0.4,
        real_turns=10,
        error_turns=3,
    )
    assert real_turn_breach["bad_window"] is True
    assert real_turn_breach["reason"] == "GATED_REAL_TURN_BREACH"


def test_tc_ut_074_loopback_http_servers_and_cli_self_tests(tmp_path):
    """TC-UT-074 (03-deployment §2.1, ADR-022, BH-05-1-02): Exercise loopback ThreadingHTTPServer routes and --self-test CLIs across all services."""
    reset_shared_runtime_flags()
    # 1. Verify --self-test CLI mode on all HTTP services
    assert bff_main(["--self-test"]) == 0
    assert ww_main(["--self-test"]) == 0
    assert sd_main(["--self-test"]) == 0
    assert ops_main(["--self-test"]) == 0
    assert orch_main(["--self-test"]) == 0
    assert (
        run_http_server_cli(
            service_name="local-mock-saas-harness",
            default_port=18090,
            server_factory=create_upstream_http_server,
            argv=["--self-test"],
        )
        == 0
    )

    # 2. Spin up ephemeral loopback HTTP servers on port 0 and exercise GET/POST/PATCH/404 routes
    servers = [
        (
            "bff",
            create_bff_http_server(
                host="127.0.0.1", port=0, service=BffService(state_dir=str(tmp_path))
            ),
        ),
        (
            "ww",
            create_ww_http_server(
                host="127.0.0.1",
                port=0,
                server_instance=WorkWeekMcpServer(state_dir=str(tmp_path)),
            ),
        ),
        (
            "sd",
            create_sd_http_server(
                host="127.0.0.1",
                port=0,
                server_instance=ServiceDeskMcpServer(state_dir=str(tmp_path)),
            ),
        ),
        (
            "ops",
            create_ops_http_server(
                host="127.0.0.1",
                port=0,
                admin_service=HrAdminService(state_dir=str(tmp_path)),
            ),
        ),
        (
            "orch",
            create_orch_http_server(
                host="127.0.0.1",
                port=0,
                coordinator=AgentCoordinator(state_dir=str(tmp_path)),
            ),
        ),
        ("up", create_upstream_http_server(host="127.0.0.1", port=0)),
    ]
    threads = []
    urls: dict[str, str] = {}
    try:
        for name, srv in servers:
            port = srv.server_address[1]
            urls[name] = f"http://127.0.0.1:{port}"
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            threads.append(t)

        # Common healthz / version / 404 checks
        for _name, base in urls.items():
            st, body = _http_request(f"{base}/healthz")
            assert st == 200 and body["status"] == "ok"
            st_v, body_v = _http_request(f"{base}/version")
            assert st_v == 200 and "version" in body_v
            st_404, _ = _http_request(f"{base}/unknown-route-xyz")
            assert st_404 == 404

        # BFF routes (/api/v1/me, /api/v1/ui-config, /api/v1/sessions, /messages, /confirmations)
        assert _http_request(f"{urls['bff']}/api/v1/me")[0] == 200
        assert _http_request(f"{urls['bff']}/api/v1/ui-config")[0] == 200
        st_s, s_body = _http_request(
            f"{urls['bff']}/api/v1/sessions",
            method="POST",
            payload={"now_epoch": 1780000000},
        )
        assert st_s == 201
        sid = s_body["session_id"]
        st_m, m_body = _http_request(
            f"{urls['bff']}/api/v1/sessions/{sid}/messages",
            method="POST",
            payload={
                "text": "Book vacation from 2026-06-10 to 2026-06-12",
                "now_epoch": 1780000005,
            },
        )
        assert st_m == 200 and m_body["ok"] is True
        assert (
            _http_request(f"{urls['bff']}/api/v1/unknown", method="POST", payload={})[0]
            == 404
        )

        # WorkWeek & ServiceDesk MCP tool HTTP routes
        st_ww, ww_body = _http_request(
            f"{urls['ww']}/mcp/v1/tools/get_my_profile",
            method="POST",
            payload={"uct": "uct-nl-001"},
        )
        assert st_ww == 200 and ww_body["ok"] is True
        assert (
            _http_request(
                f"{urls['ww']}/mcp/v1/tools/unknown_tool", method="POST", payload={}
            )[0]
            == 404
        )

        st_sd, sd_body = _http_request(
            f"{urls['sd']}/mcp/v1/tools/get_my_ticket",
            method="POST",
            payload={"uct": "uct-nl-001", "ticket_id": "INC123456"},
        )
        assert st_sd == 200 and sd_body["ok"] is True
        assert (
            _http_request(
                f"{urls['sd']}/mcp/v1/tools/unknown_tool", method="POST", payload={}
            )[0]
            == 404
        )

        # Ops HTTP routes (/admin/v1/status, /admin/v1/kill-switch)
        assert _http_request(f"{urls['ops']}/admin/v1/status")[0] == 200
        st_ks, ks_body = _http_request(
            f"{urls['ops']}/admin/v1/kill-switch",
            method="PATCH",
            payload={
                "flag_name": "writes_enabled",
                "enabled": True,
                "reason": "test",
                "caller_sa": "hr-admin-sa",
            },
        )
        assert st_ks == 200 and ks_body["ok"] is True

        # Orchestrator HTTP routes (/v1/turn, /v1/plan/execute)
        st_tr, tr_body = _http_request(
            f"{urls['orch']}/v1/turn",
            method="POST",
            payload={
                "session_id": "s1",
                "uct": "uct-nl-001",
                "message": "What is my vacation balance?",
            },
        )
        assert st_tr == 200 and tr_body["ok"] is True

        # Upstream Mock SaaS Harness (/openapi.json)
        st_oa, oa_body = _http_request(f"{urls['up']}/openapi.json")
        assert st_oa == 200 and oa_body["openapi"] == "3.1.0"
    finally:
        for _, srv in servers:
            srv.shutdown()
            srv.server_close()


def test_tc_ut_075_shared_mcp_write_gate_and_rbac_edge_cases(persona_nl):
    """TC-UT-075 (FR-004, FR-005, FR-012, FR-018, BH-05-1-07): Exercise verify_mcp_write_gate with real ES256 JWTs, kill-switch states, and RBAC edge cases."""
    reset_shared_runtime_flags()
    signer = KmsTokenSigner()
    uct_jwt = signer.mint_uct(
        persona=persona_nl, session_id="sess-gate-1", now_epoch=1780000000
    )

    # Parse real ES256 UCT JWT via parse_uct_or_shorthand
    parsed = parse_uct_or_shorthand(
        uct_jwt, expected_audience="workweek-mcp", now_epoch=1780000010
    )
    assert parsed["valid"] is True
    assert parsed["sub"] == "EMP-NL-001"

    # Mint real ES256 CT JWT and verify through verify_mcp_write_gate
    ct_jwt = signer.mint_ct(
        sub="EMP-NL-001",
        session_id="sess-gate-1",
        plan_id="plan-gate-1",
        action_kind="SINGLE_WRITE",
        steps_hash="sha256:prev-gate",
        step_digests=[
            {
                "step_id": "step-1",
                "tool_name": "submit_my_leave_request",
                "arg_digest": "sha256:prev-gate",
            }
        ],
        preview_digest="sha256:prev-gate",
        now_epoch=1780000000,
    )
    used_cts: set[str] = set()
    lock = threading.RLock()
    gate_ok = verify_mcp_write_gate(
        signer=signer,
        claims=parsed,
        ct=ct_jwt,
        step_id="step-1",
        tool_name="submit_my_leave_request",
        canonical_args={"leave_type": "VACATION"},
        preview_digest="sha256:prev-gate",
        expected_preview_digest="sha256:prev-gate",
        validated_previews={},
        used_cts=used_cts,
        lock=lock,
    )
    assert gate_ok["ok"] is True

    # Replay same single-use CT JWT -> rejected with INVALID_CONFIRMATION_TOKEN
    gate_replay = verify_mcp_write_gate(
        signer=signer,
        claims=parsed,
        ct=ct_jwt,
        step_id="step-1",
        tool_name="submit_my_leave_request",
        canonical_args={"leave_type": "VACATION"},
        preview_digest="sha256:prev-gate",
        expected_preview_digest="sha256:prev-gate",
        validated_previews={},
        used_cts=used_cts,
        lock=lock,
    )
    assert gate_replay["ok"] is False
    assert gate_replay["error_code"] == ErrorCode.INVALID_CONFIRMATION_TOKEN

    # OutputSafetyGate degraded mode, abstention, and BSN/IBAN redaction branches
    out_gate = OutputSafetyGate()
    deg = out_gate.evaluate_and_stamp_output(
        session_id="s1",
        turn_id="t1",
        candidate_text="Hello",
        grounding_verdict={"passed": True, "score": 0.95},
        is_degraded_safety_mode=True,
    )
    assert deg["verdict"] == "BLOCKED_OR_REDACTED" and deg["release_gate_stamp"] is None
    abst = out_gate.evaluate_and_stamp_output(
        session_id="s1",
        turn_id="t1",
        candidate_text="Unverified claim",
        grounding_verdict={"passed": False, "score": 0.50},
    )
    assert abst["verdict"] == "ABSTAIN"
    red = out_gate.evaluate_and_stamp_output(
        session_id="s1",
        turn_id="t1",
        candidate_text="BSN 123456789 IBAN NL91ABNA0417164300",
        grounding_verdict={"passed": True, "score": 0.95},
    )
    assert red["verdict"] == "BLOCKED_OR_REDACTED"
    assert (
        "[REDACTED-BSN]" in red["safe_text"] and "[REDACTED-IBAN]" in red["safe_text"]
    )

    # TelemetrySanitizer all-switch violations, Bearer JWT detection, missing audit fields, and retention violations
    ts = TelemetrySanitizer()
    bad_otel = ts.validate_otel_config(
        {
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "SPAN_AND_EVENT",
            "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "true",
            "GENAI_COMPLETION_HOOKS_ENABLED": "true",
            "BQ_AGENT_ANALYTICS_RAW_LOGGING": "true",
            "LOG_LEVEL": "TRACE",
            "MODEL_ARMOR_PLATFORM_LOGGING": "true",
        }
    )
    assert (
        bad_otel["content_capture_safe"] is False and len(bad_otel["violations"]) == 6
    )
    spii_jwt = ts.inspect_telemetry_payload_for_spii(
        "Header: Bearer eyJhbGciOiJFUzI1NiJ9.payload.sig", []
    )
    assert (
        spii_jwt["spii_detected"] is True and "Bearer_JWT" in spii_jwt["matched_tokens"]
    )
    bad_audit = ts.build_and_emit_audit_event({"event_type": "TEST"})
    assert (
        bad_audit["ok"] is False
        and bad_audit["error_code"] == ErrorCode.VALIDATION_FAILED
    )
    bad_ret = ts.verify_retention_windows(
        config={
            "session_working_memory_seconds": 90000,
            "redacted_transcript_days": 120,
            "operational_logs_days": 45,
            "idempotency_records_seconds": 200000,
            "plan_journal_days": 120,
            "audit_logs_days": 300,
        }
    )
    assert bad_ret["compliant"] is False and len(bad_ret["violations"]) == 6

    # RbacAuthorizer & RegionAccessGuard edge branches
    rbac = RbacAuthorizer()
    assert (
        rbac.authorize_action(
            roles=["employee"],
            route_or_action="GET /api/v1/me",
            caller_employee_id="EMP-NL-001",
            target_employee_id="EMP-DE-002",
        )["error_code"]
        == ErrorCode.CROSS_USER_ACCESS_DENIED
    )
    assert (
        rbac.authorize_action(
            roles=["employee"], route_or_action="GET /admin/v1/status"
        )["allowed"]
        is False
    )
    assert (
        rbac.authorize_action(
            roles=["kb_admin"], route_or_action="POST /internal/kb-sync"
        )["allowed"]
        is True
    )
    assert (
        rbac.authorize_action(
            roles=["employee"], route_or_action="POST /internal/kb-sync"
        )["allowed"]
        is False
    )
    assert (
        rbac.authorize_action(roles=["auditor"], route_or_action="POST /unknown/route")[
            "allowed"
        ]
        is False
    )

    rg = RegionAccessGuard()
    assert rg._is_ip_in_cidrs("invalid-ip", ["10.0.0.0/8"]) is False
    assert rg._is_ip_in_cidrs("10.1.2.3", ["invalid-cidr", "10.0.0.0/8"]) is True

    # BffService confirmation branches (UC-1.3b, partial failure, writes_disabled, rate limits)
    bff = BffService()
    hdr_nl = {"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"}
    uc13b = bff.resolve_confirmation(
        headers=hdr_nl,
        client_ip="127.0.0.1",
        session_id="sess-e2e-007",
        confirmation_id="conf-comment-and-close-inc123456",
        decision="confirm",
        now_epoch=1780000010,
    )
    assert uc13b["ok"] is True and uc13b["ticket_status"] == "Closed"

    pfail = bff.resolve_confirmation(
        headers=hdr_nl,
        client_ip="127.0.0.1",
        session_id="sess-e2e-010-fail",
        confirmation_id="conf-saga-partial-fail",
        decision="confirm",
        now_epoch=1780000010,
    )
    assert (
        pfail["ok"] is False and pfail["plan_state"] == ActionPlanState.PARTIAL_FAILURE
    )

    rl_min = bff.check_rate_limit(
        employee_id="EMP-NL-001",
        is_write=False,
        messages_last_minute=21,
        messages_today=21,
        writes_last_minute=0,
    )
    assert rl_min["allowed"] is False and rl_min["error_code"] == ErrorCode.RATE_LIMITED
    rl_day = bff.check_rate_limit(
        employee_id="EMP-NL-001",
        is_write=False,
        messages_last_minute=5,
        messages_today=301,
        writes_last_minute=0,
    )
    assert rl_day["allowed"] is False and rl_day["error_code"] == ErrorCode.RATE_LIMITED
    rl_wr = bff.check_rate_limit(
        employee_id="EMP-NL-001",
        is_write=True,
        messages_last_minute=5,
        messages_today=20,
        writes_last_minute=6,
    )
    assert rl_wr["allowed"] is False and rl_wr["error_code"] == ErrorCode.RATE_LIMITED
    rl_wr_ok = bff.check_rate_limit(
        employee_id="EMP-NL-001",
        is_write=True,
        messages_last_minute=5,
        messages_today=20,
        writes_last_minute=5,
    )
    assert rl_wr_ok["allowed"] is True
    rl_msg_ok = bff.check_rate_limit(
        employee_id="EMP-NL-001",
        is_write=False,
        messages_last_minute=20,
        messages_today=300,
        writes_last_minute=0,
    )
    assert rl_msg_ok["allowed"] is True
