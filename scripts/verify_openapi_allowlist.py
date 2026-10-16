#!/usr/bin/env python3
"""Verify OpenAPI 3.1.0 Specifications Against Strict Field Allow-List & Mock SaaS Contract (SDD §5.1, §8.1, §9.5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from hr_agent.upstream_client import UpstreamMockSaasClient

FORBIDDEN_SPII_FIELDS = frozenset(
    {
        "salary",
        "base_salary",
        "compensation",
        "bonus_target",
        "stock_equity",
        "bank_account_iban",
        "iban",
        "national_id_ssn",
        "ssn",
        "bsn",
        "medical_diagnosis",
        "performance_rating",
    }
)


def _collect_schema_properties(spec: dict[str, Any]) -> set[str]:
    schemas = spec.get("components", {}).get("schemas", {})
    found: set[str] = set()
    for schema_def in schemas.values():
        props = schema_def.get("properties", {})
        for prop_name, prop_val in props.items():
            found.add(prop_name.lower())
            if isinstance(prop_val, dict) and "properties" in prop_val:
                for nested_name in prop_val["properties"]:
                    found.add(nested_name.lower())
    return found


def main() -> int:
    ww_path = REPO_ROOT / "specs" / "workweek-v1.0.0.yaml"
    si_path = REPO_ROOT / "specs" / "service-immediately-v1.0.0.yaml"

    ww_spec = yaml.safe_load(ww_path.read_text(encoding="utf-8"))
    si_spec = yaml.safe_load(si_path.read_text(encoding="utf-8"))

    ww_props = _collect_schema_properties(ww_spec)
    si_props = _collect_schema_properties(si_spec)

    leaked_ww = sorted(ww_props & FORBIDDEN_SPII_FIELDS)
    leaked_si = sorted(si_props & FORBIDDEN_SPII_FIELDS)

    combined_paths: dict[str, Any] = {}
    combined_paths.update(ww_spec.get("paths", {}))
    combined_paths.update(si_spec.get("paths", {}))

    client = UpstreamMockSaasClient()
    contract_res = client.verify_openapi_contract(
        openapi_spec={"openapi": "3.1.0", "paths": combined_paths}
    )

    ok = not leaked_ww and not leaked_si and contract_res["compatible"]
    report = {
        "ok": ok,
        "workweek_spec": str(ww_path.relative_to(REPO_ROOT)),
        "servicedesk_spec": str(si_path.relative_to(REPO_ROOT)),
        "leaked_workweek_fields": leaked_ww,
        "leaked_servicedesk_fields": leaked_si,
        "mock_saas_contract_compatible": contract_res["compatible"],
        "breaking_changes": contract_res["breaking_changes"],
    }
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
