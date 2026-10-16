"""Unit tests for WorkWeek HRIS MCP Server (SDD §5.4.2 & §5.4.4, FR-040..FR-054, FR-022, NFR-032)."""

from __future__ import annotations

import pytest

from hr_agent.models import ALLOWED_PROFILE_COUNTRIES, ErrorCode

pytestmark = pytest.mark.unit


def test_tc_ut_024_get_my_profile_data_minimization_and_remote_status(workweek_mcp):
    """TC-UT-024 (FR-040, FR-041, FR-050, FR-052, FR-016): Retrieve profile scoped to UCT.sub; exclude address/phone from for_llm by default and never return salary/IBAN/SSN."""
    # Default call (include_contact_details=False) excludes home_address/personal_phone from for_llm
    res_min = workweek_mcp.get_my_profile(
        uct="uct-nl-001", include_contact_details=False
    )
    assert res_min["ok"] is True
    llm_view = res_min["for_llm"]
    assert llm_view["employee_id"] == "EMP-NL-001"
    assert llm_view["work_location_type"] in ("REMOTE", "HYBRID", "OFFICE")
    assert "home_address" not in llm_view
    assert "personal_phone" not in llm_view
    assert "salary" not in llm_view
    assert "iban" not in llm_view
    assert "national_id" not in llm_view

    # Explicit contact request (include_contact_details=True) returns address & phone but still strips salary/IBAN/SSN
    res_full = workweek_mcp.get_my_profile(
        uct="uct-nl-001", include_contact_details=True
    )
    assert res_full["ok"] is True
    assert "home_address" in res_full["for_llm"]
    assert "personal_phone" in res_full["for_llm"]
    assert "salary" not in res_full["for_llm"]


def test_tc_ut_025_get_my_leave_balances_live_fetch_no_cross_turn_cache(workweek_mcp):
    """TC-UT-025 (FR-044, FR-049, T-047): Fetch live Vacation and Sick balances and verify zero caching across turns."""
    turn1 = workweek_mcp.get_my_leave_balances(uct="uct-nl-001", force_live_fetch=True)
    assert turn1["ok"] is True
    assert turn1["cache_used"] is False
    assert "VACATION" in turn1["for_llm"]["balances"]
    assert "SICK" in turn1["for_llm"]["balances"]
    assert turn1["for_llm"]["balances"]["VACATION"]["remaining_days"] == 18.0

    # Negative/Boundary check: attempting to read from a cross-turn cache (force_live_fetch=False) is rejected or forced live
    turn2_after_submit = workweek_mcp.get_my_leave_balances(
        uct="uct-nl-001-after-3d-leave", force_live_fetch=True
    )
    assert turn2_after_submit["cache_used"] is False
    assert (
        turn2_after_submit["for_llm"]["balances"]["VACATION"]["remaining_days"] == 15.0
    )


def test_tc_ut_026_resolve_relative_dates_in_user_timezone_and_midnight_boundary(
    workweek_mcp,
):
    """TC-UT-026 (FR-051): Resolve 'this coming Thursday and Friday' in Europe/Amsterdam vs Asia/Shanghai across UTC midnight boundary."""
    resolved_nl = workweek_mcp.resolve_relative_dates(
        expression="this coming Thursday and Friday",
        user_timezone="Europe/Amsterdam",
        now_utc_iso="2026-06-01T22:30:00Z",  # 2026-06-02 00:30 CEST in Amsterdam
    )
    assert resolved_nl["ok"] is True
    assert resolved_nl["start_date"] == "2026-06-04"
    assert resolved_nl["end_date"] == "2026-06-05"

    resolved_cn = workweek_mcp.resolve_relative_dates(
        expression="tomorrow",
        user_timezone="Asia/Shanghai",
        now_utc_iso="2026-06-01T16:30:00Z",  # Already 2026-06-02 00:30 CST in Shanghai!
    )
    assert resolved_cn["ok"] is True
    assert resolved_cn["start_date"] == "2026-06-03"


def test_tc_ut_027_validate_leave_request_weekday_calculation_and_exact_balance_boundary(
    workweek_mcp,
):
    """TC-UT-027 (FR-045, FR-046, FR-101, BH-04-1-05): Validate weekday-only work_days, pass at exact 18.0d remaining balance, and fail at 18.5d/19.0d."""
    # 5 calendar days Fri..Tue (2 weekend days excluded) = 3.0 work_days
    res_3d = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2026-06-05",
        end_date="2026-06-09",
        today_local="2026-06-01",
    )
    assert res_3d["ok"] is True
    assert res_3d["for_llm"]["work_days"] == 3.0
    assert res_3d["for_llm"]["remaining_after"] == 15.0
    assert res_3d["for_llm"]["preview_digest"].startswith("sha256:")

    # Exact passing boundary: 18.0 work_days requested when remaining_days == 18.0 -> remaining_after == 0.0
    res_exact_18 = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2026-06-01",
        end_date="2026-06-24",  # 18 weekdays
        today_local="2026-06-01",
    )
    assert res_exact_18["ok"] is True
    assert res_exact_18["for_llm"]["work_days"] == 18.0
    assert res_exact_18["for_llm"]["remaining_after"] == 0.0

    # Adjacent failing boundary: 19.0 work_days (> 18.0 remaining) -> INSUFFICIENT_BALANCE
    res_over_19 = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2026-06-01",
        end_date="2026-06-25",  # 19 weekdays
        today_local="2026-06-01",
    )
    assert res_over_19["ok"] is False
    assert res_over_19["error_code"] == ErrorCode.INSUFFICIENT_BALANCE

    # Weekend-only request (0 work_days) -> ZERO_WORK_DAYS
    res_weekend = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2026-06-06",  # Saturday
        end_date="2026-06-07",  # Sunday
        today_local="2026-06-01",
    )
    assert res_weekend["ok"] is False
    assert res_weekend["error_code"] == ErrorCode.ZERO_WORK_DAYS


def test_tc_ut_028_leave_temporal_validity_no_past_start_and_365d_horizon_boundaries(
    workweek_mcp,
):
    """TC-UT-028 (FR-047, SDD §5.4.2/§5.4.4, BH-04-1-02, BH-04-1-05): Reject start_date in past (START_IN_PAST), end_date < start_date (END_BEFORE_START), and > +365d (BEYOND_HORIZON)."""
    # Exact boundary: start_date == today_local (2026-06-01) passes
    res_today = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="SICK",
        start_date="2026-06-01",
        end_date="2026-06-01",
        today_local="2026-06-01",
    )
    assert res_today["ok"] is True

    # Adjacent failing boundary: start_date == yesterday (2026-05-31 < 2026-06-01) -> START_IN_PAST (even for SICK leave!)
    res_past_1d = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="SICK",
        start_date="2026-05-31",
        end_date="2026-06-01",
        today_local="2026-06-01",
    )
    assert res_past_1d["ok"] is False
    assert res_past_1d["error_code"] == ErrorCode.START_IN_PAST

    # end_date < start_date -> END_BEFORE_START
    res_inverted = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2026-06-15",
        end_date="2026-06-10",
        today_local="2026-06-01",
    )
    assert res_inverted["ok"] is False
    assert res_inverted["error_code"] == ErrorCode.END_BEFORE_START

    # Exact +365d boundary (2027-06-01) passes; +366d (2027-06-02) fails with BEYOND_HORIZON
    res_365d = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2027-06-01",
        end_date="2027-06-01",
        today_local="2026-06-01",
    )
    assert res_365d["ok"] is True

    res_366d = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2027-06-02",
        end_date="2027-06-02",
        today_local="2026-06-01",
    )
    assert res_366d["ok"] is False
    assert res_366d["error_code"] == ErrorCode.BEYOND_HORIZON


def test_tc_ut_029_leave_overlap_and_in_flight_duplicate_detection(workweek_mcp):
    """TC-UT-029 (FR-054, FR-023): Reject overlapping Pending/Approved leave (OVERLAPPING_LEAVE) and in-flight IN_PROGRESS/OUTCOME_UNKNOWN leave (IN_FLIGHT_DUPLICATE), while allowing adjacent non-overlapping dates."""
    # Overlapping existing request 2026-07-08..2026-07-12
    overlap_res = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2026-07-10",
        end_date="2026-07-14",
        today_local="2026-06-01",
    )
    assert overlap_res["ok"] is False
    assert overlap_res["error_code"] == ErrorCode.OVERLAPPING_LEAVE

    # Boundary: immediately adjacent date 2026-07-13..2026-07-13 does not overlap and succeeds
    adjacent_res = workweek_mcp.validate_leave_request(
        uct="uct-nl-001",
        leave_type="VACATION",
        start_date="2026-07-13",
        end_date="2026-07-13",
        today_local="2026-06-01",
    )
    assert adjacent_res["ok"] is True

    # In-flight duplicate in hr-idem-ww (IN_PROGRESS or OUTCOME_UNKNOWN) -> IN_FLIGHT_DUPLICATE
    inflight_res = workweek_mcp.validate_leave_request(
        uct="uct-nl-001-inflight",
        leave_type="VACATION",
        start_date="2026-08-03",
        end_date="2026-08-05",
        today_local="2026-06-01",
    )
    assert inflight_res["ok"] is False
    assert inflight_res["error_code"] == ErrorCode.IN_FLIGHT_DUPLICATE


def test_tc_ut_030_validate_and_update_contact_info_address_phone_email_formats(
    workweek_mcp,
):
    """TC-UT-030 (FR-042, FR-043, FR-048, FR-027, FR-028, BH-04-1-01): Validate E.164 phone (incl. +86 and 7..15 digit boundaries), structured address in allowed countries (incl. CN, excl. TW), and RFC 5322 email."""
    assert "CN" in ALLOWED_PROFILE_COUNTRIES
    assert "TW" not in ALLOWED_PROFILE_COUNTRIES

    # Positive: valid CN address and +86 E.164 phone
    valid_cn = workweek_mcp.validate_contact_update(
        uct="uct-cn-003",
        home_address={
            "street": "88 Century Avenue",
            "city": "Shanghai",
            "postal_code": "200120",
            "country": "CN",
        },
        personal_phone="+8613800138000",
        email="wei.chen.synthetic@example.invalid",
    )
    assert valid_cn["ok"] is True
    assert valid_cn["for_llm"]["preview_digest"].startswith("sha256:")

    # Boundary: E.164 minimum 7 digits (+1234567) and maximum 15 digits (+123456789012345) pass; 6 digits or 16 digits fail
    min_e164 = workweek_mcp.validate_contact_update(
        uct="uct-nl-001", personal_phone="+3112345"
    )
    assert min_e164["ok"] is True
    max_e164 = workweek_mcp.validate_contact_update(
        uct="uct-nl-001", personal_phone="+311234567890123"
    )
    assert max_e164["ok"] is True

    too_short_phone = workweek_mcp.validate_contact_update(
        uct="uct-nl-001", personal_phone="+31123"
    )
    assert too_short_phone["ok"] is False
    assert too_short_phone["error_code"] == ErrorCode.INVALID_PHONE_FORMAT

    too_long_phone = workweek_mcp.validate_contact_update(
        uct="uct-nl-001", personal_phone="+3112345678901234"
    )
    assert too_long_phone["ok"] is False
    assert too_long_phone["error_code"] == ErrorCode.INVALID_PHONE_FORMAT

    # Negative: Taiwan ('TW') address rejected with UNSUPPORTED_COUNTRY
    tw_addr = workweek_mcp.validate_contact_update(
        uct="uct-nl-001",
        home_address={
            "street": "Section 5 Xinyi Road",
            "city": "Taipei",
            "postal_code": "110",
            "country": "TW",
        },
    )
    assert tw_addr["ok"] is False
    assert tw_addr["error_code"] == ErrorCode.UNSUPPORTED_COUNTRY

    # Negative: invalid email syntax -> INVALID_EMAIL_FORMAT; empty fields -> NO_FIELDS_PROVIDED
    bad_email = workweek_mcp.validate_contact_update(
        uct="uct-nl-001",
        personal_phone="+31612345678",
        email="not-an-email@@invalid",
    )
    assert bad_email["ok"] is False
    assert bad_email["error_code"] == ErrorCode.INVALID_EMAIL_FORMAT

    empty_update = workweek_mcp.validate_contact_update(uct="uct-nl-001")
    assert empty_update["ok"] is False
    assert empty_update["error_code"] == ErrorCode.NO_FIELDS_PROVIDED


def test_tc_ut_031_submit_leave_and_update_contact_require_valid_ct(workweek_mcp):
    """TC-UT-031 (FR-022, FR-023, FR-042, FR-043, FR-045, NFR-032): Verify submit_my_leave_request and update_my_contact_info succeed with valid CT and fail with INVALID_CONFIRMATION_TOKEN without CT."""
    leave_ok = workweek_mcp.submit_my_leave_request(
        uct="uct-nl-001",
        ct="ct-valid-single-use-001",
        idempotency_key="0195f000-0000-7000-8000-000000000001",
        step_id="step-1",
        leave_type="VACATION",
        start_date="2026-06-10",
        end_date="2026-06-12",
        expected_work_days=3.0,
        preview_digest="sha256:preview001",
    )
    assert leave_ok["ok"] is True
    assert leave_ok["for_llm"]["leave_request_id"].startswith("LR-")
    assert leave_ok["for_llm"]["status"] == "SUBMITTED"

    leave_no_ct = workweek_mcp.submit_my_leave_request(
        uct="uct-nl-001",
        ct="",
        idempotency_key="0195f000-0000-7000-8000-000000000002",
        step_id="step-1",
        leave_type="VACATION",
        start_date="2026-06-10",
        end_date="2026-06-12",
        expected_work_days=3.0,
        preview_digest="sha256:preview001",
    )
    assert leave_no_ct["ok"] is False
    assert leave_no_ct["error_code"] == ErrorCode.INVALID_CONFIRMATION_TOKEN

    contact_ok = workweek_mcp.update_my_contact_info(
        uct="uct-nl-001",
        ct="ct-valid-contact-001",
        idempotency_key="0195f000-0000-7000-8000-000000000003",
        step_id="step-1",
        home_address={
            "street": "Prinsengracht 200",
            "city": "Amsterdam",
            "postal_code": "1016 HG",
            "country": "NL",
        },
        personal_phone="+31699887766",
        preview_digest="sha256:contact001",
    )
    assert contact_ok["ok"] is True
    assert set(contact_ok["for_llm"]["updated_fields"]) == {
        "home_address",
        "personal_phone",
    }

    contact_no_ct = workweek_mcp.update_my_contact_info(
        uct="uct-nl-001",
        ct="",
        idempotency_key="0195f000-0000-7000-8000-000000000004",
        step_id="step-1",
        personal_phone="+31699887766",
        preview_digest="sha256:contact001",
    )
    assert contact_no_ct["ok"] is False
    assert contact_no_ct["error_code"] == ErrorCode.INVALID_CONFIRMATION_TOKEN
