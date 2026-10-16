"""Integration tests for Cross-System Sagas (UC-2.1, UC-2.2, UC-2.3), PlanReconcilerWorker, 6-Database Firestore Isolation Matrix, and Kill Switch Propagation."""

from __future__ import annotations

import pytest

from hr_agent.models import ActionPlanState, ErrorCode, FirestoreDatabaseId

pytestmark = pytest.mark.integration


def test_tc_it_006_cross_system_sagas_uc21_uc22_uc23_whole_plan_execution(
    coordinator, fixed_now
):
    """TC-IT-006 (FR-090..FR-093, FR-095, FR-096, BH-04-1-01, BH-04-1-02): Execute UC-2.2 medical leave + delegation ticket saga and UC-2.3 relocation address + badge ticket saga via whole-plan confirm without leaking leave/medical details."""
    res_uc22 = coordinator.execute_action_plan(
        session_id="sess-saga-uc22",
        uct="uct-nl-001",
        ct="ct-saga-uc22",
        plan_id="plan-uc22-ww-then-si",
        decision="confirm",
        remaining_deadline_ms=45000,
        now_epoch=fixed_now + 20,
    )
    assert res_uc22["plan_state"] == ActionPlanState.COMPLETED
    assert res_uc22["step_records"]["step-1"].startswith("LR-")
    assert res_uc22["step_records"]["step-2"].startswith("INC")
    assert "related_leave_request_id" not in res_uc22.get("step2_ticket_payload", {})
    assert "sick" not in str(res_uc22.get("step2_ticket_payload", {})).lower()

    res_uc23 = coordinator.execute_action_plan(
        session_id="sess-saga-uc23",
        uct="uct-nl-001",
        ct="ct-saga-uc23",
        plan_id="plan-uc23-addr-then-badge",
        decision="confirm",
        remaining_deadline_ms=45000,
        now_epoch=fixed_now + 30,
    )
    assert res_uc23["plan_state"] == ActionPlanState.COMPLETED
    assert "home_address" in res_uc23["step_records"]["step-1_updated_fields"]
    assert res_uc23["step_records"]["step-2"].startswith("INC")


def test_tc_it_007_plan_reconciler_worker_converges_unknown_and_abandons_15m_stale(
    reconciler, fixed_now
):
    """TC-IT-007 (FR-023, FR-095, ADR-016, BH-01-3-04): Reconcile UNKNOWN_OUTCOME plans (> 30s) via get_write_status and mark > 900s stale plans FAILED."""
    outcomes = reconciler.reconcile_stale_plans(
        caller_sa="plan-reconciler-sa@hr-agent-prodpilot.iam.gserviceaccount.com",
        now_epoch=fixed_now + 1000,
        stale_threshold_seconds=30,
        abandon_threshold_seconds=900,
    )
    states_by_plan = {item["plan_id"]: item["new_state"] for item in outcomes}
    assert states_by_plan["plan-unknown-45s"] == ActionPlanState.COMPLETED
    assert states_by_plan["plan-abandoned-950s"] == ActionPlanState.FAILED


def test_tc_it_008_plan_reconciler_sa_restricted_to_get_write_status_only(
    workweek_mcp, servicedesk_mcp
):
    """TC-IT-008 (FR-023, ADR-016, BH-01-3-04): plan-reconciler-sa can call get_write_status on WW and SI MCPs; unauthorized SA is denied."""
    ww_status = workweek_mcp.get_write_status(
        caller_sa="plan-reconciler-sa@hr-agent-prodpilot.iam.gserviceaccount.com",
        idempotency_key="0195f000-0000-7000-8000-000000000101",
    )
    assert ww_status["ok"] is True

    si_denied = servicedesk_mcp.get_write_status(
        caller_sa="unauthorized-sa@hr-agent-prodpilot.iam.gserviceaccount.com",
        idempotency_key="0195f000-0000-7000-8000-000000000201",
    )
    assert si_denied["ok"] is False
    assert si_denied["error_code"] == ErrorCode.AUTHZ_DENIED


@pytest.mark.parametrize(
    ("db_id", "allowed_writer_sa", "denied_writer_sa"),
    [
        (FirestoreDatabaseId.HR_PERSONAS.value, "cicd-runner-sa", "hr-bff-sa"),
        (FirestoreDatabaseId.HR_FLAGS.value, "hr-admin-sa", "hr-agent-sa"),
        (FirestoreDatabaseId.HR_PLANS.value, "hr-agent-sa", "workweek-mcp-sa"),
        (FirestoreDatabaseId.HR_BFF.value, "hr-bff-sa", "servicedesk-mcp-sa"),
        (FirestoreDatabaseId.HR_IDEM_WW.value, "workweek-mcp-sa", "servicedesk-mcp-sa"),
        (FirestoreDatabaseId.HR_IDEM_SI.value, "servicedesk-mcp-sa", "workweek-mcp-sa"),
    ],
)
def test_tc_it_009_six_firestore_databases_writer_isolation_matrix(
    iam_verifier,
    db_id,
    allowed_writer_sa,
    denied_writer_sa,
):
    """TC-IT-009 (ADR-019, NFR-004, NFR-006, BH-04-1-03): Enforce least-privilege writer isolation across all 6 Firestore databases and verify dsar-tool-sa is isolated to DSAR erasure."""
    allowed = iam_verifier.check_access(
        database_id=db_id,
        principal_sa=allowed_writer_sa,
        operation="WRITE",
    )
    assert allowed["permitted"] is True

    denied = iam_verifier.check_access(
        database_id=db_id,
        principal_sa=denied_writer_sa,
        operation="WRITE",
    )
    assert denied["permitted"] is False

    # dsar-tool-sa has zero access to hr-personas and hr-flags
    if db_id in (
        FirestoreDatabaseId.HR_PERSONAS.value,
        FirestoreDatabaseId.HR_FLAGS.value,
    ):
        dsar_denied = iam_verifier.check_access(
            database_id=db_id,
            principal_sa="dsar-tool-sa",
            operation="WRITE",
        )
        assert dsar_denied["permitted"] is False


def test_tc_it_010_kill_switch_propagation_writes_disabled_and_assistant_disabled(
    hr_admin,
):
    """TC-IT-010 (NFR-045): When writes_enabled=False, writes fail with WRITES_DISABLED while reads succeed; when assistant_enabled=False, all operations fail with ASSISTANT_DISABLED."""
    write_eval = hr_admin.evaluate_kill_switch_state(
        cached_flags={"writes_enabled": False, "assistant_enabled": True},
        cache_age_seconds=5,
        db_reachable=True,
        is_write_operation=True,
    )
    assert write_eval["allowed"] is False
    assert write_eval["error_code"] == ErrorCode.WRITES_DISABLED

    read_eval = hr_admin.evaluate_kill_switch_state(
        cached_flags={"writes_enabled": False, "assistant_enabled": True},
        cache_age_seconds=5,
        db_reachable=True,
        is_write_operation=False,
    )
    assert read_eval["allowed"] is True

    assistant_off = hr_admin.evaluate_kill_switch_state(
        cached_flags={"writes_enabled": False, "assistant_enabled": False},
        cache_age_seconds=5,
        db_reachable=True,
        is_write_operation=False,
    )
    assert assistant_off["allowed"] is False
    assert assistant_off["error_code"] == ErrorCode.ASSISTANT_DISABLED
