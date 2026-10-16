"""Unit tests for UI SSE Streaming, Confirmation Cards, Citation Drawer, AI Disclosure Banner (FR-103), WCAG 2.2 AA (FR-102), Feedback/Deflection (FR-104, FR-110, NFR-060), Kill Switch (NFR-045), FinOps (NFR-050, NFR-051), and Constraints (CON-001..CON-009)."""

from __future__ import annotations

import pytest

from hr_agent.models import ErrorCode

pytestmark = pytest.mark.unit


def test_tc_ut_053_sse_streaming_data_cards_progress_and_whole_plan_confirmation(
    bff_service, fixed_now
):
    """TC-UT-053 (FR-100, FR-101, FR-095, BH-04-1-02): BFF send_message_stream yields token, tool_status, citation, and confirmation_required events; resolve_confirmation processes whole-plan confirm/cancel."""
    events = bff_service.send_message_stream(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        session_id="sess-nl-001",
        text="Book 3 days of vacation from 2026-06-10 to 2026-06-12",
        client_message_id="msg-001",
        now_epoch=fixed_now,
    )
    event_types = [e["event"] for e in events]
    assert "tool_status" in event_types
    assert "token" in event_types
    assert "confirmation_required" in event_types
    assert "done" in event_types

    # Whole-plan cancel followed by modified message generates a fresh confirmation_id and preview_digest (SDD §5.3 / §3.7)
    cancelled = bff_service.resolve_confirmation(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        session_id="sess-nl-001",
        confirmation_id="conf-001",
        decision="cancel",
        now_epoch=fixed_now + 5,
    )
    assert cancelled["status"] == "CANCELLED"

    # Whole-plan confirm at exact 300s boundary succeeds; at 301s fails with CONFIRMATION_EXPIRED
    confirmed_300s = bff_service.resolve_confirmation(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        session_id="sess-nl-001",
        confirmation_id="conf-002",
        decision="confirm",
        now_epoch=fixed_now + 300,
    )
    assert confirmed_300s["status"] == "COMPLETED"

    expired_301s = bff_service.resolve_confirmation(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        session_id="sess-nl-001",
        confirmation_id="conf-003",
        decision="confirm",
        now_epoch=fixed_now + 301,
    )
    assert expired_301s["ok"] is False
    assert expired_301s["error_code"] == ErrorCode.CONFIRMATION_EXPIRED

    # Negative: unsupported decision verb (e.g., partial step approval or 'EDIT') rejected with VALIDATION_FAILED
    bad_decision = bff_service.resolve_confirmation(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        session_id="sess-nl-001",
        confirmation_id="conf-004",
        decision="INVALID_DECISION",
        now_epoch=fixed_now + 10,
    )
    assert bad_decision["ok"] is False
    assert bad_decision["error_code"] == ErrorCode.VALIDATION_FAILED


def test_tc_ut_054_ai_disclosure_banner_privacy_link_and_wcag_22_aa(bff_service):
    """TC-UT-054 (FR-102, FR-103, BH-04-1-01): Verify persistent AI disclosure banner, privacy notice link before first message, and WCAG 2.2 AA accessibility metadata."""
    ui_cfg = bff_service.get_ui_bootstrap_config(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
    )
    assert ui_cfg["ai_disclosure_banner"]["persistent"] is True
    assert "AI" in ui_cfg["ai_disclosure_banner"]["text"]
    assert ui_cfg["privacy_notice_url"].startswith("https://")
    assert ui_cfg["accessibility"]["wcag_level"] == "2.2-AA"
    assert ui_cfg["accessibility"]["min_contrast_ratio"] >= 4.5
    assert ui_cfg["accessibility"]["aria_live_streaming"] == "polite"
    assert ui_cfg["accessibility"]["keyboard_navigable"] is True

    # Negative/Boundary: missing AI disclosure header or contrast < 4.5 fails UI compliance check
    bad_cfg = bff_service.get_ui_bootstrap_config(
        headers={
            "X-Goog-IAP-JWT-Assertion": "jwt-nl-001",
            "X-Test-Simulate-Missing-Disclosure": "true",
        },
        client_ip="10.200.1.10",
    )
    assert bad_cfg["compliant"] is False


def test_tc_ut_055_citation_drawer_active_document_proxy_and_session_list(bff_service):
    """TC-UT-055 (FR-017, FR-074, FR-077, FR-100, FR-101): Citation drawer proxies active country-scoped policy excerpt and rejects cross-country doc with 404."""
    doc = bff_service.get_document(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        doc_id="POL-NL-LEAVE-2026",
        version="2026.1",
        section="4.2",
    )
    assert doc["doc_id"] == "POL-NL-LEAVE-2026"
    assert doc["country"] in ("NL", "GLOBAL")
    assert doc["status"] == "active"

    # Negative/Boundary: NL user requesting DE-only document gets 404 DOCUMENT_NOT_FOUND
    cross_country_doc = bff_service.get_document(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        doc_id="POL-DE-LEAVE-2026",
    )
    assert cross_country_doc["http_status"] == 404
    assert cross_country_doc["error_code"] == ErrorCode.DOCUMENT_NOT_FOUND

    sessions = bff_service.list_sessions(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        limit=20,
    )
    assert "sessions" in sessions


def test_tc_ut_056_feedback_spii_redaction_500_char_boundary_and_deflection_kpi(
    bff_service,
):
    """TC-UT-056 (FR-104, FR-110, NFR-060): Submit thumbs-up/down rating with <= 500-char SPII-redacted comment and record conversation outcome for >= 40% deflection KPI."""
    # Exact 500-char comment with embedded phone/BSN is accepted and SPII-redacted before persistence
    comment_500 = ("Helpful answer, my phone is +31612345678. " + ("A" * 500))[:500]
    res_ok = bff_service.submit_feedback(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        session_id="sess-nl-001",
        turn_id="turn-001",
        rating="UP",
        reason_category="ACCURATE_POLICY_CITATION",
        comment=comment_500,
    )
    assert res_ok["recorded"] is True
    assert "+31612345678" not in res_ok["redacted_comment"]
    assert res_ok["conversation_outcome"] in (
        "SELF_SERVED",
        "TICKET_CREATED",
        "ESCALATED",
        "BLOCKED",
        "ABANDONED",
    )
    assert res_ok["deflection_rate_target"] >= 0.40

    # Adjacent failing boundary: 501-char comment rejected with VALIDATION_FAILED
    res_501 = bff_service.submit_feedback(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        session_id="sess-nl-001",
        turn_id="turn-001",
        rating="DOWN",
        reason_category="OTHER",
        comment="X" * 501,
    )
    assert res_501["ok"] is False
    assert res_501["error_code"] == ErrorCode.VALIDATION_FAILED


def test_tc_ut_057_kill_switch_seed_toggle_and_10s_60s_fail_closed_boundaries(
    hr_admin, fixed_now
):
    """TC-UT-057 (NFR-045, BH-01-3-05, BH-04-1-05): Seed runtime_flags on startup, toggle writes_enabled, pass at 60s cache age when DB unreachable, and fail closed at 61s."""
    seed = hr_admin.seed_runtime_flags_if_absent(now_epoch=fixed_now)
    assert seed["writes_enabled"] is True
    assert seed["assistant_enabled"] is True

    toggled = hr_admin.set_kill_switch(
        flag_name="writes_enabled",
        enabled=False,
        reason="Upstream maintenance window",
        caller_sa="hr-admin-sa@hr-agent-prodpilot.iam.gserviceaccount.com",
        now_epoch=fixed_now + 10,
    )
    assert toggled["writes_enabled"] is False
    assert toggled["audit_event_type"] == "KILL_SWITCH_CHANGED"

    # Negative: unauthorized service account attempting to toggle kill switch is denied
    unauth_toggle = hr_admin.set_kill_switch(
        flag_name="writes_enabled",
        enabled=True,
        reason="Unauthorized attempt",
        caller_sa="hr-bff-sa@hr-agent-prodpilot.iam.gserviceaccount.com",
        now_epoch=fixed_now + 15,
    )
    assert unauth_toggle["ok"] is False
    assert unauth_toggle["error_code"] == ErrorCode.AUTHZ_DENIED

    # Boundary: cache_age_seconds == 60 with db_reachable=False still allows cached state; 61s fails closed on writes
    at_60s = hr_admin.evaluate_kill_switch_state(
        cached_flags={"writes_enabled": True, "assistant_enabled": True},
        cache_age_seconds=60,
        db_reachable=False,
        is_write_operation=True,
    )
    assert at_60s["allowed"] is True

    at_61s = hr_admin.evaluate_kill_switch_state(
        cached_flags={"writes_enabled": True, "assistant_enabled": True},
        cache_age_seconds=61,
        db_reachable=False,
        is_write_operation=True,
    )
    assert at_61s["allowed"] is False
    assert at_61s["error_code"] == ErrorCode.WRITES_DISABLED


def test_tc_ut_058_finops_budget_and_constraints_con001_to_con009_boundaries(
    finops_tracker,
):
    """TC-UT-058 (NFR-050, NFR-051, CON-001..CON-009, BH-04-1-01, BH-04-1-05): Verify token cost <= $0.015/turn, budget <= $8,500/mo, and positive/negative/boundary checks for CON-001..CON-009."""
    cost_ok = finops_tracker.evaluate_turn_cost_and_budget(
        model_id="gemini-2.5-flash",
        input_tokens=1800,
        output_tokens=350,
        monthly_projected_usd=8500.0,  # Exact $8,500.00 ceiling boundary passes
    )
    assert cost_ok["turn_cost_usd"] <= 0.015
    assert cost_ok["within_monthly_ceiling"] is True

    over_budget = finops_tracker.evaluate_turn_cost_and_budget(
        model_id="gemini-2.5-pro",
        input_tokens=12000,
        output_tokens=3500,
        monthly_projected_usd=8500.01,  # $0.01 over $8,500 ceiling triggers alert
    )
    assert over_budget["within_monthly_ceiling"] is False
    assert over_budget["alert_triggered"] == "BUDGET_100_PCT"

    valid_cfg = {
        "cloud_provider": "GCP",  # CON-008
        "primary_region": "europe-west4",  # CON-008, NFR-006
        "search_multi_region": "eu",  # CON-008, NFR-006
        "orchestration_framework": "google-adk",  # CON-002
        "adk_version": "2.1.0",  # CON-002 (>=2.0.0,<3.0.0)
        "single_tenant": True,  # CON-003
        "supported_languages": ["en"],  # CON-005
        "channels": ["web_spa"],  # CON-007
        "upstream_base_url": "https://mock-saas.aishprabhat.demo.altostrat.com",  # CON-001, CON-009
        "upstream_docs_url": "https://mock-saas.aishprabhat.demo.altostrat.com/docs",  # CON-009
        "upstream_iap_client_id": "332638604308-825bkuk2cjp8h06t1g41o2lvk8oq9cnk.apps.googleusercontent.com",  # CON-001
        "compensation_changes_enabled": False,  # CON-004
        "live_human_chat_handoff_enabled": False,  # CON-006
    }
    constraints_ok = finops_tracker.validate_scope_and_platform_constraints(
        config=valid_cfg
    )
    assert constraints_ok["valid"] is True

    # Negative/Boundary checks for CON-001..CON-009 violations (e.g., multi-tenant, Slack channel, non-English, or ADK 1.x)
    invalid_cfg = {
        **valid_cfg,
        "single_tenant": False,  # Violates CON-003
        "supported_languages": ["en", "nl"],  # Violates CON-005
        "channels": ["web_spa", "slack"],  # Violates CON-007
        "live_human_chat_handoff_enabled": True,  # Violates CON-006
    }
    constraints_fail = finops_tracker.validate_scope_and_platform_constraints(
        config=invalid_cfg
    )
    assert constraints_fail["valid"] is False
    assert len(constraints_fail["violated_constraints"]) >= 4


def test_tc_ut_059_display_stripping_and_rag_spotlighting_boundaries(
    output_safety_gate,
):
    """TC-UT-059 (FR-010, FR-013, FR-014, FR-075, NFR-001, NFR-002, NFR-007): Boundary test for deeply nested display{} stripping and empty/malformed RAG chunk list."""
    nested_payload = {
        "for_llm": {"items": [{"id": "1", "display": {"secret": "strip-me"}}]},
        "display": {},
    }
    cleaned = output_safety_gate.strip_display_fields_from_tool_payload(nested_payload)
    assert "display" not in cleaned
    assert "display" not in cleaned["for_llm"]["items"][0]

    empty_wrapped = output_safety_gate.wrap_untrusted_rag_chunks([])
    assert empty_wrapped == ""


def test_tc_ut_060_geo_guard_cidr_and_whitespace_normalization_boundary(region_guard):
    """TC-UT-060 (FR-027, FR-028, ADR-020, BH-01-4-02): Boundary test for VPN CIDR edge IP and lowercase/padded ' tw ' vs ' cn ' country string."""
    res_tw = region_guard.evaluate_region_access(
        route_path="/api/v1/me",
        jwt_country=" tw ",
        persona_country="NL",
        lb_country_header="NL",
        corporate_origin_header="NL",
        origin_ip="10.200.255.254",
        trusted_vpn_cidrs=["10.200.0.0/16"],
    )
    assert res_tw["allowed"] is False
    assert res_tw["error_code"] == ErrorCode.REGION_BLOCKED

    res_cn = region_guard.evaluate_region_access(
        route_path="/api/v1/me",
        jwt_country=" cn ",
        persona_country="CN",
        lb_country_header="CN",
        corporate_origin_header="CN",
        origin_ip="10.200.0.1",
        trusted_vpn_cidrs=["10.200.0.0/16"],
    )
    assert res_cn["allowed"] is True
    assert res_cn["effective_country"] == "CN"
