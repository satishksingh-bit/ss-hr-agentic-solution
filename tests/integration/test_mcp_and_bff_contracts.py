"""Integration tests for BFF HTTP/SSE contracts, MCP tool adapters, Upstream Mock SaaS headers (including X-Request-Deadline), OpenAPI drift, and Circuit Breaker."""

from __future__ import annotations

import pytest

from hr_agent.models import ErrorCode
from hr_agent.upstream_client import (
    MOCK_SAAS_BASE_URL,
    MOCK_SAAS_DOCS_URL,
    MOCK_SAAS_IAP_CLIENT_ID,
)

pytestmark = pytest.mark.integration


def test_tc_it_001_bff_get_me_bootstrap_and_session_lifecycle(bff_service, fixed_now):
    """TC-IT-001 (FR-020, FR-021, FR-026, FR-100, FR-102, FR-103): BFF /api/v1/me resolves persona, returns AI disclosure bootstrap config, and creates/lists/logs-out/deletes session."""
    me = bff_service.get_me(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
    )
    assert me["employee_id"] == "EMP-NL-001"
    assert me["country"] == "NL"

    bootstrap = bff_service.get_ui_bootstrap_config(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
    )
    assert bootstrap["ai_disclosure_banner"]["persistent"] is True
    assert bootstrap["accessibility"]["wcag_level"] == "2.2-AA"

    sess = bff_service.create_session(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        now_epoch=fixed_now,
    )
    assert sess["session_id"].startswith("sess-")
    assert sess["idle_timeout_seconds"] == 1800
    assert sess["absolute_timeout_seconds"] == 28800

    logged_out = bff_service.logout_session(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        session_id=sess["session_id"],
        now_epoch=fixed_now + 60,
    )
    assert logged_out["clear_client_state"] is True

    deleted = bff_service.delete_session(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        session_id=sess["session_id"],
    )
    assert deleted["deleted"] is True


def test_tc_it_002_upstream_mock_saas_headers_authorization_proxy_and_deadline(
    upstream_client, fixed_now
):
    """TC-IT-002 (FR-005, FR-006, NFR-041, CON-001, CON-008, CON-009, ADR-021, BH-01-5-01, BH-04-1-04): Verify outbound headers including X-Request-Deadline in authorization and proxy_authorization modes."""
    assert MOCK_SAAS_DOCS_URL == "https://mock-saas.aishprabhat.demo.altostrat.com/docs"
    deadline_ms = (fixed_now + 30) * 1000

    headers_auth = upstream_client.build_outbound_headers(
        iap_oidc_token=f"oidc-for-{MOCK_SAAS_IAP_CLIENT_ID}",
        uct="uct-nl-001",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        idempotency_key="0195f000-0000-7000-8000-000000000301",
        deadline_epoch_ms=deadline_ms,
        iap_header_mode="authorization",
    )
    assert headers_auth["Authorization"].startswith("Bearer ")
    assert headers_auth["X-Automation-Origin"] == "hr-agent-mvp1"
    assert headers_auth["X-User-Context-Assertion"] == "uct-nl-001"
    assert headers_auth["Idempotency-Key"] == "0195f000-0000-7000-8000-000000000301"
    assert headers_auth["X-Request-Deadline"] == str(deadline_ms)

    headers_proxy = upstream_client.build_outbound_headers(
        iap_oidc_token=f"oidc-for-{MOCK_SAAS_IAP_CLIENT_ID}",
        uct="uct-nl-001",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        idempotency_key="0195f000-0000-7000-8000-000000000301",
        deadline_epoch_ms=deadline_ms,
        iap_header_mode="proxy_authorization",
        backend_app_token="app-token-xyz",
    )
    assert headers_proxy["Proxy-Authorization"].startswith("Bearer ")
    assert headers_proxy["Authorization"] == "Bearer app-token-xyz"
    assert headers_proxy["X-Request-Deadline"] == str(deadline_ms)


def test_tc_it_003_upstream_transport_modes_mcp_http_rest_openapi_local_mock(
    upstream_client,
):
    """TC-IT-003 (FR-040, FR-060, CON-001, CON-009, ADR-021, ADR-022): Verify UpstreamMockSaasClient supports mcp_http, rest_openapi, and local_mock."""
    for mode in ("mcp_http", "rest_openapi", "local_mock"):
        res = upstream_client.call_upstream(
            transport_mode=mode,
            system="WORKWEEK",
            operation="get_my_leave_balances",
            payload={"employee_id": "EMP-NL-001"},
            headers={
                "X-Automation-Origin": "hr-agent-mvp1",
                "X-Base-Url": MOCK_SAAS_BASE_URL,
            },
            timeout_seconds=3.5,
            is_write=False,
        )
        assert res["ok"] is True
        assert res["transport_mode"] == mode


def test_tc_it_004_upstream_openapi_contract_drift_detection(upstream_client):
    """TC-IT-004 (NFR-044): Verify OpenAPI contract validation passes on expected schema and detects breaking field drift."""
    valid_spec = {
        "openapi": "3.1.0",
        "paths": {
            "/workweek/profile": {"get": {}, "patch": {}},
            "/workweek/leave-balances": {"get": {}},
            "/workweek/leave-requests": {"post": {}, "get": {}},
            "/servicedesk/incidents": {"post": {}, "get": {}},
            "/servicedesk/incidents/{ticket_id}/comments": {"post": {}},
            "/servicedesk/incidents/{ticket_id}/status": {"patch": {}},
        },
    }
    res_ok = upstream_client.verify_openapi_contract(openapi_spec=valid_spec)
    assert res_ok["compatible"] is True

    drifted_spec = {"openapi": "3.1.0", "paths": {"/workweek/v2/renamed": {}}}
    res_drift = upstream_client.verify_openapi_contract(openapi_spec=drifted_spec)
    assert res_drift["compatible"] is False
    assert len(res_drift["breaking_changes"]) >= 1


def test_tc_it_005_circuit_breaker_5_failures_open_vs_4_failures_closed_boundary(
    upstream_client, fixed_now
):
    """TC-IT-005 (NFR-040, NFR-041): Circuit breaker stays CLOSED at 4 consecutive failures and trips OPEN at 5 consecutive 5xx/timeout errors within 30s."""
    state_closed_4 = upstream_client.evaluate_circuit_breaker(
        system="SERVICEDESK",
        recent_outcomes=["TIMEOUT", "HTTP_503", "HTTP_502", "TIMEOUT"],
        now_epoch=fixed_now,
    )
    assert state_closed_4["circuit_state"] == "CLOSED"

    state_open_5 = upstream_client.evaluate_circuit_breaker(
        system="SERVICEDESK",
        recent_outcomes=["TIMEOUT", "HTTP_503", "HTTP_502", "TIMEOUT", "HTTP_503"],
        now_epoch=fixed_now,
    )
    assert state_open_5["circuit_state"] == "OPEN"
    assert state_open_5["error_code"] == ErrorCode.UPSTREAM_UNAVAILABLE
    assert state_open_5["half_open_at_epoch"] == fixed_now + 15
    assert "stack_trace" not in state_open_5
