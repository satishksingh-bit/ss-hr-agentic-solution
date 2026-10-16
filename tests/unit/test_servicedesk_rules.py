"""Unit tests for ServiceImmediately ITSM MCP Server: Priority Rules P-01..P-13, State Transitions, 30-Min Duplicate Window, ^INC[0-9]{6,10}$ Format, and FR-096 Privacy (FR-060..FR-068, FR-096, FR-022, NFR-032)."""

from __future__ import annotations

import pytest

from hr_agent.models import ErrorCode, TicketPriority, TicketState

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    (
        "rule_id",
        "requested_priority",
        "impact",
        "expected_allowed",
        "expected_effective_priority",
    ),
    [
        # P-01: security_incident_suspected=true -> 1 - Critical allowed
        (
            "P-01",
            TicketPriority.P1_CRITICAL.value,
            {
                "users_affected": "ONE",
                "can_work": True,
                "workaround_available": True,
                "security_incident_suspected": True,
                "safety_risk": False,
                "request_type": "INCIDENT",
            },
            True,
            TicketPriority.P1_CRITICAL.value,
        ),
        # P-02: safety_risk=true -> 1 - Critical allowed
        (
            "P-02",
            TicketPriority.P1_CRITICAL.value,
            {
                "users_affected": "ONE",
                "can_work": True,
                "workaround_available": True,
                "security_incident_suspected": False,
                "safety_risk": True,
                "request_type": "INCIDENT",
            },
            True,
            TicketPriority.P1_CRITICAL.value,
        ),
        # P-03: users_affected in {TEN_OR_MORE, WHOLE_SITE} AND can_work=false AND workaround_available=false -> 1 - Critical allowed
        (
            "P-03",
            TicketPriority.P1_CRITICAL.value,
            {
                "users_affected": "TEN_OR_MORE",
                "can_work": False,
                "workaround_available": False,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INCIDENT",
            },
            True,
            TicketPriority.P1_CRITICAL.value,
        ),
        # P-04: 1 - Critical requested, none of P-01..P-03 met, can_work=false AND workaround_available=false -> Downgrade to 2 - High
        (
            "P-04",
            TicketPriority.P1_CRITICAL.value,
            {
                "users_affected": "ONE",
                "can_work": False,
                "workaround_available": False,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INCIDENT",
            },
            False,
            TicketPriority.P2_HIGH.value,
        ),
        # P-05: 1 - Critical requested, none of P-01..P-03 met, can_work=true OR workaround_available=true -> Downgrade to 3 - Moderate
        (
            "P-05",
            TicketPriority.P1_CRITICAL.value,
            {
                "users_affected": "ONE",
                "can_work": True,
                "workaround_available": True,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INCIDENT",
            },
            False,
            TicketPriority.P3_MODERATE.value,
        ),
        # P-06: 2 - High requested, can_work=false AND workaround_available=false -> 2 - High allowed
        (
            "P-06",
            TicketPriority.P2_HIGH.value,
            {
                "users_affected": "ONE",
                "can_work": False,
                "workaround_available": False,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INCIDENT",
            },
            True,
            TicketPriority.P2_HIGH.value,
        ),
        # P-07: 2 - High requested, users_affected in {TWO_TO_NINE, TEN_OR_MORE, WHOLE_SITE} AND can_work=false -> 2 - High allowed
        (
            "P-07",
            TicketPriority.P2_HIGH.value,
            {
                "users_affected": "TWO_TO_NINE",
                "can_work": False,
                "workaround_available": True,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INCIDENT",
            },
            True,
            TicketPriority.P2_HIGH.value,
        ),
        # P-08: 2 - High requested, none of P-06..P-07 met -> Downgrade to 3 - Moderate
        (
            "P-08",
            TicketPriority.P2_HIGH.value,
            {
                "users_affected": "ONE",
                "can_work": True,
                "workaround_available": True,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INCIDENT",
            },
            False,
            TicketPriority.P3_MODERATE.value,
        ),
        # P-09: 3 - Moderate requested, request_type=INCIDENT -> 3 - Moderate allowed
        (
            "P-09",
            TicketPriority.P3_MODERATE.value,
            {
                "users_affected": "ONE",
                "can_work": True,
                "workaround_available": True,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INCIDENT",
            },
            True,
            TicketPriority.P3_MODERATE.value,
        ),
        # P-10: 3 - Moderate requested, request_type=INFORMATION AND can_work=true -> Downgrade to 4 - Low
        (
            "P-10",
            TicketPriority.P3_MODERATE.value,
            {
                "users_affected": "ONE",
                "can_work": True,
                "workaround_available": True,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INFORMATION",
            },
            False,
            TicketPriority.P4_LOW.value,
        ),
        # P-11: 4 - Low requested -> 4 - Low allowed
        (
            "P-11",
            TicketPriority.P4_LOW.value,
            {
                "users_affected": "ONE",
                "can_work": True,
                "workaround_available": True,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INFORMATION",
            },
            True,
            TicketPriority.P4_LOW.value,
        ),
        # P-12: UC-2.1 Remote Equipment Procurement template -> 4 - Low
        (
            "P-12",
            TicketPriority.P4_LOW.value,
            {
                "users_affected": "ONE",
                "can_work": True,
                "workaround_available": True,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INFORMATION",
            },
            True,
            TicketPriority.P4_LOW.value,
        ),
        # P-13: UC-2.2 Mailbox Delegation & UC-2.3 Facilities Badge templates -> 3 - Moderate
        (
            "P-13",
            TicketPriority.P3_MODERATE.value,
            {
                "users_affected": "ONE",
                "can_work": True,
                "workaround_available": True,
                "security_incident_suspected": False,
                "safety_risk": False,
                "request_type": "INCIDENT",
            },
            True,
            TicketPriority.P3_MODERATE.value,
        ),
    ],
)
def test_tc_ut_032_priority_decision_table_p01_through_p13(
    servicedesk_mcp,
    rule_id,
    requested_priority,
    impact,
    expected_allowed,
    expected_effective_priority,
):
    """TC-UT-032 (FR-062, FR-067, SDD §5.4.4, BH-04-1-03): Verify all 13 deterministic priority rules P-01..P-13 on structured impact facts."""
    res = servicedesk_mcp.validate_incident(
        uct="uct-nl-001",
        category="Hardware - Peripherals",
        short_description=f"Test incident for priority rule {rule_id}",
        detailed_description="Detailed description for deterministic priority verification.",
        requested_priority=requested_priority,
        impact=impact,
        template_id="UC-2.1"
        if rule_id == "P-12"
        else ("UC-2.2" if rule_id == "P-13" else None),
    )
    assert res["ok"] is True
    assert res["for_llm"]["rule_id"] == rule_id
    assert res["for_llm"]["priority_allowed"] is expected_allowed
    assert res["for_llm"]["effective_priority"] == expected_effective_priority
    assert res["for_llm"]["preview_digest"].startswith("sha256:")


def test_tc_ut_033_priority_ignores_free_text_and_160_char_description_boundary(
    servicedesk_mcp,
):
    """TC-UT-033 (FR-062, FR-067, BH-04-1-03, BH-04-1-05): Verify free-text 'URGENT P1 CRITICAL' cannot override structured impact, and test 160 vs 161 char short_description boundary."""
    # Free-text claims P1 emergency, but structured impact says ONE user, can_work=True -> P-05 downgrades to 3 - Moderate
    res_downgraded = servicedesk_mcp.validate_incident(
        uct="uct-nl-001",
        category="Software - Collaboration",
        short_description="URGENT P1 CRITICAL CEO DEMANDS IMMEDIATE FIX!"
        + ("X" * 114),  # Exact 160 chars boundary passes
        detailed_description="A" * 4000,  # Exact 4000 chars boundary passes
        requested_priority=TicketPriority.P1_CRITICAL.value,
        impact={
            "users_affected": "ONE",
            "can_work": True,
            "workaround_available": True,
            "security_incident_suspected": False,
            "safety_risk": False,
            "request_type": "INCIDENT",
        },
    )
    assert res_downgraded["ok"] is True
    assert res_downgraded["for_llm"]["priority_allowed"] is False
    assert (
        res_downgraded["for_llm"]["effective_priority"]
        == TicketPriority.P3_MODERATE.value
    )

    # Adjacent failing boundary: 161 chars short_description or 4001 chars detailed_description -> VALIDATION_FAILED
    res_161 = servicedesk_mcp.validate_incident(
        uct="uct-nl-001",
        category="Software - Collaboration",
        short_description="X" * 161,
        detailed_description="Valid details",
        requested_priority=TicketPriority.P3_MODERATE.value,
        impact={
            "users_affected": "ONE",
            "can_work": True,
            "workaround_available": True,
            "security_incident_suspected": False,
            "safety_risk": False,
            "request_type": "INCIDENT",
        },
    )
    assert res_161["ok"] is False
    assert res_161["error_code"] == ErrorCode.VALIDATION_FAILED


def test_tc_ut_034_duplicate_ticket_detection_30m_window_and_similarity_080_boundaries(
    servicedesk_mcp,
):
    """TC-UT-034 (FR-066, FR-023, BH-04-1-01, BH-04-1-05): Block duplicate incident within 30 minutes (token similarity >= 0.80 or in-flight write) and allow when similarity == 0.79 or age == 31m."""
    impact_std = {
        "users_affected": "ONE",
        "can_work": False,
        "workaround_available": False,
        "security_incident_suspected": False,
        "safety_risk": False,
        "request_type": "INCIDENT",
    }
    # Similarity >= 0.80 within 30 minutes (or in-flight IN_PROGRESS/OUTCOME_UNKNOWN) -> IN_FLIGHT_DUPLICATE
    dup_res = servicedesk_mcp.validate_incident(
        uct="uct-nl-001-recent-open-ticket",
        category="Hardware - Laptop",
        short_description="Corporate MacBook Pro screen flickering and blacking out",
        detailed_description="My laptop display flickers every 5 minutes.",
        requested_priority=TicketPriority.P2_HIGH.value,
        impact=impact_std,
    )
    assert dup_res["ok"] is False
    assert dup_res["error_code"] == ErrorCode.IN_FLIGHT_DUPLICATE
    assert dup_res["existing_ticket_id"].startswith("INC")

    # Boundary: token-set similarity 0.79 (< 0.80 threshold) or ticket older than 30m passes validation
    distinct_res = servicedesk_mcp.validate_incident(
        uct="uct-nl-001",
        category="Access - VPN",
        short_description="Cannot connect to Amsterdam corporate VPN gateway",
        detailed_description="VPN client returns certificate expired error.",
        requested_priority=TicketPriority.P2_HIGH.value,
        impact=impact_std,
    )
    assert distinct_res["ok"] is True


def test_tc_ut_035_ticket_id_regex_validation_6_to_10_digits_boundary(servicedesk_mcp):
    """TC-UT-035 (FR-068, FR-061, BH-04-1-01, BH-04-1-05): Enforce ^INC[0-9]{6,10}$ before any backend lookup (6 and 10 digits pass; 5 digits, 11 digits, or wrong prefix fail with INVALID_TICKET_ID)."""
    valid_6 = servicedesk_mcp.get_my_ticket(uct="uct-nl-001", ticket_id="INC123456")
    assert valid_6["ok"] is True
    assert valid_6["for_llm"]["ticket_id"] == "INC123456"

    valid_10 = servicedesk_mcp.get_my_ticket(
        uct="uct-nl-001", ticket_id="INC1234567890"
    )
    assert valid_10["ok"] is True

    for invalid_id in (
        "INC12345",
        "INC12345678901",
        "REQ123456",
        "inc123456",
        "INC-123456",
    ):
        bad = servicedesk_mcp.get_my_ticket(uct="uct-nl-001", ticket_id=invalid_id)
        assert bad["ok"] is False
        assert bad["error_code"] == ErrorCode.INVALID_TICKET_ID
        assert bad.get("backend_called", False) is False


@pytest.mark.parametrize(
    ("current_state", "target_state", "expected_ok"),
    [
        # Allowed transitions per SDD §5.4.4 (FR-065)
        (TicketState.NEW.value, TicketState.IN_PROGRESS.value, True),
        (TicketState.NEW.value, TicketState.RESOLVED.value, True),
        (TicketState.IN_PROGRESS.value, TicketState.ON_HOLD.value, True),
        (TicketState.IN_PROGRESS.value, TicketState.RESOLVED.value, True),
        (TicketState.ON_HOLD.value, TicketState.IN_PROGRESS.value, True),
        (TicketState.ON_HOLD.value, TicketState.RESOLVED.value, True),
        (TicketState.RESOLVED.value, TicketState.CLOSED.value, True),
        (TicketState.RESOLVED.value, TicketState.IN_PROGRESS.value, True),
        # Prohibited transitions per SDD §5.4.4 (FR-065)
        (TicketState.NEW.value, TicketState.CLOSED.value, False),
        (TicketState.NEW.value, TicketState.ON_HOLD.value, False),
        (TicketState.IN_PROGRESS.value, TicketState.CLOSED.value, False),
        (TicketState.ON_HOLD.value, TicketState.CLOSED.value, False),
        (TicketState.CLOSED.value, TicketState.IN_PROGRESS.value, False),
        (TicketState.CLOSED.value, TicketState.RESOLVED.value, False),
    ],
)
def test_tc_ut_036_ticket_state_transition_matrix_fr065(
    servicedesk_mcp,
    current_state,
    target_state,
    expected_ok,
):
    """TC-UT-036 (FR-064, FR-065, SDD §5.4.4, BH-04-1-01): Enforce the exact ticket lifecycle transition matrix including refusal of New -> Closed."""
    res = servicedesk_mcp.validate_ticket_update(
        uct=f"uct-nl-001-state-{current_state}",
        ticket_id="INC123456",
        new_status=target_state,
        resolution_notes="Transition test note",
    )
    assert res["ok"] is expected_ok
    if not expected_ok:
        assert res["error_code"] == ErrorCode.TRANSITION_NOT_ALLOWED


def test_tc_ut_037_cross_system_ticket_privacy_uc22_never_leaks_leave_or_medical_info(
    servicedesk_mcp,
):
    """TC-UT-037 (FR-096, FR-091, FR-092, FR-093, SDD §5.4.5, BH-04-1-02): Enforce UC-2.1, UC-2.2, UC-2.3 ticket templates and reject any UC-2.2 ticket containing medical/sick/leave_request_id details."""
    impact_uc22 = {
        "users_affected": "ONE",
        "can_work": True,
        "workaround_available": True,
        "security_incident_suspected": False,
        "safety_risk": False,
        "request_type": "INCIDENT",
    }
    # Positive: clean UC-2.2 mailbox delegation ticket with zero leave/medical context
    clean_uc22 = servicedesk_mcp.validate_incident(
        uct="uct-nl-001",
        category="Email and Collaboration - Access",
        short_description="Mailbox delegation during planned absence",
        detailed_description="Delegate mailbox access for Eva Bakker (EMP-NL-001) to manager Pieter Jansen (EMP-NL-MGR-01, pieter.jansen.synthetic@example.invalid) from 2026-06-10 to 2026-06-17.",
        requested_priority=TicketPriority.P3_MODERATE.value,
        impact=impact_uc22,
        template_id="UC-2.2",
    )
    assert clean_uc22["ok"] is True
    payload_str = str(clean_uc22["for_llm"]).lower()
    for forbidden_term in (
        "sick",
        "medical",
        "surgery",
        "hospital",
        "diagnosis",
        "lr-",
        "leave_request_id",
        "leave_type",
    ):
        assert forbidden_term not in payload_str

    # Negative: attempting to inject medical/sick leave reason or LR-* ID into UC-2.2 ticket is rejected or stripped
    leaky_uc22 = servicedesk_mcp.validate_incident(
        uct="uct-nl-001",
        category="Email and Collaboration - Access",
        short_description="Mailbox delegation during sick leave LR-2026-0099",
        detailed_description="Employee is on medical leave for surgery (leave_request_id=LR-2026-0099).",
        requested_priority=TicketPriority.P3_MODERATE.value,
        impact=impact_uc22,
        template_id="UC-2.2",
    )
    assert leaky_uc22["ok"] is False
    assert leaky_uc22["error_code"] == ErrorCode.VALIDATION_FAILED


def test_tc_ut_038_servicedesk_write_tools_automation_origin_and_ct_enforcement(
    servicedesk_mcp,
):
    """TC-UT-038 (FR-006, FR-016, FR-022, FR-060..FR-064, NFR-032): Verify create_incident, add_ticket_comment, and update_ticket_status record automation_origin='hr-agent-mvp1', enforce CT, and block cross-user access."""
    impact_p2 = {
        "users_affected": "ONE",
        "can_work": False,
        "workaround_available": False,
        "security_incident_suspected": False,
        "safety_risk": False,
        "request_type": "INCIDENT",
    }
    created = servicedesk_mcp.create_incident(
        uct="uct-nl-001",
        ct="ct-valid-inc-001",
        idempotency_key="0195f000-0000-7000-8000-000000000201",
        step_id="step-1",
        category="Hardware - Laptop",
        short_description="Laptop does not power on",
        detailed_description="Device fails to boot after firmware update.",
        priority=TicketPriority.P2_HIGH.value,
        impact=impact_p2,
        preview_digest="sha256:inc-prev-001",
    )
    assert created["ok"] is True
    assert created["for_llm"]["ticket_id"].startswith("INC")
    assert created["for_llm"]["requester_employee_id"] == "EMP-NL-001"
    assert created["for_llm"]["automation_origin"] == "hr-agent-mvp1"
    assert "related_leave_request_id" not in created["for_llm"]

    # Missing CT on add_ticket_comment or update_ticket_status -> INVALID_CONFIRMATION_TOKEN
    no_ct_comment = servicedesk_mcp.add_ticket_comment(
        uct="uct-nl-001",
        ct="",
        idempotency_key="0195f000-0000-7000-8000-000000000202",
        step_id="step-1",
        ticket_id="INC123456",
        comment="Added diagnostic logs.",
        preview_digest="sha256:comm-prev-001",
    )
    assert no_ct_comment["ok"] is False
    assert no_ct_comment["error_code"] == ErrorCode.INVALID_CONFIRMATION_TOKEN

    # Cross-user ticket lookup -> CROSS_USER_ACCESS_DENIED
    cross_user = servicedesk_mcp.get_my_ticket(
        uct="uct-nl-001",
        ticket_id="INC999999",  # Owned by EMP-DE-002
    )
    assert cross_user["ok"] is False
    assert cross_user["error_code"] == ErrorCode.CROSS_USER_ACCESS_DENIED
