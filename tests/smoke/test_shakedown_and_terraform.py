"""Deployment Shakedown and Smoke tests (Phase 07, TC-SD-001..TC-SD-006)."""

from __future__ import annotations

import pytest

from hr_agent.upstream_client import (
    MOCK_SAAS_BASE_URL,
    MOCK_SAAS_DOCS_URL,
    MOCK_SAAS_IAP_CLIENT_ID,
    MOCK_SAAS_OPENAPI_URL,
)

pytestmark = pytest.mark.smoke


def test_tc_sd_001_healthz_readyz_five_switches_and_version_endpoints(bff_service):
    """TC-SD-001 (FR-001, FR-002, FR-014, NFR-007, NFR-022, BH-04-1-07): Verify /healthz, /readyz (with all 5 content-capture switches safe), and /version."""
    health = bff_service.healthz()
    assert health["status"] == "ok"

    ready = bff_service.readyz(
        env={
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "NO_CONTENT",
            "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false",
            "GENAI_COMPLETION_HOOKS_ENABLED": "false",
            "BQ_AGENT_ANALYTICS_RAW_LOGGING": "false",
            "LOG_LEVEL": "INFO",
            "MODEL_ARMOR_PLATFORM_LOGGING": "false",
        }
    )
    assert ready["status"] == "ready"
    assert ready["content_capture_safe"] is True

    ver = bff_service.version()
    assert "version" in ver
    assert "git_sha" in ver
    assert "tool_manifest_digest" in ver
    assert "prompt_bundle_digest" in ver


def test_tc_sd_002_cloudtop_dual_mode_loopback_port_map_verification(
    topology_validator,
):
    """TC-SD-002 (ADR-022, 03-deployment §2.1, BH-04-1-04): Verify all local Cloudtop services bind strictly to 127.0.0.1 on the canonical port map 18080..18090 (local-staging) and 28080..28090 (local-prod)."""
    services = [
        {
            "name": "hr-chat-bff",
            "host": "127.0.0.1",
            "local_staging_port": 18080,
            "local_prod_port": 28080,
        },
        {
            "name": "hr-chat-bff-candidate",
            "host": "127.0.0.1",
            "local_staging_port": 18081,
            "local_prod_port": 28081,
        },
        {
            "name": "hr-agent-blue",
            "host": "127.0.0.1",
            "local_staging_port": 18082,
            "local_prod_port": 28082,
        },
        {
            "name": "hr-agent-green",
            "host": "127.0.0.1",
            "local_staging_port": 18083,
            "local_prod_port": 28083,
        },
        {
            "name": "workweek-mcp",
            "host": "127.0.0.1",
            "local_staging_port": 18084,
            "local_prod_port": 28084,
        },
        {
            "name": "servicedesk-mcp",
            "host": "127.0.0.1",
            "local_staging_port": 18085,
            "local_prod_port": 28085,
        },
        {
            "name": "hr-admin",
            "host": "127.0.0.1",
            "local_staging_port": 18086,
            "local_prod_port": 28086,
        },
        {
            "name": "local-mock-saas-harness",
            "host": "127.0.0.1",
            "local_staging_port": 18090,
            "local_prod_port": 28090,
        },
    ]
    res = topology_validator.verify_cloudtop_service_bindings(services=services)
    assert res["all_loopback_only"] is True
    assert res["zero_wildcard_bindings"] is True
    assert res["local_firestore_sqlite_dbs_count"] == 6


def test_tc_sd_003_runtime_flags_seed_shakedown(hr_admin, fixed_now):
    """TC-SD-003 (NFR-045, BH-01-3-05): Verify runtime_flags/global document is seeded with writes_enabled=True, assistant_enabled=True."""
    res = hr_admin.seed_runtime_flags_if_absent(now_epoch=fixed_now)
    assert res["writes_enabled"] is True
    assert res["assistant_enabled"] is True


def test_tc_sd_004_persona_seed_and_tw_exclusion_shakedown(
    region_guard, persona_nl, persona_cn, persona_tw_blocked
):
    """TC-SD-004 (FR-020, FR-027, FR-028): Verify synthetic NL and CN personas pass seed validation and TW persona is blocked."""
    assert region_guard.validate_persona_seed(persona_nl) is True
    assert region_guard.validate_persona_seed(persona_cn) is True
    assert region_guard.validate_persona_seed(persona_tw_blocked) is False


def test_tc_sd_005_canary_spii_zero_leak_shakedown(telemetry_sanitizer, canary_spii):
    """TC-SD-005 (FR-014, NFR-007): Shakedown probe verifying CANARY-SPII-99412 is never captured in exported telemetry."""
    res = telemetry_sanitizer.inspect_telemetry_payload_for_spii(
        payload_str='{"service": "hr-chat-bff", "event": "SHAKEDOWN_PROBE", "content_sha256": "sha256:clean"}',
        canary_tokens=[canary_spii],
    )
    assert res["spii_detected"] is False


def test_tc_sd_006_upstream_mock_saas_endpoint_and_iap_client_shakedown(
    upstream_client,
):
    """TC-SD-006 (CON-001, CON-008, CON-009, ADR-021): Verify upstream Mock SaaS base URL, /docs, /openapi.json, and IAP Client ID configuration."""
    assert MOCK_SAAS_BASE_URL == "https://mock-saas.aishprabhat.demo.altostrat.com"
    assert MOCK_SAAS_DOCS_URL == "https://mock-saas.aishprabhat.demo.altostrat.com/docs"
    assert (
        MOCK_SAAS_OPENAPI_URL
        == "https://mock-saas.aishprabhat.demo.altostrat.com/openapi.json"
    )
    assert (
        MOCK_SAAS_IAP_CLIENT_ID
        == "332638604308-825bkuk2cjp8h06t1g41o2lvk8oq9cnk.apps.googleusercontent.com"
    )

    headers = upstream_client.build_outbound_headers(
        iap_oidc_token="shakedown-oidc-jwt",
        uct="shakedown-uct-jwt",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    )
    assert headers["X-Automation-Origin"] == "hr-agent-mvp1"
