#!/usr/bin/env python3
"""Release Gate Verification for Grounded RAG & Seeded Unsupported-Claim Benchmark (03-deployment §6.3, NFR-030, NFR-031, BH-01-3-03)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hr_agent.policy_rag import PolicyRagService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify NFR-030/NFR-031 Grounding & Hallucination Release Gate"
    )
    parser.add_argument("--env", default="test", help="Target environment")
    parser.add_argument(
        "--suite",
        default="tests/unit/test_policy_grounding_and_sync.py",
        help="Benchmark suite path",
    )
    parser.add_argument(
        "--min-block-rate",
        type=float,
        default=0.90,
        help="Minimum unsupported-claim block rate",
    )
    parser.add_argument(
        "--author-id", default="prompt-author-1", help="Prompt author identity"
    )
    parser.add_argument(
        "--curator-id",
        default="eval-curator-2",
        help="Independent eval curator identity",
    )
    args = parser.parse_args(argv)

    rag = PolicyRagService()
    seeded_cases = [
        {
            "perturbation": "SCOPE_SHIFT",
            "claim": "NL leave applies to external contractors",
        },
        {
            "perturbation": "DROPPED_CONDITION",
            "claim": "Every employee is eligible for EUR 750 monitor regardless of tenure",
        },
        {
            "perturbation": "CHANGED_QUANTIFIER",
            "claim": "Full-time NL employees receive 35 paid vacation days",
        },
        {
            "perturbation": "TEMPORAL_SHIFT",
            "claim": "Retired 2022 NL leave policy is currently active",
        },
    ]
    res_primary = rag.evaluate_seeded_unsupported_claim_suite(
        seeded_cases=seeded_cases,
        verifier_mode="CHECK_GROUNDING",
        author_id=args.author_id,
        curator_id=args.curator_id,
    )
    res_fallback = rag.evaluate_seeded_unsupported_claim_suite(
        seeded_cases=seeded_cases,
        verifier_mode="FALLBACK_VERIFIER",
        author_id=args.author_id,
        curator_id=args.curator_id,
    )
    passed = bool(
        res_primary["release_gate_passed"] and res_fallback["release_gate_passed"]
    )
    print(
        json.dumps(
            {
                "ok": passed,
                "env": args.env,
                "check_grounding": res_primary,
                "fallback_verifier": res_fallback,
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
