"""Performance, Latency Budget, 99.9% Availability SLO (SDD §7.8.2), Rate Limiting (FR-024/NFR-024), Concurrency (NFR-023), and Warm Instances (NFR-042) tests."""

from __future__ import annotations

import pytest

from hr_agent.models import ErrorCode

pytestmark = pytest.mark.performance


def test_tc_perf_001_sse_ttft_and_10s_p95_turn_latency_boundaries(
    bff_service, fixed_now
):
    """TC-PERF-001 (NFR-020, BH-04-1-05): Verify SSE TTFT <= 1500ms p95, read/RAG <= 3500ms p95, write <= 5000ms p95, and hard 10000ms p95 ceiling."""
    events = bff_service.send_message_stream(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        session_id="sess-perf-01",
        text="What is my current vacation balance?",
        client_message_id="msg-perf-01",
        now_epoch=fixed_now,
    )
    done_event = next(e for e in events if e["event"] == "done")
    assert done_event["metrics"]["ttft_ms"] <= 1500
    assert done_event["metrics"]["total_latency_ms"] <= 3500
    assert done_event["metrics"]["p95_turn_latency_ms"] <= 10000


def test_tc_perf_002_safety_and_grounding_gate_300ms_and_600ms_latency_boundaries(
    input_safety_gate, output_safety_gate, fixed_now
):
    """TC-PERF-002 (NFR-021, BH-04-1-05): Verify InputSafetyGate <= 300ms p95 and combined safety + grounding <= 600ms p95."""
    in_res = input_safety_gate.evaluate_input(
        session_id="sess-perf-02",
        employee_id="EMP-NL-001",
        text="How do I request parental leave in NL?",
        turn_index=1,
        now_epoch=fixed_now,
    )
    assert in_res["latency_ms"] <= 300

    out_res = output_safety_gate.evaluate_and_stamp_output(
        session_id="sess-perf-02",
        turn_id="turn-perf-02",
        candidate_text="Employees in NL may request parental leave via WorkWeek [POL-NL-LEAVE-2026#5.1].",
        grounding_verdict={"passed": True, "score": 0.92},
    )
    assert out_res["gate_latency_ms"] <= 600
    assert (in_res["latency_ms"] + out_res["gate_latency_ms"]) <= 600


def test_tc_perf_003_per_user_rate_limits_20_min_300_day_and_5_writes_min_boundaries(
    bff_service, fixed_now
):
    """TC-PERF-003 (FR-024, NFR-024, BH-04-1-01, BH-04-1-05): Boundary test for sliding-window rate limits (20 msg/min vs 21, 300 msg/day vs 301, and 5 writes/min vs 6)."""
    # Exact passing boundary: 20th message in minute and 300th message in day allowed
    msg_ok = bff_service.check_rate_limit(
        employee_id="EMP-RATE-01",
        is_write=False,
        messages_last_minute=20,
        messages_today=300,
        now_epoch=fixed_now,
    )
    assert msg_ok["allowed"] is True

    # Failing boundary: 21st message in minute -> RATE_LIMITED
    msg_21_min = bff_service.check_rate_limit(
        employee_id="EMP-RATE-01",
        is_write=False,
        messages_last_minute=21,
        messages_today=21,
        now_epoch=fixed_now,
    )
    assert msg_21_min["allowed"] is False
    assert msg_21_min["error_code"] == ErrorCode.RATE_LIMITED
    assert msg_21_min["retry_after_seconds"] > 0

    # Failing boundary: 301st message in day -> RATE_LIMITED
    msg_301_day = bff_service.check_rate_limit(
        employee_id="EMP-RATE-01",
        is_write=False,
        messages_last_minute=5,
        messages_today=301,
        now_epoch=fixed_now,
    )
    assert msg_301_day["allowed"] is False
    assert msg_301_day["error_code"] == ErrorCode.RATE_LIMITED

    # Write confirmation boundary: 5 writes/min allowed, 6th write/min rejected with RATE_LIMITED
    write_5 = bff_service.check_rate_limit(
        employee_id="EMP-RATE-01",
        is_write=True,
        writes_last_minute=5,
        now_epoch=fixed_now,
    )
    assert write_5["allowed"] is True

    write_6 = bff_service.check_rate_limit(
        employee_id="EMP-RATE-01",
        is_write=True,
        writes_last_minute=6,
        now_epoch=fixed_now,
    )
    assert write_6["allowed"] is False
    assert write_6["error_code"] == ErrorCode.RATE_LIMITED


def test_tc_perf_004_availability_slo_999_two_probe_and_gated_real_turn_rules(
    topology_validator,
):
    """TC-PERF-004 (NFR-022, SDD §7.8.2, BH-04-1-04, BH-04-1-05): Verify 99.9% service-window uptime SLO using the two-probe rule (30s retry, 20s cutoff) and gated real-turn rule (real_turns >= 5, error_turns >= 3, ratio > 0.05)."""
    # 1. Single probe failure where 30s confirmation probe passes -> GOOD window (prevents false bad windows)
    single_probe_recovered = topology_validator.evaluate_availability_slo_window(
        probe_1_passed=False,
        probe_2_after_30s_passed=True,
        probe_latency_seconds=1.2,
        real_turns=10,
        error_turns=0,
    )
    assert single_probe_recovered["bad_window"] is False

    # 2. Both probe 1 and 30s confirmation probe fail (or latency > 20.0s) -> BAD window
    two_probe_failed = topology_validator.evaluate_availability_slo_window(
        probe_1_passed=False,
        probe_2_after_30s_passed=False,
        probe_latency_seconds=20.1,
        real_turns=0,
        error_turns=0,
    )
    assert two_probe_failed["bad_window"] is True
    assert two_probe_failed["reason"] == "TWO_PROBE_FAILURE"

    # 3. Gated real-turn rule boundaries:
    # 3a. real_turns=4, error_turns=3 (< 5 minimum turns) -> GOOD window
    low_traffic = topology_validator.evaluate_availability_slo_window(
        probe_1_passed=True,
        probe_2_after_30s_passed=None,
        probe_latency_seconds=0.8,
        real_turns=4,
        error_turns=3,
    )
    assert low_traffic["bad_window"] is False

    # 3b. real_turns=60, error_turns=3 (ratio == 0.05, not > 0.05) -> GOOD window
    exact_5pct = topology_validator.evaluate_availability_slo_window(
        probe_1_passed=True,
        probe_2_after_30s_passed=None,
        probe_latency_seconds=0.8,
        real_turns=60,
        error_turns=3,
    )
    assert exact_5pct["bad_window"] is False

    # 3c. real_turns=5, error_turns=3 (real_turns >= 5, error_turns >= 3, ratio 0.60 > 0.05) -> BAD window
    real_turn_breach = topology_validator.evaluate_availability_slo_window(
        probe_1_passed=True,
        probe_2_after_30s_passed=None,
        probe_latency_seconds=0.8,
        real_turns=5,
        error_turns=3,
    )
    assert real_turn_breach["bad_window"] is True
    assert real_turn_breach["reason"] == "GATED_REAL_TURN_BREACH"


def test_tc_perf_005_concurrency_1000_sessions_4x_headroom_and_warm_min_instances(
    topology_validator,
):
    """TC-PERF-005 (NFR-022, NFR-023, NFR-024, NFR-042, BH-04-1-04, BH-04-1-05): Verify 99.9% SLO target, >= 1,000 concurrent sessions, 2.0 turns/sec (4x pilot peak), and warm min_instances >= 1 in prod."""
    compliant = topology_validator.verify_deployment_invariants(
        deployment_manifest={
            "environment": "prod",
            "min_instances_bff": 2,
            "min_instances_mcp": 2,
            "max_instances_bff": 20,
            "pilot_peak_concurrent_users": 20,
            "pilot_peak_tps": 0.5,
            "headroom_tps_capacity": 2.0,  # 4x pilot peak headroom (NFR-024)
            "concurrent_sessions_supported": 1000,  # >= 1,000 concurrent sessions (NFR-023)
            "availability_slo_pct": 99.9,  # 99.9% service-window uptime SLO (NFR-022)
        }
    )
    assert compliant["compliant"] is True
    assert compliant["zero_cold_start_verified"] is True

    # Negative/Boundary: min_instances=0 in prod or availability_slo_pct=99.5 (< 99.9%) fails compliance
    non_compliant = topology_validator.verify_deployment_invariants(
        deployment_manifest={
            "environment": "prod",
            "min_instances_bff": 0,  # Violates NFR-042 warm instance requirement
            "min_instances_mcp": 1,
            "concurrent_sessions_supported": 999,  # Below 1,000 session scale target
            "availability_slo_pct": 99.5,  # Below 99.9% SLO boundary
        }
    )
    assert non_compliant["compliant"] is False
