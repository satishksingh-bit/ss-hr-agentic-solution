"""Create-Only Startup Seed for hr-flags/runtime_flags/global (BH-01-3-05, NFR-045, 03-deployment §5.2)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hr_agent.models import FirestoreDatabaseId
from hr_agent.ops import HrAdminService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create-only seed for runtime_flags/global in hr-flags"
    )
    parser.add_argument(
        "--env",
        default="local",
        help="Target environment (local, local-staging, local-prod, dev, test, prodpilot)",
    )
    parser.add_argument("--project", default="hr-agent-local", help="GCP project ID")
    parser.add_argument(
        "--database",
        default=FirestoreDatabaseId.HR_FLAGS.value,
        help="Firestore database ID",
    )
    parser.add_argument(
        "--create-only",
        action="store_true",
        default=True,
        help="Never overwrite an existing runtime_flags/global document",
    )
    args = parser.parse_args(argv)

    svc = HrAdminService()
    res = svc.seed_runtime_flags_if_absent(now_epoch=int(time.time()))
    print(
        json.dumps(
            {
                "ok": True,
                "env": args.env,
                "project": args.project,
                "database": args.database,
                "create_only": args.create_only,
                **res,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
