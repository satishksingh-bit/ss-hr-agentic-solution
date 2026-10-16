"""Unit tests for v2.2 evaluation enhancements: Singapore persona (EMP-SG-004), Singapore handbook chunks, citationDeepLinks, and granular LCS trajectory scoring."""

from __future__ import annotations

import json
from pathlib import Path

from app.agent import (
    check_grounding_citations,
    get_employee_profile,
    get_leave_balances,
    get_ticket_status,
    search_policy_handbook,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_singapore_persona_profile_and_balances() -> None:
    """Verify EMP-SG-004 (Singapore) profile, department, manager hierarchy, and 14d/21d leave balances."""
    prof = get_employee_profile(include_contact_details=True, uct="uct-sg-004")
    assert prof["ok"] is True
    llm_view = prof["for_llm"]
    assert llm_view["employee_id"] == "EMP-SG-004"
    assert llm_view["country"] == "SG"
    assert llm_view["department"] == "APAC Engineering"
    assert llm_view["manager_id"] == "EMP-SG-MGR-01"
    assert llm_view["manager_name"] == "Rajesh Nair"
    assert llm_view["home_address"]["country"] == "SG"

    bal = get_leave_balances(leave_type="ALL", uct="uct-sg-004")
    assert bal["ok"] is True
    balances = bal["for_llm"]["balances"]
    assert balances["VACATION"]["accrued_days"] == 21.0
    assert balances["SICK"]["accrued_days"] == 14.0


def test_singapore_policy_chunks_and_citation_deep_links() -> None:
    """Verify Singapore policy retrieval returns citationDeepLinks and covers shift accrual, ramp-back, gift cards, and room salons."""
    vac_res = search_policy_handbook(
        query="8 years tenure 12-hour shifts vacation accrual", country="SG", top_k=3
    )
    assert vac_res["ok"] is True
    assert vac_res["citationDeepLinks"]
    top_chunk = vac_res["chunks"][0]
    assert top_chunk["doc_id"] == "POL-SG-VAC-2026"
    assert "1.5" in top_chunk["text"]

    draft = f"{top_chunk['text']} [POL-SG-VAC-2026#20.2]"
    chk = check_grounding_citations(
        query="12-hour shift vacation accrual",
        draft_answer=draft,
        retrieved_chunks=vac_res["chunks"],
        country="SG",
    )
    assert chk["verdict"] == "PASS"
    assert chk["citationDeepLinks"] == [
        "https://hr-handbook.altostrat.com/policies/POL-SG-VAC-2026#section-20.2"
    ]


def test_itsm_ticket_meta_properties_valid_itsm_13() -> None:
    """Verify ServiceImmediately tickets include assignment_group, sla_target_hours, and sla_status."""
    tkt = get_ticket_status(ticket_id="INC123456", uct="uct-nl-001")
    assert tkt["ok"] is True
    data = tkt["for_llm"]
    assert data["priority"] == "2 - High"
    assert data["assignee"] == "EU Desktop Support"
    assert data["assignment_group"] == "EMEA Endpoint Engineering"
    assert data["sla_status"] == "WITHIN_SLA"
    assert data["sla_target_hours"] == 8


def test_eval_config_granular_trajectory_formula() -> None:
    """Verify eval_config.json defines GRANULAR_PARTIAL_CREDIT_LCS weights and token/fault-tolerance criteria."""
    cfg = json.loads(
        (REPO_ROOT / "tests" / "eval" / "eval_config.json").read_text(encoding="utf-8")
    )
    traj_crit = cfg["criteria"]["tool_trajectory_avg_score"]
    assert traj_crit["match_type"] == "GRANULAR_PARTIAL_CREDIT_LCS"
    assert traj_crit["weights"] == {
        "recall": 0.50,
        "lcs_order": 0.35,
        "precision_factor": 0.15,
    }
    assert "fault_tolerance_and_resilience" in cfg["custom_metrics"]
    assert "token_and_cost_efficiency" in cfg["custom_metrics"]
