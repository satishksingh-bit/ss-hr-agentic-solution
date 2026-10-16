"""Idempotent Persona Seed Script for hr-personas (03-deployment §5.2, FR-020, FR-027, FR-028)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hr_agent.models import (
    ALLOWED_PROFILE_COUNTRIES,
    EmployeePersona,
    FirestoreDatabaseId,
    LocalSqliteStore,
)
from hr_agent.security import RegionAccessGuard

SYNTHETIC_PERSONAS: tuple[EmployeePersona, ...] = (
    EmployeePersona(
        employee_id="EMP-NL-001",
        email="eva.bakker.synthetic@example.invalid",
        full_name="Eva Bakker",
        country="NL",
        legal_entity="LE-NL-BV",
        department="Engineering",
        role="Senior Software Engineer",
        manager_employee_id="EMP-NL-MGR-01",
        manager_name="Pieter Jansen",
        manager_email="pieter.jansen.synthetic@example.invalid",
        hire_date="2022-03-01",
        fte=1.0,
        preferred_language="en",
        timezone="Europe/Amsterdam",
        work_location_type="REMOTE",
        personal_phone="+31612345678",
        home_address={
            "street": "Keizersgracht 100",
            "city": "Amsterdam",
            "postal_code": "1015 AA",
            "country": "NL",
        },
        roles=("employee",),
    ),
    EmployeePersona(
        employee_id="EMP-DE-002",
        email="lukas.mueller.synthetic@example.invalid",
        full_name="Lukas Mueller",
        country="DE",
        legal_entity="LE-DE-GMBH",
        department="Product Management",
        role="Product Manager",
        manager_employee_id="EMP-DE-MGR-01",
        manager_name="Hannah Schmidt",
        manager_email="hannah.schmidt.synthetic@example.invalid",
        hire_date="2023-01-15",
        fte=0.8,
        preferred_language="en",
        timezone="Europe/Berlin",
        work_location_type="HYBRID",
        personal_phone="+4915123456789",
        home_address={
            "street": "Friedrichstrasse 50",
            "city": "Berlin",
            "postal_code": "10117",
            "country": "DE",
        },
        roles=("employee",),
    ),
    EmployeePersona(
        employee_id="EMP-CN-003",
        email="wei.chen.synthetic@example.invalid",
        full_name="Wei Chen",
        country="CN",
        legal_entity="LE-CN-WFOE",
        department="Cloud Operations",
        role="Site Reliability Engineer",
        manager_employee_id="EMP-CN-MGR-01",
        manager_name="Lin Zhang",
        manager_email="lin.zhang.synthetic@example.invalid",
        hire_date="2021-09-01",
        fte=1.0,
        preferred_language="en",
        timezone="Asia/Shanghai",
        work_location_type="REMOTE",
        personal_phone="+8613800138000",
        home_address={
            "street": "Century Avenue 88",
            "city": "Shanghai",
            "postal_code": "200120",
            "country": "CN",
        },
        roles=("employee",),
    ),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed synthetic personas into hr-personas"
    )
    parser.add_argument(
        "--env",
        default="local",
        help="Target environment (local, local-staging, local-prod, dev, test, prodpilot)",
    )
    parser.add_argument("--project", default="hr-agent-local", help="GCP project ID")
    parser.add_argument(
        "--database",
        default=FirestoreDatabaseId.HR_PERSONAS.value,
        help="Firestore database name",
    )
    parser.add_argument(
        "--source",
        default="config/personas.seed.json",
        help="Optional persona seed JSON path",
    )
    parser.add_argument(
        "--validate-no-tw",
        action="store_true",
        default=True,
        help="Reject any Taiwan (TW) persona",
    )
    args = parser.parse_args(argv)

    assert "TW" not in ALLOWED_PROFILE_COUNTRIES
    assert "CN" in ALLOWED_PROFILE_COUNTRIES

    guard = RegionAccessGuard()
    store = LocalSqliteStore(FirestoreDatabaseId.HR_PERSONAS)
    now_epoch = int(time.time())

    seeded_ids: list[str] = []
    for persona in SYNTHETIC_PERSONAS:
        if args.validate_no_tw and not guard.validate_persona_seed(persona):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"Persona {persona.employee_id} failed geo/language validation",
                    }
                )
            )
            return 1
        store.put_document("personas", persona.employee_id, asdict(persona))
        seeded_ids.append(persona.employee_id)

    print(
        json.dumps(
            {
                "ok": True,
                "env": args.env,
                "project": args.project,
                "database": args.database,
                "source": args.source,
                "seeded_personas": seeded_ids,
                "zero_tw_verified": True,
                "seeded_at_epoch": now_epoch,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
