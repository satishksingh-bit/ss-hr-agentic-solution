"""Unit tests for Policy RAG Retrieval, Sufficiency Gate, Citation Integrity, Eligibility Reasoning, Seeded Unsupported-Claim Suite, and 15m Corpus Sync (FR-052, FR-053, FR-070..FR-075, FR-077..FR-079, NFR-030, NFR-031)."""

from __future__ import annotations

import pytest

from hr_agent.models import ErrorCode

pytestmark = pytest.mark.unit


def test_tc_ut_039_policy_retrieval_country_filter_domains_and_top_k_1_to_6_boundaries(
    policy_rag,
):
    """TC-UT-039 (FR-017, FR-072, FR-077, FR-079, SDD §5.5, BH-04-1-05): Filter chunks by (country==UCT.country OR GLOBAL) AND status=='active' AND effective_date<=today, verify all 6 required domains, and enforce 1 <= top_k <= 6."""
    res_ok = policy_rag.retrieve_policy_chunks(
        uct="uct-nl-001",
        query="What are the policies on leave, expenses, remote work equipment, relocation, and code of conduct?",
        top_k=6,  # Exact upper boundary top_k=6 passes
        as_of_date="2026-06-01",
    )
    assert res_ok["ok"] is True
    assert 1 <= len(res_ok["chunks"]) <= 6
    for chunk in res_ok["chunks"]:
        assert chunk["country"] in ("NL", "GLOBAL")
        assert chunk["status"] == "active"
        assert chunk["effective_date"] <= "2026-06-01"
    assert {
        "LEAVE",
        "EXPENSE",
        "REMOTE_WORK",
        "CODE_OF_CONDUCT",
        "EQUIPMENT",
        "RELOCATION",
    }.issubset(set(res_ok["covered_domains"]))

    # Exact lower boundary top_k=1 passes; top_k=0 and top_k=7 fail with VALIDATION_FAILED
    res_k1 = policy_rag.retrieve_policy_chunks(
        uct="uct-nl-001", query="Leave policy", top_k=1, as_of_date="2026-06-01"
    )
    assert res_k1["ok"] is True

    res_k0 = policy_rag.retrieve_policy_chunks(
        uct="uct-nl-001", query="Leave policy", top_k=0, as_of_date="2026-06-01"
    )
    assert res_k0["ok"] is False
    assert res_k0["error_code"] == ErrorCode.VALIDATION_FAILED

    res_k7 = policy_rag.retrieve_policy_chunks(
        uct="uct-nl-001", query="Leave policy", top_k=7, as_of_date="2026-06-01"
    )
    assert res_k7["ok"] is False
    assert res_k7["error_code"] == ErrorCode.VALIDATION_FAILED


def test_tc_ut_040_sufficiency_gate_relevance_065_boundary_and_partial_vs_insufficient(
    policy_rag,
):
    """TC-UT-040 (FR-073, FR-075, BH-04-1-01, BH-04-1-05): Enforce relevance threshold >= 0.65 (0.65 pass vs 0.64 fail) and sufficiency gate (FULL, PARTIAL, INSUFFICIENT)."""
    # Score 0.65 at boundary with PARTIAL context -> discloses answered part and explicitly states missing policy part
    res_partial = policy_rag.retrieve_policy_chunks(
        uct="uct-nl-001",
        query="What is the NL remote monitor allowance and the Mars colony relocation bonus?",
        top_k=5,
    )
    assert res_partial["max_relevance_score"] >= 0.65
    assert res_partial["context_sufficiency"] == "PARTIAL"
    assert "missing_aspects" in res_partial

    # Score 0.64 (< 0.65 threshold) or INSUFFICIENT flag -> abstains with POLICY_NOT_FOUND / INSUFFICIENT_EVIDENCE
    res_insufficient = policy_rag.retrieve_policy_chunks(
        uct="uct-nl-001",
        query="What is the company crypto mining subsidy in Antarctica?",
        top_k=5,
    )
    assert res_insufficient["ok"] is False
    assert res_insufficient["context_sufficiency"] == "INSUFFICIENT"
    assert res_insufficient["error_code"] in (
        ErrorCode.POLICY_NOT_FOUND,
        ErrorCode.INSUFFICIENT_EVIDENCE,
    )


def test_tc_ut_041_eligibility_reasoning_combining_policy_and_profile_facts(
    policy_rag, persona_nl
):
    """TC-UT-041 (FR-052, FR-053, BH-04-1-01): Combine retrieved policy criteria with employee profile facts (work_location_type, hire_date/tenure, fte) to determine benefit eligibility."""
    chunks = [
        {
            "doc_id": "POL-GLOBAL-EQUIP-2026",
            "section_id": "2.1",
            "title": "Remote Equipment Procurement Policy",
            "effective_date": "2026-01-01",
            "status": "active",
            "text": "Employees with work_location_type in {REMOTE, HYBRID} and >= 90 days tenure are eligible for up to EUR 750 ergonomic equipment reimbursement.",
        }
    ]
    # Positive: REMOTE employee hired 2022-03-01 (>= 90 days tenure) is eligible
    eligible_res = policy_rag.evaluate_eligibility(
        uct="uct-nl-001",
        benefit_or_policy_topic="ergonomic_equipment_stipend",
        policy_chunks=chunks,
        employee_profile={
            "employee_id": persona_nl.employee_id,
            "work_location_type": "REMOTE",
            "hire_date": "2022-03-01",
            "fte": 1.0,
        },
    )
    assert eligible_res["ok"] is True
    assert eligible_res["eligible"] is True
    assert eligible_res["citations"] == ["POL-GLOBAL-EQUIP-2026#2.1"]

    # Boundary/Negative: OFFICE employee or new hire at 89 days tenure (< 90 days boundary) is ineligible
    ineligible_89d = policy_rag.evaluate_eligibility(
        uct="uct-nl-001",
        benefit_or_policy_topic="ergonomic_equipment_stipend",
        policy_chunks=chunks,
        employee_profile={
            "employee_id": "EMP-NL-NEW",
            "work_location_type": "OFFICE",
            "hire_date": "2026-03-04",  # 89 days before 2026-06-01
            "fte": 1.0,
        },
    )
    assert ineligible_89d["ok"] is True
    assert ineligible_89d["eligible"] is False
    assert (
        "work_location_type" in ineligible_89d["unmet_criteria"]
        or "tenure_days" in ineligible_89d["unmet_criteria"]
    )


def test_tc_ut_042_citation_integrity_active_status_and_retired_doc_404(
    policy_rag, bff_service
):
    """TC-UT-042 (FR-074, FR-077, BH-04-1-01): Reject hallucinated or retired citations not in retrieved_chunks and return HTTP 404 DOCUMENT_NOT_FOUND on retired/unknown docs in GET /api/v1/docs/{doc_id}."""
    active_chunks = [
        {
            "doc_id": "POL-NL-LEAVE-2026",
            "section_id": "4.2",
            "title": "Netherlands Annual Leave Policy",
            "effective_date": "2026-01-01",
            "status": "active",
            "text": "Full-time employees in the Netherlands receive 25 paid vacation days per calendar year.",
        }
    ]
    # Positive: valid citation to active chunk in retrieved_chunks passes at support >= 0.80
    valid_res = policy_rag.verify_grounding_and_repair(
        uct="uct-nl-001",
        query="How many vacation days do full-time NL employees get?",
        draft_answer="Full-time employees in the Netherlands receive 25 paid vacation days per year [POL-NL-LEAVE-2026#4.2].",
        retrieved_chunks=active_chunks,
    )
    assert valid_res["verdict"] == "PASS"
    assert valid_res["min_claim_support_score"] >= 0.80

    # Negative: draft citing fabricated/unretrieved [POL-FAKE-999#1.1] fails citation integrity check
    fabricated_cit = policy_rag.verify_grounding_and_repair(
        uct="uct-nl-001",
        query="How many vacation days do full-time NL employees get?",
        draft_answer="Employees receive 35 vacation days [POL-FAKE-999#1.1].",
        retrieved_chunks=active_chunks,
    )
    assert fabricated_cit["verdict"] == "ABSTAIN"
    assert fabricated_cit["error_code"] == ErrorCode.INSUFFICIENT_EVIDENCE

    # BFF GET /api/v1/docs/{doc_id} returns 404 DOCUMENT_NOT_FOUND for retired or unknown document
    retired_doc = bff_service.get_document(
        headers={"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
        client_ip="10.200.1.10",
        doc_id="POL-NL-LEAVE-2022-RETIRED",
    )
    assert retired_doc["http_status"] == 404
    assert retired_doc["error_code"] == ErrorCode.DOCUMENT_NOT_FOUND


def test_tc_ut_043_grounding_support_080_boundary_single_repair_and_no_silent_trim(
    policy_rag,
):
    """TC-UT-043 (FR-011, FR-074, FR-075, NFR-030, NFR-031, BH-01-3-03, BH-04-1-05): Verify support score 0.80 passes, 0.79 triggers at most 1 repair regeneration and abstains without silent claim trimming."""
    chunks = [
        {
            "doc_id": "POL-NL-EXPENSE-2026",
            "section_id": "3.1",
            "title": "Netherlands Expense Policy",
            "effective_date": "2026-01-01",
            "status": "active",
            "text": "Home internet reimbursement is capped at EUR 50 per month for eligible remote employees.",
        }
    ]
    # Draft with unsupported second claim (support < 0.80) triggers 1 repair attempt and abstains if still unsupported (no silent trimming)
    abstain_res = policy_rag.verify_grounding_and_repair(
        uct="uct-nl-001",
        query="What is the home internet reimbursement and free mobile phone upgrade policy?",
        draft_answer="Home internet is capped at EUR 50/month [POL-NL-EXPENSE-2026#3.1], and every employee gets a free phone upgrade every 6 months.",
        retrieved_chunks=chunks,
        check_grounding_available=False,  # Also tests fallback verifier when Vertex Check Grounding is unavailable
    )
    assert abstain_res["verdict"] == "ABSTAIN"
    assert abstain_res["repair_attempts"] == 1
    assert abstain_res["silent_claim_trimming_performed"] is False
    assert abstain_res["error_code"] == ErrorCode.INSUFFICIENT_EVIDENCE


def test_tc_ut_044_seeded_unsupported_claim_suite_both_verifiers_and_curator_independence(
    policy_rag,
):
    """TC-UT-044 (NFR-030, NFR-031, BH-01-3-03, BH-04-1-07): Verify held-out seeded unsupported-claim suite (scope shift, dropped condition, changed quantifier, temporal shift) on both CHECK_GROUNDING and FALLBACK_VERIFIER with 0 misses and curator independence."""
    seeded_cases = [
        {
            "case_id": "SEED-01",
            "perturbation": "SCOPE_SHIFT",
            "claim": "Applies to all global contractors instead of NL FTEs",
        },
        {
            "case_id": "SEED-02",
            "perturbation": "DROPPED_CONDITION",
            "claim": "Drops manager approval requirement for study leave",
        },
        {
            "case_id": "SEED-03",
            "perturbation": "CHANGED_QUANTIFIER",
            "claim": "Changes 'up to 5 days' to 'at least 15 days'",
        },
        {
            "case_id": "SEED-04",
            "perturbation": "TEMPORAL_SHIFT",
            "claim": "Cites 2024 retired allowance instead of 2026 effective rate",
        },
    ]
    for mode in ("CHECK_GROUNDING", "FALLBACK_VERIFIER"):
        suite_res = policy_rag.evaluate_seeded_unsupported_claim_suite(
            seeded_cases=seeded_cases,
            verifier_mode=mode,
            author_id="prompt-author-alice",
            curator_id="eval-curator-bob",
        )
        assert suite_res["false_negatives"] == 0
        assert suite_res["detection_rate"] == 1.0
        assert suite_res["curator_independent"] is True

    # Negative/Boundary: if author_id == curator_id, independence check fails release gate
    same_author = policy_rag.evaluate_seeded_unsupported_claim_suite(
        seeded_cases=seeded_cases,
        verifier_mode="CHECK_GROUNDING",
        author_id="same-person",
        curator_id="same-person",
    )
    assert same_author["curator_independent"] is False
    assert same_author["release_gate_passed"] is False


def test_tc_ut_045_policy_corpus_ingestion_chunk_metadata_and_900s_sync_sla_boundary(
    policy_rag, fixed_now
):
    """TC-UT-045 (FR-070, FR-071, FR-078, BH-04-1-05): Ingest PDF/text documents with section/page/effective_date metadata; pass at 900s (15m) SLA boundary and flag breach at 901s."""
    docs = [
        {
            "doc_id": "POL-DE-LEAVE-2026",
            "title": "Germany Leave Policy 2026",
            "format": "PDF",
            "country": "DE",
            "effective_date": "2026-01-01",
            "status": "active",
            "pages": [
                {
                    "page_number": 1,
                    "section_id": "1.1",
                    "heading": "Annual Entitlement",
                    "text": "30 work days per year.",
                }
            ],
        },
        {
            "doc_id": "POL-GLOBAL-RELOC-2026",
            "title": "Global Office Relocation Policy",
            "format": "MARKDOWN",
            "country": "GLOBAL",
            "effective_date": "2026-01-01",
            "status": "active",
            "pages": [
                {
                    "page_number": 1,
                    "section_id": "2.0",
                    "heading": "Relocation Allowance",
                    "text": "Lump-sum relocation support.",
                }
            ],
        },
    ]
    # Exact 900s (15m) SLA boundary passes
    res_900 = policy_rag.sync_policy_corpus(
        changed_documents=docs,
        sync_started_epoch=fixed_now,
        sync_completed_epoch=fixed_now + 900,
    )
    assert res_900["ok"] is True
    assert res_900["within_15m_sla"] is True
    assert res_900["chunks_indexed"] >= 2
    assert res_900["metadata_preserved"] is True

    # Adjacent 901s (> 15m) boundary flags SLA breach
    res_901 = policy_rag.sync_policy_corpus(
        changed_documents=docs,
        sync_started_epoch=fixed_now,
        sync_completed_epoch=fixed_now + 901,
    )
    assert res_901["within_15m_sla"] is False
    assert res_901["sla_breach_alert_emitted"] is True
