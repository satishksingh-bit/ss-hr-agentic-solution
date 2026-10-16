#!/usr/bin/env python3
"""Assert P95 Latency SLA Budgets across Policy Q&A, Single-System, and Multi-Agent Composite Paths (SDD §1.4, §9.5)."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = REPO_ROOT / "artifacts" / "eval_summary.json"

LATENCY_BUDGETS_MS = {
    "policy_qa_p95_ms": 3500.0,
    "single_system_p95_ms": 4500.0,
    "composite_multi_agent_p95_ms": 6500.0,
}


def main() -> int:
    if not SUMMARY_PATH.exists():
        print(f"ERROR: Evaluation summary not found at {SUMMARY_PATH}.")
        return 1

    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    latency = summary.get("latency_metrics_ms", {})
    breaches: list[str] = []

    for key, max_ms in LATENCY_BUDGETS_MS.items():
        actual = float(latency.get(key, 0.0))
        if actual > max_ms:
            breaches.append(f"{key}: actual={actual:.1f}ms > budget={max_ms:.1f}ms")

    result = {
        "sla_passed": len(breaches) == 0,
        "budgets_ms": LATENCY_BUDGETS_MS,
        "actual_latency_ms": latency,
        "breaches": breaches,
    }
    print(json.dumps(result, indent=2))
    return 0 if not breaches else 1


if __name__ == "__main__":
    raise SystemExit(main())
