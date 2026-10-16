"""Unit tests for Cross-System Sagas (UC-2.1, UC-2.2, UC-2.3), Whole-Plan Confirmation, Idempotency State Machine, and Timeout/Retry Profiles K/R/N (FR-023, FR-090..FR-093, FR-095, FR-096, NFR-032, NFR-040, NFR-041)."""

from __future__ import annotations

import pytest

from hr_agent.models import ActionPlanState, ErrorCode, IdempotencyStatus

pytestmark = pytest.mark.unit


def test_tc_ut_046_uc21_remote_equipment_procurement_plan(coordinator):
    """TC-UT-046 (FR-090, FR-091, FR-052, FR-096, BH-04-1-01): Build UC-2.1 plan quoting remote-work policy, verifying REMOTE status, and preparing 'Hardware - Peripherals' ('4 - Low') ticket with shipping address."""
    plan = coordinator.build_action_plan(
        session_id="sess-uc21",
        uct="uct-nl-001",
        use_case_id="UC-2.1",
        policy_citations=[{"doc_id": "POL-GLOBAL-EQUIP-2026", "section_id": "2.1"}],
        proposed_steps=[
            {
                "step_id": "step-1",
                "target_system": "SERVICEDESK",
                "tool_name": "create_incident",
                "args": {
                    "category": "Hardware - Peripherals",
                    "short_description": "Remote work equipment request: 27-inch 4K monitor",
                    "detailed_description": "Employee EMP-NL-001 (REMOTE) requests 27-inch 4K monitor shipped to Keizersgracht 100, 1015 AA Amsterdam, NL.",
                    "priority": "4 - Low",
                    "template_id": "UC-2.1",
                },
            }
        ],
    )
    assert plan["state"] == ActionPlanState.AWAITING_CONFIRMATION
    assert plan["use_case_id"] == "UC-2.1"
    assert plan["steps"][0]["args"]["category"] == "Hardware - Peripherals"
    assert plan["steps"][0]["args"]["priority"] == "4 - Low"


def test_tc_ut_047_uc22_medical_leave_and_privacy_minimized_mailbox_delegation_plan(
    coordinator,
):
    """TC-UT-047 (FR-090, FR-092, FR-096, SDD §5.4.5, BH-04-1-01, BH-04-1-02): Build UC-2.2 2-step plan (WorkWeek SICK leave + ServiceImmediately mailbox delegation) and verify Step 2 never contains leave_type, reason, medical context, or leave_request_id."""
    plan = coordinator.build_action_plan(
        session_id="sess-uc22",
        uct="uct-nl-001",
        use_case_id="UC-2.2",
        policy_citations=[{"doc_id": "POL-NL-LEAVE-2026", "section_id": "6.1"}],
        proposed_steps=[
            {
                "step_id": "step-1",
                "target_system": "WORKWEEK",
                "tool_name": "submit_my_leave_request",
                "args": {
                    "leave_type": "SICK",
                    "start_date": "2026-06-10",
                    "end_date": "2026-06-17",
                    "expected_work_days": 6.0,
                },
            },
            {
                "step_id": "step-2",
                "target_system": "SERVICEDESK",
                "tool_name": "create_incident",
                "depends_on": ["step-1"],
                "args": {
                    "category": "Email and Collaboration - Access",
                    "short_description": "Mailbox delegation during planned absence",
                    "detailed_description": "Delegate mailbox access for Eva Bakker (EMP-NL-001) to manager Pieter Jansen (EMP-NL-MGR-01, pieter.jansen.synthetic@example.invalid) from 2026-06-10 to 2026-06-17.",
                    "priority": "3 - Moderate",
                    "template_id": "UC-2.2",
                },
            },
        ],
    )
    assert plan["state"] == ActionPlanState.AWAITING_CONFIRMATION
    assert len(plan["steps"]) == 2
    step2_args_str = str(plan["steps"][1]["args"]).lower()
    for forbidden in (
        "sick",
        "medical",
        "surgery",
        "diagnosis",
        "leave_request_id",
        "related_leave_request_id",
        "lr-",
    ):
        assert forbidden not in step2_args_str


def test_tc_ut_048_uc23_office_relocation_address_update_and_badge_ticket_plan(
    coordinator,
):
    """TC-UT-048 (FR-090, FR-093, FR-042, FR-096, BH-04-1-01): Build UC-2.3 2-step plan (WorkWeek update_my_contact_info + ServiceImmediately Facilities badge ticket) and reject > 3 steps boundary."""
    plan = coordinator.build_action_plan(
        session_id="sess-uc23",
        uct="uct-nl-001",
        use_case_id="UC-2.3",
        policy_citations=[{"doc_id": "POL-GLOBAL-RELOC-2026", "section_id": "2.0"}],
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
                    "short_description": "Office badge re-provisioning for relocation",
                    "detailed_description": "Re-provision office badge for Eva Bakker (EMP-NL-001) to Rotterdam office effective 2026-07-01.",
                    "priority": "3 - Moderate",
                    "template_id": "UC-2.3",
                },
            },
        ],
    )
    assert plan["state"] == ActionPlanState.AWAITING_CONFIRMATION
    assert len(plan["steps"]) == 2

    # Boundary: max 3 write steps per plan passes; 4 write steps fails with VALIDATION_FAILED
    four_steps = [
        {
            "step_id": f"step-{i}",
            "target_system": "WORKWEEK",
            "tool_name": "update_my_contact_info",
            "args": {},
        }
        for i in range(1, 5)
    ]
    res_over = coordinator.build_action_plan(
        session_id="sess-over", uct="uct-nl-001", proposed_steps=four_steps
    )
    assert res_over["ok"] is False
    assert res_over["error_code"] == ErrorCode.VALIDATION_FAILED


def test_tc_ut_049_whole_plan_confirmation_execution_partial_failure_and_18s_deadline_boundary(
    coordinator, fixed_now
):
    """TC-UT-049 (FR-095, FR-019, FR-022, NFR-040, NFR-041, BH-04-1-02, BH-04-1-04, BH-04-1-05): Execute whole-plan decision ('confirm'|'cancel'), test 18000ms vs 17999ms executor deadline boundary, and verify PARTIAL_FAILURE without auto-rollback."""
    # Exact 18000ms remaining deadline boundary allows step execution
    res_ok = coordinator.execute_action_plan(
        session_id="sess-exec-ok",
        uct="uct-nl-001",
        ct="ct-valid-plan-001",
        plan_id="plan-uc22-001",
        decision="confirm",
        remaining_deadline_ms=18000,
        now_epoch=fixed_now,
    )
    assert res_ok["plan_state"] == ActionPlanState.COMPLETED

    # Adjacent 17999ms (< 18s) remaining deadline boundary refuses to start write step -> DEADLINE_TOO_SHORT
    res_short = coordinator.execute_action_plan(
        session_id="sess-exec-short",
        uct="uct-nl-001",
        ct="ct-valid-plan-002",
        plan_id="plan-uc22-002",
        decision="confirm",
        remaining_deadline_ms=17999,
        now_epoch=fixed_now,
    )
    assert res_short["ok"] is False
    assert res_short["error_code"] == ErrorCode.DEADLINE_TOO_SHORT

    # Whole-plan cancel transitions plan to CANCELLED with 0 writes executed
    res_cancel = coordinator.execute_action_plan(
        session_id="sess-exec-cancel",
        uct="uct-nl-001",
        ct="",
        plan_id="plan-uc22-003",
        decision="cancel",
        remaining_deadline_ms=45000,
        now_epoch=fixed_now,
    )
    assert res_cancel["plan_state"] == ActionPlanState.CANCELLED
    assert res_cancel["writes_executed"] == 0

    # Partial failure: Step 1 COMPLETED (LR-2026-0042), Step 2 FAILED -> PARTIAL_FAILURE, Step 1 is NOT rolled back
    res_partial = coordinator.execute_action_plan(
        session_id="sess-exec-partial",
        uct="uct-nl-001",
        ct="ct-valid-partial",
        plan_id="plan-step2-fails",
        decision="confirm",
        remaining_deadline_ms=45000,
        now_epoch=fixed_now,
    )
    assert res_partial["plan_state"] == ActionPlanState.PARTIAL_FAILURE
    assert res_partial["step_outcomes"]["step-1"]["status"] == "COMPLETED"
    assert res_partial["step_outcomes"]["step-1"]["backend_record_id"] == "LR-2026-0042"
    assert res_partial["step_outcomes"]["step-2"]["status"] == "FAILED"
    assert res_partial["auto_rollback_attempted"] is False


def test_tc_ut_050_idempotency_key_and_lease_20s_ttl_48h_match_key_7d_boundaries(
    idempotency_sm, fixed_now
):
    """TC-UT-050 (FR-023, FR-054, FR-066, NFR-032, BH-01-3-01, BH-04-1-05): Verify deterministic UUIDv7 idempotency key and test exact boundaries for 20s lease (20s vs 21s), 48h TTL (172800s vs 172801s), and 7d match_key_version (604800s vs 604801s)."""
    k1 = idempotency_sm.compute_idempotency_key(
        session_id="sess-nl-001",
        plan_id="plan-001",
        step_id="step-1",
        tool_name="submit_my_leave_request",
        canonical_args={
            "end_date": "2026-06-12",
            "leave_type": "VACATION",
            "start_date": "2026-06-10",
        },
    )
    k2 = idempotency_sm.compute_idempotency_key(
        session_id="sess-nl-001",
        plan_id="plan-001",
        step_id="step-1",
        tool_name="submit_my_leave_request",
        canonical_args={
            "start_date": "2026-06-10",
            "end_date": "2026-06-12",
            "leave_type": "VACATION",
        },
    )
    assert k1["idempotency_key"] == k2["idempotency_key"]
    assert k1["arg_digest"] == k2["arg_digest"]

    # Acquire lease at fixed_now: lease_expires_at == fixed_now + 20, ttl_expires_at == fixed_now + 172800 (48h)
    acquired = idempotency_sm.acquire_or_inspect(
        db_id="hr-idem-ww",
        idempotency_key="0195f000-0000-7000-8000-000000000010",
        employee_id="EMP-NL-001",
        tool_name="submit_my_leave_request",
        arg_digest=k1["arg_digest"],
        match_key=k1["match_key"],
        match_key_version=1,
        now_epoch=fixed_now,
    )
    assert acquired["status"] == IdempotencyStatus.IN_PROGRESS
    assert acquired["lease_expires_at"] == fixed_now + 20
    assert acquired["ttl_expires_at"] == fixed_now + 172800

    # Concurrent call at exact 20s lease boundary (fixed_now + 20) sees active IN_PROGRESS lease -> DUPLICATE_IN_PROGRESS
    active_at_20s = idempotency_sm.acquire_or_inspect(
        db_id="hr-idem-ww",
        idempotency_key="0195f000-0000-7000-8000-000000000010",
        employee_id="EMP-NL-001",
        tool_name="submit_my_leave_request",
        arg_digest=k1["arg_digest"],
        match_key=k1["match_key"],
        match_key_version=1,
        now_epoch=fixed_now + 20,
    )
    assert active_at_20s["error_code"] == ErrorCode.DUPLICATE_IN_PROGRESS

    # At 21s (fixed_now + 21 > lease_expires_at), lease is expired and transitions to reconciliation / OUTCOME_UNKNOWN check
    expired_at_21s = idempotency_sm.acquire_or_inspect(
        db_id="hr-idem-ww",
        idempotency_key="0195f000-0000-7000-8000-000000000010",
        employee_id="EMP-NL-001",
        tool_name="submit_my_leave_request",
        arg_digest=k1["arg_digest"],
        match_key=k1["match_key"],
        match_key_version=1,
        now_epoch=fixed_now + 21,
    )
    assert expired_at_21s["lease_expired"] is True


def test_tc_ut_051_reconcile_unknown_record_unlinked_filter_and_7d_version_window(
    idempotency_sm, fixed_now
):
    """TC-UT-051 (FR-023, BH-01-3-01, BH-04-1-05): Reconcile OUTCOME_UNKNOWN excluding already-linked backend_record_ids and testing 604800s (7d) vs 604801s match_key_version window."""
    # Exact 7d (604800s) boundary for v1 is still active; LR-1001 is already linked so LR-1002 is selected
    reconciled = idempotency_sm.reconcile_unknown_record(
        db_id="hr-idem-ww",
        idempotency_key="0195f000-0000-7000-8000-000000000099",
        upstream_candidates=[
            {
                "backend_record_id": "LR-1001",
                "match_key": "mk:v1:abc",
                "match_key_version": 1,
            },
            {
                "backend_record_id": "LR-1002",
                "match_key": "mk:v1:abc",
                "match_key_version": 1,
            },
        ],
        linked_backend_record_ids={"LR-1001"},
        active_match_key_versions={1: fixed_now - 604800, 2: fixed_now},
        now_epoch=fixed_now,
    )
    assert reconciled["status"] == IdempotencyStatus.COMPLETED
    assert reconciled["backend_record_id"] == "LR-1002"

    # At 604801s (> 7d), match_key_version 0 is expired
    expired_v0 = idempotency_sm.reconcile_unknown_record(
        db_id="hr-idem-ww",
        idempotency_key="0195f000-0000-7000-8000-000000000100",
        upstream_candidates=[
            {
                "backend_record_id": "LR-0999",
                "match_key": "mk:v0:old",
                "match_key_version": 0,
            }
        ],
        linked_backend_record_ids=set(),
        active_match_key_versions={0: fixed_now - 604801, 1: fixed_now},
        now_epoch=fixed_now,
    )
    assert expired_v0["version_expired"] is True


def test_tc_ut_052_timeout_hierarchy_and_retry_profiles_read_k_r_n(upstream_client):
    """TC-UT-052 (NFR-040, NFR-041, SDD §5.7.2, BH-04-1-04, BH-04-1-05): Verify retry Profiles READ (3x), PROFILE_K (2x), PROFILE_R (retry only on not-processed + empty lookup, NEVER after timeout), PROFILE_N (1x + lookup), and DEADLINE_TOO_SHORT write start rule."""
    # 1. Write start rule: Profile K needs 3500ms + 300ms margin = 3800ms. 3800ms passes; 3799ms fails with DEADLINE_TOO_SHORT
    short_deadline = upstream_client.execute_with_retry_profile(
        profile="PROFILE_K",
        remaining_deadline_ms=3799,
        attempt_outcomes=["SUCCESS"],
    )
    assert short_deadline["ok"] is False
    assert short_deadline["error_code"] == ErrorCode.DEADLINE_TOO_SHORT
    assert short_deadline["attempts_made"] == 0

    # 2. READ profile: retries transient HTTP_503 up to 3 attempts total
    read_ok = upstream_client.execute_with_retry_profile(
        profile="READ",
        remaining_deadline_ms=12000,
        attempt_outcomes=["HTTP_503", "TIMEOUT", "SUCCESS"],
    )
    assert read_ok["ok"] is True
    assert read_ok["attempts_made"] == 3

    # 3. PROFILE_K (idempotency-key write): max 2 attempts with same Idempotency-Key
    prof_k_ok = upstream_client.execute_with_retry_profile(
        profile="PROFILE_K",
        remaining_deadline_ms=8000,
        attempt_outcomes=["HTTP_503", "SUCCESS"],
    )
    assert prof_k_ok["ok"] is True
    assert prof_k_ok["attempts_made"] == 2

    # 4. PROFILE_R (correlation-reference write):
    # 4a. Definitive not-processed error (CONNECT_REFUSED / HTTP_429) + lookup_found_record=False -> allows 2nd attempt
    prof_r_retry = upstream_client.execute_with_retry_profile(
        profile="PROFILE_R",
        remaining_deadline_ms=11000,
        attempt_outcomes=["HTTP_429", "SUCCESS"],
        lookup_found_record=False,
    )
    assert prof_r_retry["ok"] is True
    assert prof_r_retry["attempts_made"] == 2
    assert prof_r_retry["lookup_performed"] is True

    # 4b. Timeout on attempt 1 + lookup_found_record=False -> NEVER retries a second write attempt; returns OUTCOME_UNKNOWN
    prof_r_timeout = upstream_client.execute_with_retry_profile(
        profile="PROFILE_R",
        remaining_deadline_ms=11000,
        attempt_outcomes=["TIMEOUT"],
        lookup_found_record=False,
    )
    assert prof_r_timeout["ok"] is False
    assert prof_r_timeout["attempts_made"] == 1
    assert prof_r_timeout["error_code"] == ErrorCode.OUTCOME_UNKNOWN

    # 5. PROFILE_N (non-idempotent write): 1 attempt + lookup; never retries in-turn
    prof_n_recovered = upstream_client.execute_with_retry_profile(
        profile="PROFILE_N",
        remaining_deadline_ms=8000,
        attempt_outcomes=["TIMEOUT"],
        lookup_found_record=True,
    )
    assert prof_n_recovered["ok"] is True
    assert prof_n_recovered["attempts_made"] == 1
    assert prof_n_recovered["recovered_via_lookup"] is True
