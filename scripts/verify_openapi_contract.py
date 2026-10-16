"""Upstream Mock SaaS OpenAPI Contract Drift Verifier (NFR-044, CON-009)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hr_agent.upstream_client import UpstreamMockSaasClient


def main() -> int:
    client = UpstreamMockSaasClient()
    baseline_spec = {
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
    res = client.verify_openapi_contract(openapi_spec=baseline_spec)
    print(json.dumps(res))
    return 0 if res["compatible"] else 1


if __name__ == "__main__":
    sys.exit(main())
