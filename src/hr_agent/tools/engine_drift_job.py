"""Daily OpenAPI & Reasoning Engine Drift Detection Job (C11, SDD §5.9, §7.7, NFR-044, FR-002)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from hr_agent.models import FALLBACK_COORDINATOR_MODEL, PRIMARY_COORDINATOR_MODEL
from hr_agent.security import ToolManifestAllowlist
from hr_agent.upstream_client import UpstreamMockSaasClient

DEFAULT_OPENAPI_SPEC: dict[str, Any] = {
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

DEFAULT_DEPLOYMENT_MANIFEST: dict[str, Any] = {
    "agent_code_version": "0.1.0-mvp1",
    "prompt_bundle_digest": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "tool_manifest_digest": "sha256:7d865e959b2466918c9863afca942d0fb89d7c9ac0c99bafc3749504ded97730",
    "model_ids": {
        "coordinator": PRIMARY_COORDINATOR_MODEL,
        "coordinator_fallback": FALLBACK_COORDINATOR_MODEL,
        "policy": "gemini-2.5-pro",
    },
    "safety_template_version": "v1",
}


def run_drift_check(
    *,
    openapi_spec: dict[str, Any] | None = None,
    deployment_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify both upstream OpenAPI schema compatibility and Reasoning Engine version pinning."""
    client = UpstreamMockSaasClient()
    allowlist = ToolManifestAllowlist()
    spec = openapi_spec if openapi_spec is not None else DEFAULT_OPENAPI_SPEC
    manifest = (
        deployment_manifest
        if deployment_manifest is not None
        else DEFAULT_DEPLOYMENT_MANIFEST
    )
    openapi_res = client.verify_openapi_contract(openapi_spec=spec)
    pinning_res = allowlist.validate_version_pinning(deployment_manifest=manifest)
    drift_free = bool(openapi_res["compatible"] and pinning_res["valid"])
    return {
        "ok": drift_free,
        "job": "engine-drift-job",
        "openapi_compatible": openapi_res["compatible"],
        "openapi_breaking_changes": openapi_res["breaking_changes"],
        "version_pinning_valid": pinning_res["valid"],
        "missing_pins": pinning_res.get("missing_keys", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run OpenAPI & Reasoning Engine Drift Detection Job (C11)"
    )
    for flag, default in (
        ("--env", "prodpilot"),
        ("--config", "config/engine.yaml"),
        ("--state-dir", ""),
    ):
        parser.add_argument(flag, default=default)
    args, res = parser.parse_args(argv), run_drift_check()
    print(
        json.dumps(
            {
                "env": args.env,
                "config": args.config,
                "state_dir": args.state_dir,
                **res,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
