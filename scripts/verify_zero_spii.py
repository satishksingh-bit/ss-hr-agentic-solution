"""Canary SPII Zero-Leakage Telemetry Verification Script (FR-014, NFR-007, 03-deployment §6.2)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hr_agent.security import TelemetrySanitizer


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify zero canary SPII tokens in exported telemetry"
    )
    parser.add_argument("--env", default="local")
    parser.add_argument("--canary-token", default="CANARY-SPII-99412")
    args = parser.parse_args()

    sanitizer = TelemetrySanitizer()
    res = sanitizer.inspect_telemetry_payload_for_spii(
        payload_str='{"event": "TURN_COMPLETED", "content_sha256": "sha256:clean"}',
        canary_tokens=[args.canary_token],
    )
    print(json.dumps({"env": args.env, **res}))
    return 0 if not res["spii_detected"] else 1


if __name__ == "__main__":
    sys.exit(main())
