"""ServiceImmediately ITSM Proxy Service — Cloud Run MCP & OpenAPI Adapter (SDD §5.1.2, §8.1).

Exposes the ServiceImmediately ITSM MCP tool server over Streamable HTTP (/mcp, /mcp/v1/tools/{tool_name})
and integrates with the hosted Mock SaaS backend (https://mock-saas.aishprabhat.demo.altostrat.com/docs)
via UpstreamMockSaasClient (`mcp_http`, `rest_openapi`, and `local_mock` transport modes).
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from app.upstream_client import (
    MOCK_SAAS_BASE_URL,
    MOCK_SAAS_DOCS_URL,
    MOCK_SAAS_IAP_CLIENT_ID,
    MOCK_SAAS_OPENAPI_URL,
    UpstreamMockSaasClient,
)
from hr_agent.servicedesk_mcp import ServiceDeskMcpServer, evaluate_priority_rules

DEFAULT_PORT = int(
    os.environ.get("SERVICEDESK_PROXY_PORT", os.environ.get("PORT", "8082"))
)
TRANSPORT_MODE = os.environ.get("UPSTREAM_TRANSPORT_MODE", "local_mock")

servicedesk_server = ServiceDeskMcpServer()
upstream_client = UpstreamMockSaasClient()

proxy_app = FastAPI(
    title="ServiceImmediately ITSM Proxy Service",
    version="1.0.0",
    description=(
        f"Cloud Run MCP Proxy for ServiceImmediately ITSM targeting {MOCK_SAAS_DOCS_URL} "
        f"(IAP Client ID: {MOCK_SAAS_IAP_CLIENT_ID})."
    ),
)


@proxy_app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "servicedesk-mcp-proxy",
        "transport_mode": TRANSPORT_MODE,
        "upstream_base_url": MOCK_SAAS_BASE_URL,
        "upstream_openapi_url": MOCK_SAAS_OPENAPI_URL,
    }


@proxy_app.post("/mcp/v1/tools/{tool_name}")
@proxy_app.post("/mcp/{tool_name}")
async def invoke_servicedesk_tool(
    tool_name: str,
    request: Request,
    x_user_context_assertion: str | None = Header(default=None),
) -> dict[str, Any]:
    payload: dict[str, Any] = await request.json()
    if x_user_context_assertion and "uct" not in payload:
        payload["uct"] = x_user_context_assertion
    fn = getattr(servicedesk_server, tool_name, None)
    if fn is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown ServiceImmediately tool: {tool_name}"
        )
    return fn(**payload)


__all__ = [
    "ServiceDeskMcpServer",
    "evaluate_priority_rules",
    "proxy_app",
    "servicedesk_server",
]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(proxy_app, host="0.0.0.0", port=DEFAULT_PORT)
