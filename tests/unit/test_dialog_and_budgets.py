"""Unit tests for Conversational NLU, Turn Call/Time Budgets, 2000-Char Limit, 10-Turn Context Window, Session Isolation, English-Only Notice, and Out-of-Scope Refusal (FR-019, FR-024, FR-025, FR-030..FR-036, NFR-033, CON-004, CON-005)."""

from __future__ import annotations

import pytest

from hr_agent.models import OUT_OF_SCOPE_DOMAINS, SUPPORTED_LANGUAGES, ErrorCode

pytestmark = pytest.mark.unit


def test_tc_ut_016_intent_classification_typos_synonyms_and_multi_intent(coordinator):
    """TC-UT-016 (FR-030, FR-031, FR-090, NFR-033, CON-002): Classify single and multi-domain intents while tolerating typos and synonyms ('PTO' -> VACATION)."""
    res = coordinator.classify_and_route_turn(
        session_id="sess-nl-001",
        uct="uct-nl-001",
        message="What is our remote equipment policy, can I book 2 days PTO next week, and open an IT laptop ticket?",
    )
    assert res["intent_category"] == "COMPOSITE_MULTI_AGENT"
    assert set(res["target_sub_agents"]) == {
        "policy_answer",
        "workweek_ops",
        "servicedesk_ops",
    }
    assert len(res["target_sub_agents"]) <= 3

    synonym_res = coordinator.classify_and_route_turn(
        session_id="sess-nl-001",
        uct="uct-nl-001",
        message="How much pto / annual holiday balnce do I have left?",
    )
    assert synonym_res["normalized_leave_type"] == "VACATION"
    assert synonym_res["target_sub_agents"] == ["workweek_ops"]


def test_tc_ut_017_ambiguous_input_triggers_clarification_without_mutation(coordinator):
    """TC-UT-017 (FR-030, FR-031, FR-045): Ambiguous leave request missing dates asks clarifying question and invokes zero write tools."""
    res = coordinator.classify_and_route_turn(
        session_id="sess-nl-001",
        uct="uct-nl-001",
        message="I need to take some time off soon.",
    )
    assert res["action"] == "ASK_CLARIFICATION"
    assert "start_date" in res["missing_slots"]
    assert "end_date" in res["missing_slots"]
    assert res["write_tools_invoked"] == 0


def test_tc_ut_018_ten_turn_context_window_anaphora_resolution_and_boundary(
    coordinator,
):
    """TC-UT-018 (FR-032): Resolve follow-up references ('that ticket', 'add a comment to it') within 10-turn window and enforce 10-turn boundary."""
    history_10_turns = [
        {
            "turn_index": i,
            "role": "user",
            "text": f"Turn {i}",
            "referenced_ticket_id": "INC123456" if i == 10 else None,
        }
        for i in range(1, 11)
    ]
    res_within_10 = coordinator.classify_and_route_turn(
        session_id="sess-nl-001",
        uct="uct-nl-001",
        message="Please add a comment to that ticket saying I rebooted the router.",
        conversation_history=history_10_turns,
    )
    assert res_within_10["resolved_entities"]["ticket_id"] == "INC123456"
    assert res_within_10["context_turns_retained"] == 10

    # Boundary: 11 turns in history -> oldest turn beyond 10-turn window is dropped from active context
    history_11_turns = [
        {
            "turn_index": 0,
            "role": "user",
            "text": "Old turn 0",
            "referenced_ticket_id": "INC000001",
        },
        *history_10_turns,
    ]
    res_at_11 = coordinator.classify_and_route_turn(
        session_id="sess-nl-001",
        uct="uct-nl-001",
        message="What is the status of that ticket?",
        conversation_history=history_11_turns,
    )
    assert res_at_11["context_turns_retained"] == 10
    assert res_at_11["resolved_entities"]["ticket_id"] == "INC123456"


def test_tc_ut_019_session_memory_isolation_and_memory_bank_disabled(coordinator):
    """TC-UT-019 (FR-033, FR-015, NFR-003): Enforce strict session memory isolation across sessions and reject enabled Vertex Memory Bank."""
    isolated = coordinator.classify_and_route_turn(
        session_id="sess-user-a",
        uct="uct-nl-001",
        message="Check my profile",
        memory_bank_enabled=False,
    )
    assert isolated["cross_session_memory_used"] is False

    # Negative/Boundary: attempting to enable cross-session Vertex Memory Bank in MVP 1 is rejected
    violation = coordinator.classify_and_route_turn(
        session_id="sess-user-a",
        uct="uct-nl-001",
        message="Check my profile",
        memory_bank_enabled=True,
    )
    assert violation["ok"] is False
    assert violation["error_code"] == ErrorCode.VALIDATION_FAILED


def test_tc_ut_020_per_turn_call_and_deadline_budgets_pass_and_fail_boundaries(
    coordinator,
):
    """TC-UT-020 (FR-019, SDD §3.3, BH-04-1-01, BH-04-1-05): Enforce max 10 model calls, max 10 tool calls, 30s message turn deadline, and 50s confirmation turn deadline."""
    # Exact passing boundary: 10 model calls, 10 tool calls, 30000ms message turn
    pass_msg_boundary = coordinator.classify_and_route_turn(
        session_id="sess-budget-ok",
        uct="uct-nl-001",
        message="Check leave balance",
        model_calls_used=10,
        tool_calls_used=10,
        elapsed_ms=30000,
        is_confirmation_turn=False,
    )
    assert pass_msg_boundary["ok"] is True

    # Failing boundary: 11 model calls -> TURN_LIMIT_REACHED
    fail_model_11 = coordinator.classify_and_route_turn(
        session_id="sess-budget-fail-m",
        uct="uct-nl-001",
        message="Check leave balance",
        model_calls_used=11,
        tool_calls_used=5,
        elapsed_ms=10000,
    )
    assert fail_model_11["ok"] is False
    assert fail_model_11["error_code"] == ErrorCode.TURN_LIMIT_REACHED

    # Failing boundary: 11 tool calls across nested agents -> TURN_LIMIT_REACHED
    fail_tool_11 = coordinator.classify_and_route_turn(
        session_id="sess-budget-fail-t",
        uct="uct-nl-001",
        message="Check leave balance",
        model_calls_used=5,
        tool_calls_used=11,
        elapsed_ms=10000,
    )
    assert fail_tool_11["ok"] is False
    assert fail_tool_11["error_code"] == ErrorCode.TURN_LIMIT_REACHED

    # Failing boundary: 30001ms on message turn (> 30s) -> TURN_LIMIT_REACHED; 50000ms on confirmation turn passes, 50001ms fails
    fail_msg_time = coordinator.classify_and_route_turn(
        session_id="sess-budget-fail-time",
        uct="uct-nl-001",
        message="Check leave balance",
        model_calls_used=2,
        tool_calls_used=2,
        elapsed_ms=30001,
        is_confirmation_turn=False,
    )
    assert fail_msg_time["ok"] is False
    assert fail_msg_time["error_code"] == ErrorCode.TURN_LIMIT_REACHED

    pass_conf_50s = coordinator.classify_and_route_turn(
        session_id="sess-budget-conf-ok",
        uct="uct-nl-001",
        message="Confirm plan",
        model_calls_used=2,
        tool_calls_used=2,
        elapsed_ms=50000,
        is_confirmation_turn=True,
    )
    assert pass_conf_50s["ok"] is True

    fail_conf_50001 = coordinator.classify_and_route_turn(
        session_id="sess-budget-conf-fail",
        uct="uct-nl-001",
        message="Confirm plan",
        model_calls_used=2,
        tool_calls_used=2,
        elapsed_ms=50001,
        is_confirmation_turn=True,
    )
    assert fail_conf_50001["ok"] is False
    assert fail_conf_50001["error_code"] == ErrorCode.TURN_LIMIT_REACHED


def test_tc_ut_021_message_length_2000_pass_and_2001_http_413_boundary(
    input_safety_gate, fixed_now
):
    """TC-UT-021 (FR-025, FR-031, SDD §5.3, BH-04-1-02): Allow 2,000-character message and reject 2,001-character message with HTTP 413 MESSAGE_TOO_LONG."""
    msg_2000 = "A" * 2000
    res_ok = input_safety_gate.evaluate_input(
        session_id="sess-len-2000",
        employee_id="EMP-NL-001",
        text=msg_2000,
        turn_index=1,
        now_epoch=fixed_now,
    )
    assert res_ok["allowed"] is True

    msg_2001 = "A" * 2001
    res_fail = input_safety_gate.evaluate_input(
        session_id="sess-len-2001",
        employee_id="EMP-NL-001",
        text=msg_2001,
        turn_index=1,
        now_epoch=fixed_now,
    )
    assert res_fail["allowed"] is False
    assert res_fail["http_status"] == 413
    assert res_fail["error_code"] == ErrorCode.MESSAGE_TOO_LONG


def test_tc_ut_022_english_only_constraint_and_non_english_notice(
    input_safety_gate, fixed_now
):
    """TC-UT-022 (FR-034, CON-005, BH-04-1-02): Verify SUPPORTED_LANGUAGES == ('en',) and non-English input returns polite English notice."""
    assert SUPPORTED_LANGUAGES == ("en",)

    res_nl_text = input_safety_gate.evaluate_input(
        session_id="sess-lang-nl",
        employee_id="EMP-NL-001",
        text="Hoeveel vakantiedagen heb ik nog dit jaar?",
        turn_index=1,
        now_epoch=fixed_now,
    )
    assert res_nl_text["allowed"] is False
    assert res_nl_text["error_code"] == ErrorCode.UNSUPPORTED_LANGUAGE
    assert "only English is supported" in res_nl_text["reply_text"]


def test_tc_ut_023_out_of_scope_domain_refusal_and_fallback_guidance(coordinator):
    """TC-UT-023 (FR-035, FR-036, CON-004, CON-006): Refuse out-of-scope payroll/compensation/stock/tax/performance queries and offer ticket or HR contact fallback."""
    assert "PAYROLL" in OUT_OF_SCOPE_DOMAINS
    assert "COMPENSATION" in OUT_OF_SCOPE_DOMAINS
    assert "STOCK_EQUITY" in OUT_OF_SCOPE_DOMAINS

    res = coordinator.classify_and_route_turn(
        session_id="sess-oos-01",
        uct="uct-nl-001",
        message="Please increase my base salary by 10% and vest my equity stock options.",
    )
    assert res["ok"] is False
    assert res["error_code"] == ErrorCode.OUT_OF_SCOPE_DOMAIN
    assert res["fallback_offered"]["can_create_ticket"] is True
    assert res["fallback_offered"]["hr_contact_provided"] is True
    assert (
        res["live_human_chat_handoff"] is False
    )  # CON-006: no live chat handoff in MVP 1
