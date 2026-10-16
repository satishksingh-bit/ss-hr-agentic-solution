"""Upstream Mock SaaS Client & Transport Adapter for ADK App (SDD §5.1, §8.1)."""

from __future__ import annotations

from hr_agent.upstream_client import (
    _EXPECTED_OPENAPI_PATHS,
    _OPERATION_ENDPOINTS,
    MOCK_SAAS_BASE_URL,
    MOCK_SAAS_DOCS_URL,
    MOCK_SAAS_IAP_CLIENT_ID,
    MOCK_SAAS_OPENAPI_URL,
    UpstreamMockSaasClient,
    create_http_server,
)

__all__ = [
    "MOCK_SAAS_BASE_URL",
    "MOCK_SAAS_DOCS_URL",
    "MOCK_SAAS_IAP_CLIENT_ID",
    "MOCK_SAAS_OPENAPI_URL",
    "_EXPECTED_OPENAPI_PATHS",
    "_OPERATION_ENDPOINTS",
    "UpstreamMockSaasClient",
    "create_http_server",
]
