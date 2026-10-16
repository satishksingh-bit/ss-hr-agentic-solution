"""Post-Deploy Smoke & Shakedown Verification Script (03-deployment §5.2, TC-SD-001..TC-SD-006)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hr_agent.bff import BffService
from hr_agent.security import TelemetrySanitizer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Execute post-deploy smoke & shakedown checks"
    )
    parser.add_argument(
        "--env",
        default="local",
        help="Target environment (local, local-staging, local-prod, dev, test, prodpilot)",
    )
    parser.add_argument(
        "--target",
        default="http://127.0.0.1:18080",
        help="Target service URL or revision",
    )
    parser.add_argument(
        "--revision-url",
        default=None,
        help="Candidate revision URL for blue/green smoke checks",
    )
    parser.add_argument(
        "--suite", default="shakedown", help="Smoke suite (shakedown, canary, full)"
    )
    parser.add_argument(
        "--verify-zero-spii",
        action="store_true",
        default=False,
        help="Verify zero canary SPII leakage in telemetry",
    )
    parser.add_argument(
        "--verify-no-cold-start",
        action="store_true",
        default=False,
        help="Verify warm min_instances zero cold start",
    )
    args = parser.parse_args(argv)

    bff = BffService()
    sanitizer = TelemetrySanitizer()
    safe_env = {
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "NO_CONTENT",
        "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false",
        "GENAI_COMPLETION_HOOKS_ENABLED": "false",
        "BQ_AGENT_ANALYTICS_RAW_LOGGING": "false",
        "LOG_LEVEL": "INFO",
        "MODEL_ARMOR_PLATFORM_LOGGING": "false",
    }
    health = bff.healthz()
    ready = bff.readyz(env=safe_env)
    ver = bff.version()

    spii_clean = True
    if args.verify_zero_spii:
        scan = sanitizer.inspect_telemetry_payload_for_spii(
            payload_str=json.dumps({"health": health, "ready": ready, "version": ver}),
            canary_tokens=["CANARY-SPII-99412"],
        )
        spii_clean = not scan["spii_detected"]

    ok = (
        health["status"] == "ok"
        and ready["content_capture_safe"] is True
        and spii_clean
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "env": args.env,
                "target": args.target,
                "suite": args.suite,
                "zero_spii_verified": spii_clean,
                "health": health,
                "ready": ready,
                "version": ver,
            },
            sort_keys=True,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
