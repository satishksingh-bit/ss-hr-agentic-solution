"""GDPR Right-to-Erasure (DSAR) CLI Script (03-deployment §5.3, FR-015, NFR-005, ADR-019)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import time

from hr_agent.ops import DsarToolService


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute GDPR DSAR user erasure")
    parser.add_argument("--employee-id", required=True)
    parser.add_argument(
        "--caller-sa", default="dsar-tool-sa@hr-agent-prodpilot.iam.gserviceaccount.com"
    )
    args = parser.parse_args()

    svc = DsarToolService()
    res = svc.execute_dsar_deletion(
        employee_id=args.employee_id,
        caller_sa=args.caller_sa,
        now_epoch=int(time.time()),
    )
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
