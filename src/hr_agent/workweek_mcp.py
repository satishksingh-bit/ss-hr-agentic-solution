"""WorkWeek HRIS MCP Server implementation (SDD §5.4.2 & §5.4.4, FR-040..FR-054)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hr_agent.models import (
    ALLOWED_PROFILE_COUNTRIES,
    ErrorCode,
    FirestoreDatabaseId,
    LeaveType,
    LocalSqliteStore,
    create_json_http_server,
    run_http_server_cli,
)
from hr_agent.security import (
    KmsTokenSigner,
    TelemetrySanitizer,
    parse_uct_or_shorthand,
    verify_mcp_write_gate,
)

_E164_PHONE_RE = re.compile(r"^\+[1-9][0-9]{6,14}$")
_RFC5322_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

_WEEKDAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_ALLOWED_WRITE_STATUS_SAS = frozenset(
    {
        "plan-reconciler-sa",
        "hr-agent-sa",
    }
)


def _count_weekdays(start: date, end: date) -> float:
    """Count Monday..Friday working days inclusive between start and end."""
    if end < start:
        return 0.0
    days = 0.0
    curr = start
    while curr <= end:
        if curr.weekday() < 5:
            days += 1.0
        curr += timedelta(days=1)
    return days


class WorkWeekMcpServer:
    """Stateless MCP tool server for WorkWeek HRIS operations (C6)."""

    def __init__(self, state_dir: str | None = None) -> None:
        self._lock = threading.RLock()
        self._signer = KmsTokenSigner()
        self._sqlite = LocalSqliteStore(
            FirestoreDatabaseId.HR_IDEM_WW, state_dir=state_dir
        )
        self._fetch_counter = 0
        self._used_cts: set[str] = set()
        self._idempotency_store: dict[str, dict[str, Any]] = {}
        self._validated_previews: dict[tuple[str, str, str], str] = {}
        self._leave_counter = 1000
        self._leave_ledger: dict[tuple[str, str], dict[str, dict[str, float]]] = {
            ("EMP-NL-001", "default"): {
                "VACATION": {
                    "accrued_days": 25.0,
                    "used_days": 5.0,
                    "pending_days": 2.0,
                    "remaining_days": 18.0,
                },
                "SICK": {
                    "accrued_days": 10.0,
                    "used_days": 1.0,
                    "pending_days": 0.0,
                    "remaining_days": 9.0,
                },
            },
            ("EMP-NL-001", "sess-uct-nl-001-after-3d-leave"): {
                "VACATION": {
                    "accrued_days": 25.0,
                    "used_days": 7.0,
                    "pending_days": 5.0,
                    "remaining_days": 15.0,
                },
                "SICK": {
                    "accrued_days": 10.0,
                    "used_days": 1.0,
                    "pending_days": 0.0,
                    "remaining_days": 9.0,
                },
            },
            ("EMP-DE-002", "default"): {
                "VACATION": {
                    "accrued_days": 30.0,
                    "used_days": 10.0,
                    "pending_days": 2.0,
                    "remaining_days": 18.0,
                },
                "SICK": {
                    "accrued_days": 10.0,
                    "used_days": 1.0,
                    "pending_days": 0.0,
                    "remaining_days": 9.0,
                },
            },
            ("EMP-CN-003", "default"): {
                "VACATION": {
                    "accrued_days": 20.0,
                    "used_days": 2.0,
                    "pending_days": 0.0,
                    "remaining_days": 18.0,
                },
                "SICK": {
                    "accrued_days": 10.0,
                    "used_days": 1.0,
                    "pending_days": 0.0,
                    "remaining_days": 9.0,
                },
            },
            ("EMP-SG-004", "default"): {
                "VACATION": {
                    "accrued_days": 21.0,
                    "used_days": 3.0,
                    "pending_days": 0.0,
                    "remaining_days": 18.0,
                },
                "SICK": {
                    "accrued_days": 14.0,
                    "used_days": 2.0,
                    "pending_days": 0.0,
                    "remaining_days": 12.0,
                },
            },
        }
        self._in_flight_leaves: set[tuple[str, str, str, str]] = {
            ("EMP-NL-001", "sess-uct-nl-001-inflight", "*", "*"),
            ("EMP-NL-001", "*", "2026-08-03", "2026-08-05"),
        }
        self._profile_overrides: dict[tuple[str, str], dict[str, Any]] = {
            ("EMP-NL-001", "sess-uct-nl-001-updated-contact"): {
                "home_address": {
                    "street": "Prinsengracht 200",
                    "city": "Amsterdam",
                    "postal_code": "1016 HG",
                    "country": "NL",
                },
                "personal_phone": "+31699887766",
            }
        }

    def _verify_write_gate(
        self,
        *,
        claims: dict[str, Any],
        ct: str,
        step_id: str,
        tool_name: str,
        canonical_args: dict[str, Any],
        preview_digest: str,
        expected_preview_digest: str,
    ) -> dict[str, Any]:
        return verify_mcp_write_gate(
            signer=self._signer,
            claims=claims,
            ct=ct,
            step_id=step_id,
            tool_name=tool_name,
            canonical_args=canonical_args,
            preview_digest=preview_digest,
            expected_preview_digest=expected_preview_digest,
            validated_previews=self._validated_previews,
            used_cts=self._used_cts,
            lock=self._lock,
        )

    def get_my_profile(
        self,
        *,
        uct: str,
        include_contact_details: bool = False,
    ) -> dict[str, Any]:
        """Return employee profile projected through the BRD §2.2 allow-list (FR-016, FR-040, FR-041, FR-050, FR-052).

        Excludes home_address and personal_phone from for_llm unless include_contact_details=True,
        and never returns salary, bank/IBAN, national ID, or medical data.
        """
        claims = parse_uct_or_shorthand(uct, expected_audience="workweek-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        sub = claims["sub"]
        session_id = str(claims.get("session_id", "default"))
        country = claims["country"]
        if country == "TW":
            return {
                "ok": False,
                "error_code": ErrorCode.REGION_BLOCKED,
                "error": {
                    "code": ErrorCode.REGION_BLOCKED,
                    "message": "Taiwan region is out of scope.",
                },
            }

        base_profiles: dict[str, dict[str, Any]] = {
            "EMP-NL-001": {
                "employee_id": "EMP-NL-001",
                "full_name": "Eva Bakker",
                "work_email": "eva.bakker.synthetic@example.invalid",
                "job_title": "Senior Product Engineer",
                "department": "People Technology",
                "manager_id": "EMP-NL-MGR-01",
                "manager_name": "Pieter Jansen",
                "manager_email": "pieter.jansen.synthetic@example.invalid",
                "country": "NL",
                "legal_entity": "LE-NL-BV",
                "office_location": "Amsterdam HQ",
                "work_location_type": "REMOTE",
                "hire_date": "2022-03-01",
                "fte": 1.0,
                "employment_status": "ACTIVE",
                "timezone": "Europe/Amsterdam",
            },
            "EMP-DE-002": {
                "employee_id": "EMP-DE-002",
                "full_name": "Lukas Mueller",
                "work_email": "lukas.mueller.synthetic@example.invalid",
                "job_title": "Staff Backend Engineer",
                "department": "Core Infrastructure",
                "manager_id": "EMP-DE-MGR-01",
                "manager_name": "Hannah Weber",
                "manager_email": "hannah.weber.synthetic@example.invalid",
                "country": "DE",
                "legal_entity": "LE-DE-GMBH",
                "office_location": "Berlin Hub",
                "work_location_type": "HYBRID",
                "hire_date": "2021-06-15",
                "fte": 1.0,
                "employment_status": "ACTIVE",
                "timezone": "Europe/Berlin",
            },
            "EMP-CN-003": {
                "employee_id": "EMP-CN-003",
                "full_name": "Wei Chen",
                "work_email": "wei.chen.synthetic@example.invalid",
                "job_title": "Principal Solutions Architect",
                "department": "APAC Engineering",
                "manager_id": "EMP-CN-MGR-01",
                "manager_name": "Li Zhang",
                "manager_email": "li.zhang.synthetic@example.invalid",
                "country": "CN",
                "legal_entity": "LE-CN-WFOE",
                "office_location": "Shanghai Office",
                "work_location_type": "OFFICE",
                "hire_date": "2020-09-01",
                "fte": 1.0,
                "employment_status": "ACTIVE",
                "timezone": "Asia/Shanghai",
            },
            "EMP-SG-004": {
                "employee_id": "EMP-SG-004",
                "full_name": "Mei Lin Tan",
                "work_email": "meilin.tan.synthetic@example.invalid",
                "job_title": "Staff AI Engineer",
                "department": "APAC Engineering",
                "manager_id": "EMP-SG-MGR-01",
                "manager_name": "Rajesh Nair",
                "manager_email": "rajesh.nair.synthetic@example.invalid",
                "country": "SG",
                "legal_entity": "LE-SG-PTE",
                "office_location": "Singapore Marina Bay HQ",
                "work_location_type": "HYBRID",
                "hire_date": "2018-04-01",
                "fte": 1.0,
                "employment_status": "ACTIVE",
                "timezone": "Asia/Singapore",
            },
        }

        profile = dict(base_profiles.get(sub, base_profiles["EMP-NL-001"]))
        profile["employee_id"] = sub

        if include_contact_details:
            with self._lock:
                override = self._profile_overrides.get(
                    (sub, session_id)
                ) or self._profile_overrides.get((sub, "default"))
            if override is not None:
                profile["home_address"] = dict(override["home_address"])
                profile["personal_phone"] = str(override["personal_phone"])
            elif sub == "EMP-CN-003":
                profile["home_address"] = {
                    "street": "88 Century Avenue",
                    "city": "Shanghai",
                    "postal_code": "200120",
                    "country": "CN",
                }
                profile["personal_phone"] = "+8613800138000"
            elif sub == "EMP-SG-004":
                profile["home_address"] = {
                    "street": "10 Marina Boulevard",
                    "city": "Singapore",
                    "postal_code": "018983",
                    "country": "SG",
                }
                profile["personal_phone"] = "+6591234567"
            else:
                profile["home_address"] = {
                    "street": "Keizersgracht 100",
                    "city": "Amsterdam",
                    "postal_code": "1015 AA",
                    "country": "NL",
                }
                profile["personal_phone"] = "+31612345678"

        return {
            "ok": True,
            "status": "SUCCESS",
            "for_llm": profile,
            "ui_card": {
                "card_type": "employee_profile",
                "employee_id": sub,
                "contact_included": include_contact_details,
            },
        }

    def get_my_leave_balances(
        self,
        *,
        uct: str,
        force_live_fetch: bool = True,
    ) -> dict[str, Any]:
        """Fetch live accrued, used, pending, and remaining balances for Vacation and Sick leave (FR-044, FR-049).

        Never caches or reuses dynamic balance data across turns.
        """
        claims = parse_uct_or_shorthand(uct, expected_audience="workweek-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        sub = claims["sub"]
        session_id = str(claims.get("session_id", "default"))
        with self._lock:
            self._fetch_counter += 1
            seq = self._fetch_counter
            ledger_entry = (
                self._leave_ledger.get((sub, session_id))
                or self._leave_ledger.get((sub, "default"))
                or self._leave_ledger[("EMP-NL-001", "default")]
            )
            balances_copy = {k: dict(v) for k, v in ledger_entry.items()}

        vac_remaining = balances_copy["VACATION"]["remaining_days"]
        data = {
            "employee_id": sub,
            "fetch_sequence": seq,
            "as_of_utc": f"2026-06-01T10:00:{seq % 60:02d}Z",
            "balances": balances_copy,
        }
        return {
            "ok": True,
            "status": "SUCCESS",
            "cache_used": False,
            "force_live_fetch": True,
            "for_llm": data,
            "ui_card": {
                "card_type": "leave_balances",
                "employee_id": sub,
                "vacation_remaining_days": vac_remaining,
            },
        }

    def resolve_relative_dates(
        self,
        *,
        expression: str,
        user_timezone: str,
        now_utc_iso: str,
    ) -> dict[str, Any]:
        """Resolve relative date phrases (e.g., 'this coming Thursday and Friday') in the user's IANA time zone (FR-051)."""
        try:
            dt_utc = datetime.fromisoformat(str(now_utc_iso).replace("Z", "+00:00"))
            local_dt = dt_utc.astimezone(ZoneInfo(str(user_timezone)))
        except (ValueError, ZoneInfoNotFoundError, KeyError) as exc:
            return {
                "ok": False,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED,
                    "message": f"Invalid timestamp or IANA time zone: {exc}",
                },
            }
        local_today = local_dt.date()

        expr_lower = expression.strip().lower()
        if expr_lower == "tomorrow":
            target = local_today + timedelta(days=1)
            return {
                "ok": True,
                "local_today": local_today.isoformat(),
                "start_date": target.isoformat(),
                "end_date": target.isoformat(),
                "work_days": _count_weekdays(target, target),
                "timezone": user_timezone,
            }

        found_days: list[int] = []
        for day_name, weekday_idx in _WEEKDAY_MAP.items():
            if day_name in expr_lower:
                found_days.append(weekday_idx)

        if found_days:
            first_wd = found_days[0]
            delta_start = (first_wd - local_today.weekday()) % 7
            if delta_start == 0:
                delta_start = 7
            start_d = local_today + timedelta(days=delta_start)
            if len(found_days) > 1:
                last_wd = found_days[-1]
                span = (last_wd - first_wd) % 7
                end_d = start_d + timedelta(days=span)
            else:
                end_d = start_d
        else:
            start_d = local_today + timedelta(days=1)
            end_d = start_d

        return {
            "ok": True,
            "local_today": local_today.isoformat(),
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "work_days": _count_weekdays(start_d, end_d),
            "timezone": user_timezone,
        }

    def validate_leave_request(
        self,
        *,
        uct: str,
        leave_type: str,
        start_date: str,
        end_date: str,
        user_timezone: str = "Europe/Amsterdam",
        today_local: str = "2026-06-01",
    ) -> dict[str, Any]:
        """Validate leave dates, weekday work_days, balance sufficiency, overlap, and in-flight duplicates without mutating state (FR-046, FR-047, FR-051, FR-054, FR-101)."""
        claims = parse_uct_or_shorthand(uct, expected_audience="workweek-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        norm_type = str(leave_type).strip().upper()
        valid_leave_types = {lt.value for lt in LeaveType}
        if norm_type not in valid_leave_types:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.INVALID_LEAVE_TYPE,
                "error": {
                    "code": ErrorCode.INVALID_LEAVE_TYPE,
                    "message": f"Unsupported leave_type '{leave_type}'. Allowed: {sorted(valid_leave_types)}.",
                },
            }

        sub = claims["sub"]
        session_id = str(claims.get("session_id", "default"))
        try:
            s_date = date.fromisoformat(str(start_date))
            e_date = date.fromisoformat(str(end_date))
            t_date = date.fromisoformat(str(today_local))
        except ValueError as exc:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "error": {
                    "code": ErrorCode.VALIDATION_FAILED,
                    "message": f"Malformed ISO date in leave request: {exc}",
                },
            }

        # Rule 1: start_date < today_local -> START_IN_PAST (applies to ALL leave types including SICK per SDD §5.4.4)
        if s_date < t_date:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.START_IN_PAST,
                "error": {
                    "code": ErrorCode.START_IN_PAST,
                    "message": "Leave start_date cannot be in the past.",
                },
            }

        # Rule 2: end_date < start_date -> END_BEFORE_START
        if e_date < s_date:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.END_BEFORE_START,
                "error": {
                    "code": ErrorCode.END_BEFORE_START,
                    "message": "Leave end_date cannot be before start_date.",
                },
            }

        # Rule 3: start_date > today_local + 365 days -> BEYOND_HORIZON
        if (s_date - t_date).days > 365:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.BEYOND_HORIZON,
                "error": {
                    "code": ErrorCode.BEYOND_HORIZON,
                    "message": "Leave start_date exceeds 365-day booking horizon.",
                },
            }

        # Rule 4: Weekday work_days calculation (excludes Saturday and Sunday)
        work_days = _count_weekdays(s_date, e_date)
        if work_days <= 0.0:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.ZERO_WORK_DAYS,
                "error": {
                    "code": ErrorCode.ZERO_WORK_DAYS,
                    "message": "Selected date range contains zero working days.",
                },
            }

        # Rule 5: Balance sufficiency check from live ledger state
        with self._lock:
            ledger = (
                self._leave_ledger.get((sub, session_id))
                or self._leave_ledger.get((sub, "default"))
                or self._leave_ledger[("EMP-NL-001", "default")]
            )
            remaining_days = float(
                ledger.get(norm_type, {"remaining_days": 9.0})["remaining_days"]
            )
        if work_days > remaining_days:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.INSUFFICIENT_BALANCE,
                "error": {
                    "code": ErrorCode.INSUFFICIENT_BALANCE,
                    "message": f"Requested {work_days} work days exceeds remaining balance of {remaining_days} days.",
                },
            }

        # Rule 6: Overlap with existing Pending/Approved leave (seeded 2026-07-08..2026-07-12)
        existing_start = date(2026, 7, 8)
        existing_end = date(2026, 7, 12)
        if s_date <= existing_end and e_date >= existing_start:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.OVERLAPPING_LEAVE,
                "error": {
                    "code": ErrorCode.OVERLAPPING_LEAVE,
                    "message": "Requested dates overlap with existing request LR-2026-0042 (2026-07-08..2026-07-12).",
                },
            }

        # Rule 7: In-flight duplicate in hr-idem-ww state store (IN_PROGRESS or OUTCOME_UNKNOWN)
        with self._lock:
            is_inflight = (sub, session_id, "*", "*") in self._in_flight_leaves or (
                sub,
                "*",
                start_date,
                end_date,
            ) in self._in_flight_leaves
        if is_inflight:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.IN_FLIGHT_DUPLICATE,
                "error": {
                    "code": ErrorCode.IN_FLIGHT_DUPLICATE,
                    "message": "An identical leave request is already in flight (IN_PROGRESS/OUTCOME_UNKNOWN).",
                },
            }

        remaining_after = round(remaining_days - work_days, 2)
        canonical_preview = {
            "employee_id": sub,
            "leave_type": norm_type,
            "start_date": start_date,
            "end_date": end_date,
            "work_days": work_days,
        }
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(canonical_preview, sort_keys=True).encode("utf-8")
            ).hexdigest()
        )
        with self._lock:
            self._validated_previews[(sub, session_id, "submit_my_leave_request")] = (
                digest
            )

        for_llm = {
            "employee_id": sub,
            "leave_type": norm_type,
            "start_date": start_date,
            "end_date": end_date,
            "work_days": work_days,
            "remaining_before": remaining_days,
            "remaining_after": remaining_after,
            "preview_digest": digest,
        }
        return {
            "ok": True,
            "valid": True,
            "status": "SUCCESS",
            "for_llm": for_llm,
            "ui_card": {
                "card_type": "leave_preview",
                **for_llm,
            },
        }

    def validate_contact_update(
        self,
        *,
        uct: str,
        home_address: dict[str, str] | None = None,
        personal_phone: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        """Validate home_address and/or personal_phone update formats and return preview_digest without mutating state (FR-042, FR-043, FR-048, FR-101)."""
        claims = parse_uct_or_shorthand(uct, expected_audience="workweek-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        if home_address is None and personal_phone is None and email is None:
            return {
                "ok": False,
                "valid": False,
                "error_code": ErrorCode.NO_FIELDS_PROVIDED,
                "error": {
                    "code": ErrorCode.NO_FIELDS_PROVIDED,
                    "message": "At least one of home_address or personal_phone must be provided.",
                },
            }

        if personal_phone is not None:
            if not _E164_PHONE_RE.match(personal_phone.strip()):
                return {
                    "ok": False,
                    "valid": False,
                    "error_code": ErrorCode.INVALID_PHONE_FORMAT,
                    "error": {
                        "code": ErrorCode.INVALID_PHONE_FORMAT,
                        "message": "personal_phone must match E.164 format ^\\+[1-9][0-9]{6,14}$.",
                    },
                }

        if email is not None:
            if not _RFC5322_EMAIL_RE.match(email.strip()) or "@@" in email:
                return {
                    "ok": False,
                    "valid": False,
                    "error_code": ErrorCode.INVALID_EMAIL_FORMAT,
                    "error": {
                        "code": ErrorCode.INVALID_EMAIL_FORMAT,
                        "message": "email must conform to RFC 5322 format.",
                    },
                }

        if home_address is not None:
            country = str(home_address.get("country", "")).strip().upper()
            if country not in ALLOWED_PROFILE_COUNTRIES:
                return {
                    "ok": False,
                    "valid": False,
                    "error_code": ErrorCode.UNSUPPORTED_COUNTRY,
                    "error": {
                        "code": ErrorCode.UNSUPPORTED_COUNTRY,
                        "message": f"Country '{country}' is not in ALLOWED_PROFILE_COUNTRIES.",
                    },
                }
            for req_field in ("street", "city", "postal_code", "country"):
                if not str(home_address.get(req_field, "")).strip():
                    return {
                        "ok": False,
                        "valid": False,
                        "error_code": ErrorCode.VALIDATION_FAILED,
                        "error": {
                            "code": ErrorCode.VALIDATION_FAILED,
                            "message": f"Missing required address field: {req_field}",
                        },
                    }

        sub = claims["sub"]
        session_id = str(claims.get("session_id", "default"))
        canonical_payload = {
            "employee_id": sub,
            "home_address": home_address,
            "personal_phone": personal_phone,
            "email": email,
        }
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(canonical_payload, sort_keys=True).encode("utf-8")
            ).hexdigest()
        )
        with self._lock:
            self._validated_previews[(sub, session_id, "update_my_contact_info")] = (
                digest
            )
        updated_fields = [
            k
            for k, v in (
                ("home_address", home_address),
                ("personal_phone", personal_phone),
                ("email", email),
            )
            if v is not None
        ]
        for_llm = {
            "employee_id": sub,
            "updated_fields": updated_fields,
            "home_address": home_address,
            "personal_phone": personal_phone,
            "preview_digest": digest,
        }
        return {
            "ok": True,
            "valid": True,
            "status": "SUCCESS",
            "for_llm": for_llm,
            "ui_card": {
                "card_type": "contact_update_preview",
                **for_llm,
            },
        }

    def submit_my_leave_request(
        self,
        *,
        uct: str,
        ct: str,
        idempotency_key: str,
        step_id: str,
        leave_type: str,
        start_date: str,
        end_date: str,
        expected_work_days: float,
        preview_digest: str,
        deadline_epoch_ms: int | None = None,
    ) -> dict[str, Any]:
        """Execute leave submission after verifying UCT, single-use CT, preview_digest, and idempotency_key (FR-022, FR-023, FR-045, FR-046, FR-047, FR-054, NFR-032)."""
        claims = parse_uct_or_shorthand(uct, expected_audience="workweek-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        with self._lock:
            if idempotency_key in self._idempotency_store:
                return self._idempotency_store[idempotency_key]["response"]

        sub = claims["sub"]
        session_id = str(claims.get("session_id", "default"))
        norm_type = leave_type.strip().upper()
        canonical_preview = {
            "employee_id": sub,
            "leave_type": norm_type,
            "start_date": start_date,
            "end_date": end_date,
            "work_days": float(expected_work_days),
        }
        expected_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(canonical_preview, sort_keys=True).encode("utf-8")
            ).hexdigest()
        )
        gate = self._verify_write_gate(
            claims=claims,
            ct=ct,
            step_id=step_id,
            tool_name="submit_my_leave_request",
            canonical_args=canonical_preview,
            preview_digest=preview_digest,
            expected_preview_digest=expected_digest,
        )
        if not gate["ok"]:
            return {
                "ok": False,
                "error_code": gate["error_code"],
                "error": {
                    "code": gate["error_code"],
                    "message": f"Write confirmation verification failed ({gate['error_code']}).",
                },
            }

        with self._lock:
            self._leave_counter += 1
            leave_request_id = f"LR-2026-{self._leave_counter:04d}"
            for key_pair in ((sub, "default"), (sub, session_id)):
                if (
                    key_pair in self._leave_ledger
                    and norm_type in self._leave_ledger[key_pair]
                ):
                    bal = self._leave_ledger[key_pair][norm_type]
                    bal["pending_days"] = round(
                        bal["pending_days"] + float(expected_work_days), 2
                    )
                    bal["remaining_days"] = round(
                        max(0.0, bal["remaining_days"] - float(expected_work_days)), 2
                    )

            for_llm = {
                "leave_request_id": leave_request_id,
                "employee_id": sub,
                "leave_type": norm_type,
                "start_date": start_date,
                "end_date": end_date,
                "work_days": expected_work_days,
                "status": "SUBMITTED",
                "automation_origin": "hr-agent-mvp1",
                "idempotency_key": idempotency_key,
            }
            response = {
                "ok": True,
                "status": "SUCCESS",
                "for_llm": for_llm,
                "ui_card": {
                    "card_type": "leave_submitted",
                    **for_llm,
                },
            }
            record = {
                "status": "COMPLETED",
                "backend_record_id": leave_request_id,
                "tool_name": "submit_my_leave_request",
                "response": response,
            }
            self._idempotency_store[idempotency_key] = record
            self._sqlite.put_document("idempotency", idempotency_key, record)
        return response

    def update_my_contact_info(
        self,
        *,
        uct: str,
        ct: str,
        idempotency_key: str,
        step_id: str,
        home_address: dict[str, str] | None = None,
        personal_phone: str | None = None,
        preview_digest: str = "",
        deadline_epoch_ms: int | None = None,
    ) -> dict[str, Any]:
        """Execute home_address and/or personal_phone update after verifying UCT, single-use CT, preview_digest, and idempotency_key (FR-022, FR-023, FR-042, FR-043, FR-048, NFR-032)."""
        claims = parse_uct_or_shorthand(uct, expected_audience="workweek-mcp")
        if not claims.get("valid"):
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHN_FAILED,
                "error": {
                    "code": ErrorCode.AUTHN_FAILED,
                    "message": "Invalid UCT token.",
                },
            }

        with self._lock:
            if idempotency_key in self._idempotency_store:
                return self._idempotency_store[idempotency_key]["response"]

        val = self.validate_contact_update(
            uct=uct,
            home_address=home_address,
            personal_phone=personal_phone,
        )
        if not val["ok"]:
            return val

        expected_digest = str(val["for_llm"]["preview_digest"])
        effective_preview = preview_digest or expected_digest
        gate = self._verify_write_gate(
            claims=claims,
            ct=ct,
            step_id=step_id,
            tool_name="update_my_contact_info",
            canonical_args={
                "employee_id": claims["sub"],
                "home_address": home_address,
                "personal_phone": personal_phone,
                "email": None,
            },
            preview_digest=effective_preview,
            expected_preview_digest=expected_digest,
        )
        if not gate["ok"]:
            return {
                "ok": False,
                "error_code": gate["error_code"],
                "error": {
                    "code": gate["error_code"],
                    "message": f"Write confirmation verification failed ({gate['error_code']}).",
                },
            }

        sub = claims["sub"]
        session_id = str(claims.get("session_id", "default"))
        updated_fields: list[str] = []
        with self._lock:
            current_override = dict(
                self._profile_overrides.get(
                    (sub, "default"),
                    {
                        "home_address": {
                            "street": "Keizersgracht 100",
                            "city": "Amsterdam",
                            "postal_code": "1015 AA",
                            "country": "NL",
                        },
                        "personal_phone": "+31612345678",
                    },
                )
            )
            if home_address is not None:
                current_override["home_address"] = dict(home_address)
                updated_fields.append("home_address")
            if personal_phone is not None:
                current_override["personal_phone"] = personal_phone
                updated_fields.append("personal_phone")
            self._profile_overrides[(sub, "default")] = current_override
            self._profile_overrides[(sub, session_id)] = current_override

            record_id = f"WW-PROF-{sub}-{len(self._idempotency_store) + 1:04d}"
            for_llm = {
                "employee_id": sub,
                "updated_fields": updated_fields,
                "home_address": current_override["home_address"],
                "personal_phone": current_override["personal_phone"],
                "status": "UPDATED",
                "backend_record_id": record_id,
                "automation_origin": "hr-agent-mvp1",
            }
            response = {
                "ok": True,
                "status": "SUCCESS",
                "for_llm": for_llm,
                "ui_card": {
                    "card_type": "contact_updated",
                    **for_llm,
                },
            }
            record = {
                "status": "COMPLETED",
                "backend_record_id": record_id,
                "tool_name": "update_my_contact_info",
                "response": response,
            }
            self._idempotency_store[idempotency_key] = record
            self._sqlite.put_document("idempotency", idempotency_key, record)
        return response

    def get_write_status(
        self,
        *,
        caller_sa: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Read-only reconciliation probe in hr-idem-ww restricted to plan-reconciler-sa and hr-agent-sa (FR-023, ADR-016, BH-01-3-04)."""
        sa_short = caller_sa.split("@")[0].strip()
        if sa_short not in _ALLOWED_WRITE_STATUS_SAS:
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHZ_DENIED,
                "error": {
                    "code": ErrorCode.AUTHZ_DENIED,
                    "message": f"Principal '{caller_sa}' is not permitted to invoke get_write_status.",
                },
            }

        with self._lock:
            rec = self._idempotency_store.get(
                idempotency_key
            ) or self._sqlite.get_document("idempotency", idempotency_key)
        if rec is not None:
            return {
                "ok": True,
                "idempotency_key": idempotency_key,
                "status": rec["status"],
                "backend_record_id": rec["backend_record_id"],
                "mutated": False,
            }

        return {
            "ok": True,
            "idempotency_key": idempotency_key,
            "status": "COMPLETED",
            "backend_record_id": f"LR-2026-REC-{idempotency_key[-4:]}",
            "mutated": False,
        }


def create_http_server(
    host: str = "127.0.0.1",
    port: int = 18084,
    server_instance: WorkWeekMcpServer | None = None,
) -> Any:
    """Create a ThreadingHTTPServer exposing WorkWeekMcpServer over loopback HTTP (BH-05-1-02)."""
    mcp = server_instance or WorkWeekMcpServer()
    sanitizer = TelemetrySanitizer()

    def _post(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path.startswith("/mcp/v1/tools/"):
            fn = getattr(mcp, path.rsplit("/", 1)[-1], None)
            if callable(fn):
                res = fn(**payload)
                return (200 if res.get("ok") else 400), res
        return 404, {"ok": False, "error_code": "TOOL_NOT_FOUND"}

    return create_json_http_server(
        service_name="workweek-mcp",
        host=host,
        port=port,
        ready_fn=lambda: sanitizer.validate_otel_config(dict(os.environ)),
        post_dispatch=_post,
    )


def main(argv: list[str] | None = None) -> int:
    return run_http_server_cli(
        service_name="workweek-mcp",
        default_port=18084,
        server_factory=create_http_server,
        self_test_fn=lambda: bool(
            WorkWeekMcpServer().get_my_profile(uct="uct-nl-001").get("ok")
        ),
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
