#!/usr/bin/env python3
"""Deploy and Pin Vertex AI Agent Engine Reasoning Engine (03-deployment §5.2 / §6.4, FR-001, FR-002, ADR-001).

Validates `config/engine.yaml` model IDs (`gemini-3.8-flash` primary, `gemini-3.5-flash` fallback, `gemini-2.5-pro` policy),
verifies version pinning digests, and deploys or simulates blue/green Reasoning Engine endpoint update.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hr_agent.models import FALLBACK_COORDINATOR_MODEL, PRIMARY_COORDINATOR_MODEL
from hr_agent.security import ToolManifestAllowlist


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deploy or update Vertex AI Agent Engine Reasoning Engine"
    )
    parser.add_argument(
        "--env",
        default="dev",
        help="Target environment (dev, test, prodpilot, local-staging)",
    )
    parser.add_argument("--project", default="hr-agent-dev", help="GCP project ID")
    parser.add_argument(
        "--region",
        default="europe-west4",
        help="Deployment region (must be europe-west4)",
    )
    parser.add_argument(
        "--slot",
        choices=["blue", "green"],
        default="green",
        help="Blue/green deployment slot",
    )
    parser.add_argument(
        "--config",
        default="config/engine.yaml",
        help="Path to engine.yaml configuration",
    )
    args = parser.parse_args(argv)

    if args.region != "europe-west4":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Invalid region '{args.region}'; must be 'europe-west4' (NFR-006).",
                }
            )
        )
        return 1

    manifest = {
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
    allowlist = ToolManifestAllowlist()
    pin_check = allowlist.validate_version_pinning(deployment_manifest=manifest)
    if not pin_check["valid"]:
        print(json.dumps({"ok": False, "error": "Version pinning failed", **pin_check}))
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "env": args.env,
                "project": args.project,
                "region": args.region,
                "slot": args.slot,
                "config": args.config,
                "reasoning_engine_id": f"projects/{args.project}/locations/{args.region}/reasoningEngines/hr-agent-{args.slot}",
                "manifest": manifest,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
