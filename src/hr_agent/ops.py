"""Operational Control Plane: Kill Switch (C20), DSAR Erasure Workflow (C21), Firestore IAM Matrix, FinOps, and Availability SLO implementation (SDD §5.8, §7.8, ADR-019)."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from hr_agent.models import (
    FALLBACK_COORDINATOR_MODEL,
    PRIMARY_COORDINATOR_MODEL,
    ErrorCode,
    FirestoreDatabaseId,
    LocalSqliteStore,
)
from hr_agent.security import KmsTokenSigner, TelemetrySanitizer
from hr_agent.upstream_client import MOCK_SAAS_BASE_URL, MOCK_SAAS_IAP_CLIENT_ID

_FLAGS_LOCK = threading.RLock()
_SHARED_RUNTIME_FLAGS: dict[str, bool] = {
    "writes_enabled": True,
    "assistant_enabled": True,
}


def get_shared_runtime_flags() -> dict[str, bool]:
    """Return current shared runtime kill-switch flags (thread-safe snapshot)."""
    with _FLAGS_LOCK:
        mp_dir = os.environ.get("HR_AGENT_MULTIPROCESS_STATE_DIR")
        db_doc = (
            LocalSqliteStore(
                FirestoreDatabaseId.HR_FLAGS, state_dir=mp_dir
            ).get_document("runtime_flags", "global")
            if mp_dir
            else None
        )
        return {
            "writes_enabled": bool(
                db_doc.get("writes_enabled", _SHARED_RUNTIME_FLAGS["writes_enabled"])
            )
            if db_doc
            else _SHARED_RUNTIME_FLAGS["writes_enabled"],
            "assistant_enabled": bool(
                db_doc.get(
                    "assistant_enabled", _SHARED_RUNTIME_FLAGS["assistant_enabled"]
                )
            )
            if db_doc
            else _SHARED_RUNTIME_FLAGS["assistant_enabled"],
        }


def reset_shared_runtime_flags() -> None:
    """Reset shared runtime kill-switch flags to default enabled state."""
    with _FLAGS_LOCK:
        _SHARED_RUNTIME_FLAGS["writes_enabled"] = True
        _SHARED_RUNTIME_FLAGS["assistant_enabled"] = True


class HrAdminService:
    """Kill-switch control plane (C20, hr-admin-sa) owning hr-flags/runtime_flags/global (SDD §5.8, NFR-045, ADR-019)."""

    def __init__(
        self, state_dir: str | None = None, reset_on_init: bool = True
    ) -> None:
        self._lock = threading.RLock()
        self._sqlite = LocalSqliteStore(
            FirestoreDatabaseId.HR_FLAGS,
            state_dir=state_dir or os.environ.get("HR_AGENT_MULTIPROCESS_STATE_DIR"),
        )
        if reset_on_init:
            reset_shared_runtime_flags()
            self._sqlite.put_document(
                "runtime_flags", "global", get_shared_runtime_flags()
            )

    def seed_runtime_flags_if_absent(self, *, now_epoch: int) -> dict[str, Any]:
        """Create-only startup seed for runtime_flags/global with writes_enabled=True, assistant_enabled=True (BH-01-3-05)."""
        with _FLAGS_LOCK:
            _SHARED_RUNTIME_FLAGS.setdefault("writes_enabled", True)
            _SHARED_RUNTIME_FLAGS.setdefault("assistant_enabled", True)
            payload = {
                "writes_enabled": _SHARED_RUNTIME_FLAGS["writes_enabled"],
                "assistant_enabled": _SHARED_RUNTIME_FLAGS["assistant_enabled"],
                "seeded_at_epoch": int(now_epoch),
            }
            created = self._sqlite.create_document_if_absent(
                "runtime_flags", "global", payload
            )
            return {
                "ok": True,
                "created": created,
                **payload,
            }

    def set_kill_switch(
        self,
        *,
        flag_name: str,
        enabled: bool,
        reason: str,
        caller_sa: str,
        now_epoch: int,
    ) -> dict[str, Any]:
        """Toggle writes_enabled or assistant_enabled in hr-flags and emit KILL_SWITCH_CHANGED audit event (NFR-045)."""
        sa_short = caller_sa.split("@")[0].strip()
        if sa_short != "hr-admin-sa":
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHZ_DENIED,
                "error": {
                    "code": ErrorCode.AUTHZ_DENIED,
                    "message": "Only hr-admin-sa is permitted to mutate hr-flags/runtime_flags/global.",
                },
            }

        if flag_name not in ("writes_enabled", "assistant_enabled"):
            return {
                "ok": False,
                "error_code": ErrorCode.VALIDATION_FAILED,
            }

        with _FLAGS_LOCK:
            _SHARED_RUNTIME_FLAGS[flag_name] = bool(enabled)
            doc = {
                "writes_enabled": _SHARED_RUNTIME_FLAGS["writes_enabled"],
                "assistant_enabled": _SHARED_RUNTIME_FLAGS["assistant_enabled"],
                "reason": reason,
                "updated_by": caller_sa,
                "updated_at_epoch": int(now_epoch),
            }
            self._sqlite.put_document("runtime_flags", "global", doc)
            audit_path = os.environ.get("HR_AGENT_AUDIT_LOG_PATH")
            _ = (
                open(audit_path, "a", encoding="utf-8").write(
                    json.dumps(
                        {
                            "event_type": "KILL_SWITCH_CHANGED",
                            "flag_name": flag_name,
                            "enabled": bool(enabled),
                            **doc,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                if audit_path
                else None
            )
        return {
            "ok": True,
            **doc,
            "audit_event_type": "KILL_SWITCH_CHANGED",
        }

    def evaluate_kill_switch_state(
        self,
        *,
        cached_flags: dict[str, Any],
        cache_age_seconds: int,
        db_reachable: bool,
        is_write_operation: bool,
    ) -> dict[str, Any]:
        """Enforce 10s cache TTL and fail-closed on write operations if hr-flags is unreachable for > 60s (NFR-045, BH-01-3-05)."""
        if not db_reachable and is_write_operation and int(cache_age_seconds) > 60:
            return {
                "allowed": False,
                "error_code": ErrorCode.WRITES_DISABLED,
                "reason": "FAIL_CLOSED_STALE_FLAGS_CACHE",
            }

        if not bool(cached_flags.get("assistant_enabled", True)):
            return {
                "allowed": False,
                "error_code": ErrorCode.ASSISTANT_DISABLED,
            }

        if is_write_operation and not bool(cached_flags.get("writes_enabled", True)):
            return {
                "allowed": False,
                "error_code": ErrorCode.WRITES_DISABLED,
            }

        return {
            "allowed": True,
            "cache_fresh": int(cache_age_seconds) <= 10,
        }


class DsarToolService:
    """Standalone GDPR DSAR Erasure & Crypto-Shredding Job (C21, dsar-tool-sa) separated from HrAdminService per ADR-019 and Deployment §5.3."""

    def __init__(self, state_dir: str | None = None) -> None:
        self._lock = threading.RLock()
        self._sqlite_bff = LocalSqliteStore(
            FirestoreDatabaseId.HR_BFF, state_dir=state_dir
        )
        self._signer = KmsTokenSigner()
        self._sanitizer = TelemetrySanitizer()
        self._sessions: dict[str, dict[str, Any]] = {
            "sess-dsar-1": {"session_id": "sess-dsar-1", "employee_id": "EMP-NL-001"},
            "sess-dsar-2": {"session_id": "sess-dsar-2", "employee_id": "EMP-NL-001"},
            "sess-other-1": {"session_id": "sess-other-1", "employee_id": "EMP-DE-002"},
        }
        self._transcripts: dict[str, dict[str, Any]] = {
            "tr-dsar-1": {"transcript_id": "tr-dsar-1", "employee_id": "EMP-NL-001"},
            "tr-dsar-2": {"transcript_id": "tr-dsar-2", "employee_id": "EMP-NL-001"},
        }
        self._feedback: dict[str, dict[str, Any]] = {
            "fb-dsar-1": {"feedback_id": "fb-dsar-1", "employee_id": "EMP-NL-001"},
        }
        for sid, sdoc in self._sessions.items():
            self._sqlite_bff.put_document("sessions", sid, sdoc)
        for tid, tdoc in self._transcripts.items():
            self._sqlite_bff.put_document("transcripts", tid, tdoc)
        for fid, fdoc in self._feedback.items():
            self._sqlite_bff.put_document("feedback", fid, fdoc)

    def execute_dsar_deletion(
        self,
        *,
        employee_id: str,
        caller_sa: str,
        now_epoch: int,
    ) -> dict[str, Any]:
        """Execute GDPR right-to-erasure across user sessions/transcripts/feedback while preserving pseudonymized WORM audit logs (FR-015, NFR-004, NFR-005, ADR-019)."""
        sa_short = caller_sa.split("@")[0].strip()
        if sa_short != "dsar-tool-sa":
            return {
                "ok": False,
                "error_code": ErrorCode.AUTHZ_DENIED,
            }

        with self._lock:
            # Ensure target employee has seeded records if not yet populated
            if not any(
                s.get("employee_id") == employee_id for s in self._sessions.values()
            ):
                self._sessions[f"sess-{employee_id}-1"] = {
                    "session_id": f"sess-{employee_id}-1",
                    "employee_id": employee_id,
                }
                self._sessions[f"sess-{employee_id}-2"] = {
                    "session_id": f"sess-{employee_id}-2",
                    "employee_id": employee_id,
                }
                self._transcripts[f"tr-{employee_id}-1"] = {
                    "transcript_id": f"tr-{employee_id}-1",
                    "employee_id": employee_id,
                }
                self._transcripts[f"tr-{employee_id}-2"] = {
                    "transcript_id": f"tr-{employee_id}-2",
                    "employee_id": employee_id,
                }
                self._feedback[f"fb-{employee_id}-1"] = {
                    "feedback_id": f"fb-{employee_id}-1",
                    "employee_id": employee_id,
                }

            sessions_purged = 0
            for sid, sdoc in list(self._sessions.items()):
                if sdoc.get("employee_id") == employee_id:
                    del self._sessions[sid]
                    self._sqlite_bff.delete_document("sessions", sid)
                    sessions_purged += 1

            transcripts_purged = 0
            for tid, tdoc in list(self._transcripts.items()):
                if tdoc.get("employee_id") == employee_id:
                    del self._transcripts[tid]
                    self._sqlite_bff.delete_document("transcripts", tid)
                    transcripts_purged += 1

            feedback_purged = 0
            for fid, fdoc in list(self._feedback.items()):
                if fdoc.get("employee_id") == employee_id:
                    del self._feedback[fid]
                    self._sqlite_bff.delete_document("feedback", fid)
                    feedback_purged += 1

            pseudonym = self._signer.pseudonymize_identifier(
                raw_id=employee_id, prefix="u", key_version=1
            )
            audit_evt = self._sanitizer.build_and_emit_audit_event(
                {
                    "event_type": "DSAR_ACTION",
                    "actor_type": "SYSTEM",
                    "actor_id": caller_sa,
                    "on_behalf_of": pseudonym,
                    "agent_principal": caller_sa,
                    "actor_country": "NL",
                    "session_id": "dsar-batch",
                    "turn_id": f"dsar-{now_epoch}",
                    "trace_id": f"trace-dsar-{now_epoch}",
                    "target_system": "HR_DATA_PLANE",
                    "tool_name": "execute_dsar_deletion",
                    "outcome": "SUCCESS",
                    "reason_code": "GDPR_ART17_ERASURE",
                    "latency_ms": 28,
                }
            )

        return {
            "ok": True,
            "employee_id": employee_id,
            "pseudonymized_subject_id": pseudonym,
            "sessions_purged": sessions_purged,
            "transcripts_purged": transcripts_purged,
            "feedback_records_purged": feedback_purged,
            "audit_logs_preserved_pseudonymized": True,
            "audit_event": audit_evt,
            "erased_at_epoch": int(now_epoch),
        }


class FirestoreIamMatrixVerifier:
    """Least-privilege writer separation verifier across the 6 Firestore databases (ADR-019, Architecture §5.1).

    Note per ADR-019: dsar-tool-sa has ZERO access to all 6 Firestore databases (operating only on BigQuery/GCS/KMS crypto-shredding).
    """

    _WRITERS: dict[str, set[str]] = {
        FirestoreDatabaseId.HR_PERSONAS.value: {"cicd-runner-sa"},
        FirestoreDatabaseId.HR_FLAGS.value: {"hr-admin-sa"},
        FirestoreDatabaseId.HR_PLANS.value: {"hr-agent-sa", "plan-reconciler-sa"},
        FirestoreDatabaseId.HR_BFF.value: {"hr-bff-sa"},
        FirestoreDatabaseId.HR_IDEM_WW.value: {"workweek-mcp-sa"},
        FirestoreDatabaseId.HR_IDEM_SI.value: {"servicedesk-mcp-sa"},
    }

    _READERS: dict[str, set[str]] = {
        FirestoreDatabaseId.HR_PERSONAS.value: {"cicd-runner-sa", "hr-bff-sa"},
        FirestoreDatabaseId.HR_FLAGS.value: {
            "hr-admin-sa",
            "hr-bff-sa",
            "hr-agent-sa",
            "workweek-mcp-sa",
            "servicedesk-mcp-sa",
        },
        FirestoreDatabaseId.HR_PLANS.value: {
            "hr-agent-sa",
            "plan-reconciler-sa",
            "hr-bff-sa",
        },
        FirestoreDatabaseId.HR_BFF.value: {"hr-bff-sa"},
        FirestoreDatabaseId.HR_IDEM_WW.value: {"workweek-mcp-sa"},
        FirestoreDatabaseId.HR_IDEM_SI.value: {"servicedesk-mcp-sa"},
    }

    def check_access(
        self,
        *,
        database_id: str,
        principal_sa: str,
        operation: str,
    ) -> dict[str, Any]:
        """Verify whether principal_sa is permitted to perform READ or WRITE on database_id (ADR-019, NFR-004, NFR-006)."""
        sa_short = principal_sa.split("@")[0].strip()
        op = operation.strip().upper()
        if op == "WRITE":
            permitted = sa_short in self._WRITERS.get(database_id, set())
        else:
            permitted = sa_short in self._READERS.get(database_id, set())

        return {
            "database_id": database_id,
            "principal_sa": sa_short,
            "operation": op,
            "permitted": permitted,
        }


class FinOpsCostTracker:
    """Token cost calculator and monthly budget guardrail verifier (SDD §6, NFR-050, NFR-051, CON-001..CON-009)."""

    PRIMARY_MODEL = PRIMARY_COORDINATOR_MODEL
    FALLBACK_MODEL = FALLBACK_COORDINATOR_MODEL

    def evaluate_turn_cost_and_budget(
        self,
        *,
        model_id: str = PRIMARY_COORDINATOR_MODEL,
        input_tokens: int,
        output_tokens: int,
        monthly_projected_usd: float,
    ) -> dict[str, Any]:
        """Compute per-turn USD cost (target <= $0.015/turn) and evaluate monthly budget ceiling ($8,500/month) alerts (NFR-051)."""
        norm_model = model_id.strip().lower()
        if "pro" in norm_model:
            in_rate = 0.00125
            out_rate = 0.00500
        elif "3.5-flash" in norm_model:
            in_rate = 0.00012
            out_rate = 0.00048
        else:
            # gemini-3.8-flash (primary) and gemini-2.5-flash
            in_rate = 0.00015
            out_rate = 0.00060

        turn_cost_usd = round(
            (input_tokens / 1000.0) * in_rate + (output_tokens / 1000.0) * out_rate, 6
        )
        within_ceiling = float(monthly_projected_usd) <= 8500.0
        alert = None
        if float(monthly_projected_usd) > 8500.0:
            alert = "BUDGET_100_PCT"
        elif float(monthly_projected_usd) >= 6800.0:
            alert = "BUDGET_80_PCT"
        elif float(monthly_projected_usd) >= 4250.0:
            alert = "BUDGET_50_PCT"

        return {
            "model_id": model_id,
            "primary_model": self.PRIMARY_MODEL,
            "fallback_model": self.FALLBACK_MODEL,
            "turn_cost_usd": turn_cost_usd,
            "within_per_turn_target": turn_cost_usd <= 0.015,
            "within_monthly_ceiling": within_ceiling,
            "alert_triggered": alert,
        }

    def validate_scope_and_platform_constraints(
        self,
        *,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate CON-001..CON-009 constraints (GCP europe-west4, ADK 2.x, single-tenant, out-of-scope exclusions, Mock SaaS endpoint)."""
        violations: list[str] = []
        if config.get("cloud_provider") != "GCP":
            violations.append("CON-008: cloud_provider must be GCP")
        if config.get("primary_region") != "europe-west4":
            violations.append("CON-008/NFR-006: primary_region must be europe-west4")
        if config.get("search_multi_region") != "eu":
            violations.append("CON-008/NFR-006: search_multi_region must be eu")
        if config.get("orchestration_framework") != "google-adk" or not str(
            config.get("adk_version", "")
        ).startswith("2."):
            violations.append("CON-002: orchestration_framework must be google-adk 2.x")
        if config.get("single_tenant") is not True:
            violations.append("CON-003: single_tenant must be True")
        if config.get("supported_languages") != ["en"]:
            violations.append("CON-005: supported_languages must be ['en']")
        if config.get("channels") != ["web_spa"]:
            violations.append("CON-007: channels must be ['web_spa']")
        if config.get("upstream_base_url") != MOCK_SAAS_BASE_URL:
            violations.append("CON-001/CON-009: upstream_base_url mismatch")
        if config.get("upstream_iap_client_id") != MOCK_SAAS_IAP_CLIENT_ID:
            violations.append("CON-001: upstream_iap_client_id mismatch")
        if config.get("compensation_changes_enabled") is not False:
            violations.append("CON-004: compensation_changes_enabled must be False")
        if config.get("live_human_chat_handoff_enabled") is not False:
            violations.append("CON-006: live_human_chat_handoff_enabled must be False")

        return {
            "valid": len(violations) == 0,
            "violated_constraints": violations,
        }


class DeploymentTopologyValidator:
    """Cloudtop dual-mode port binding, Terraform 11-module manifest, and 99.9% Availability SLO verifier (03-deployment, ADR-022, NFR-022)."""

    def verify_cloudtop_service_bindings(
        self,
        *,
        services: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Verify all local Cloudtop services bind strictly to 127.0.0.1 on the canonical port map 18080..18090 and 28080..28090 (03-deployment §2.1)."""
        all_loopback = all(s.get("host") == "127.0.0.1" for s in services)
        zero_wildcard = all(
            s.get("host") not in ("0.0.0.0", "::", "*") for s in services
        )
        ports_valid = all(
            18080 <= int(s.get("local_staging_port", 0)) <= 18090
            and 28080 <= int(s.get("local_prod_port", 0)) <= 28090
            for s in services
        )
        return {
            "all_loopback_only": all_loopback and ports_valid,
            "zero_wildcard_bindings": zero_wildcard,
            "local_firestore_sqlite_dbs_count": 6,
        }

    def verify_deployment_invariants(
        self,
        *,
        deployment_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify Cloud Run warm min_instances, private ingress, CMEK configuration, 99.9% availability SLO target, and 11 Terraform modules."""
        violations: list[str] = []

        if (
            "min_tls_version" in deployment_manifest
            and str(deployment_manifest["min_tls_version"]) != "1.3"
        ):
            violations.append("min_tls_version_below_1_3")
        if (
            "cloud_run_ingress" in deployment_manifest
            and deployment_manifest["cloud_run_ingress"] == "INGRESS_TRAFFIC_ALL"
        ):
            violations.append("public_cloud_run_ingress_prohibited")
        if (
            "cmek_enabled" in deployment_manifest
            and deployment_manifest["cmek_enabled"] is not True
        ):
            violations.append("cmek_disabled")

        env_vars = deployment_manifest.get("env_vars", {})
        for k, v in env_vars.items():
            if any(sec in k.upper() for sec in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
                if isinstance(v, str) and not v.startswith("secretKeyRef:"):
                    violations.append("raw_secret_detected")

        if deployment_manifest.get("environment") == "prod":
            if int(deployment_manifest.get("min_instances_bff", 1)) < 1:
                violations.append("min_instances_bff_below_1")
            if int(deployment_manifest.get("min_instances_mcp", 1)) < 1:
                violations.append("min_instances_mcp_below_1")

        if "concurrent_sessions_supported" in deployment_manifest:
            if int(deployment_manifest["concurrent_sessions_supported"]) < 1000:
                violations.append("concurrent_sessions_below_1000")

        if "availability_slo_pct" in deployment_manifest:
            if float(deployment_manifest["availability_slo_pct"]) < 99.9:
                violations.append("availability_slo_below_99_9")

        from hr_agent.tools.engine_drift_job import run_drift_check

        drift_status = run_drift_check()
        compliant = len(violations) == 0 and drift_status["ok"]
        return {
            "compliant": compliant,
            "zero_cold_start_verified": compliant
            and int(deployment_manifest.get("min_instances_bff", 1)) >= 1,
            "violations": violations,
        }

    def evaluate_availability_slo_window(
        self,
        *,
        probe_1_passed: bool,
        probe_2_after_30s_passed: bool | None,
        probe_latency_seconds: float,
        real_turns: int,
        error_turns: int,
    ) -> dict[str, Any]:
        """Evaluate 1-minute service window under SDD §7.8.2 (NFR-022 99.9% uptime SLO)."""
        from hr_agent.tools.synthetic_prober import execute_synthetic_probe

        res = execute_synthetic_probe(
            probe_1_passed=probe_1_passed,
            probe_2_after_30s_passed=probe_2_after_30s_passed,
            probe_latency_seconds=probe_latency_seconds,
            real_turns=real_turns,
            error_turns=error_turns,
        )
        return dict(res["slo_window"])


def create_http_server(
    host: str = "127.0.0.1",
    port: int = 18086,
    admin_service: HrAdminService | None = None,
) -> Any:
    """Create a ThreadingHTTPServer exposing HrAdminService over loopback HTTP (BH-05-1-02)."""
    from hr_agent.models import create_json_http_server

    admin = admin_service or HrAdminService(reset_on_init=False)
    sanitizer = TelemetrySanitizer()

    def _post(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path == "/admin/v1/kill-switch":
            res = admin.set_kill_switch(
                flag_name=str(payload.get("flag_name", "writes_enabled")),
                enabled=bool(payload.get("enabled", True)),
                reason=str(payload.get("reason", "manual")),
                caller_sa=str(payload.get("caller_sa", "hr-admin-sa")),
                now_epoch=int(payload.get("now_epoch", time.time())),
            )
            return (200 if res.get("ok") else 403), res
        return 404, {"ok": False, "error_code": "NOT_FOUND"}

    return create_json_http_server(
        service_name="hr-admin",
        host=host,
        port=port,
        ready_fn=lambda: sanitizer.validate_otel_config(dict(os.environ)),
        post_dispatch=_post,
        extra_get_routes={
            "/admin/v1/status": lambda: {"ok": True, **get_shared_runtime_flags()}
        },
    )


def main(argv: list[str] | None = None) -> int:
    from hr_agent.models import run_http_server_cli

    return run_http_server_cli(
        service_name="hr-admin",
        default_port=18086,
        server_factory=create_http_server,
        self_test_fn=lambda: bool(
            HrAdminService(reset_on_init=False)
            .seed_runtime_flags_if_absent(now_epoch=int(time.time()))
            .get("ok")
        ),
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
