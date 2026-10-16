"""Phase 07 Live HTTP & Cross-Process Integration Tests (TC-IT-026..TC-IT-030).

Exercises real loopback (127.0.0.1) HTTP server boundaries, cross-process SQLite
kill-switch synchronization, Blue/Green runtime state transitions, synthetic prober
live HTTP checks, and operator CLI contracts without touching any of the 14 locked
Phase 04 test files in `test-manifest.sha256`.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from hr_agent.bff import BffService
from hr_agent.bff import create_http_server as create_bff_http_server
from hr_agent.ops import (
    HrAdminService,
    get_shared_runtime_flags,
    reset_shared_runtime_flags,
)
from hr_agent.ops import create_http_server as create_ops_http_server
from hr_agent.tools.engine_drift_job import main as drift_main
from hr_agent.tools.synthetic_prober import main as prober_main

pytestmark = pytest.mark.integration


def _http_req(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, dict(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        return exc.code, dict(json.loads(exc.read().decode("utf-8")))


def test_tc_it_026_live_http_geofencing_tw_deny_cn_allow_and_audit_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-IT-026 (FR-026, FR-027, FR-028, Runbook §4.3): Verify live HTTP TW block (403 REGION_BLOCKED + region=TW + audit log) and CN allow (200 + country_code=CN)."""
    audit_file = tmp_path / "hr-agent-audit.jsonl"
    monkeypatch.setenv("HR_AGENT_AUDIT_LOG_PATH", str(audit_file))

    bff = BffService(state_dir=str(tmp_path))
    srv = create_bff_http_server(host="127.0.0.1", port=0, service=bff)
    port = srv.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        # 1. Root SPA bootstrap from TW -> HTTP 403 REGION_BLOCKED
        st_root_tw, body_root_tw = _http_req(
            f"{base_url}/", headers={"X-Client-Region": "TW"}
        )
        assert st_root_tw == 403
        assert body_root_tw["error"]["code"] == "REGION_BLOCKED"
        assert body_root_tw["error"]["region"] == "TW"

        # 2. CN corporate origin connecting from TW -> HTTP 403 REGION_BLOCKED
        st_me_tw, body_me_tw = _http_req(
            f"{base_url}/api/v1/me",
            headers={"X-Client-Region": "TW", "X-Corporate-Origin-Region": "CN"},
        )
        assert st_me_tw == 403
        assert body_me_tw["error"]["code"] == "REGION_BLOCKED"
        assert body_me_tw["error"]["region"] == "TW"

        # 3. Mainland China (CN) evaluator -> HTTP 200 with country_code == CN
        st_cn, body_cn = _http_req(
            f"{base_url}/api/v1/me",
            headers={
                "X-Dev-User-Email": "cn.evaluator@example.com",
                "X-Client-Region": "CN",
            },
        )
        assert st_cn == 200
        assert body_cn["country"] == "CN"
        assert body_cn["country_code"] == "CN"

        # 4. Audit log recorded AUTHZ_DENIED / REGION_BLOCKED_TW
        audit_lines = [
            json.loads(line)
            for line in audit_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(
            e.get("event_type") == "AUTHZ_DENIED"
            and e.get("reason_code") == "REGION_BLOCKED_TW"
            for e in audit_lines
        )
    finally:
        srv.shutdown()
        srv.server_close()


def test_tc_it_027_live_http_cross_user_isolation_and_non_nl_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-IT-027 (FR-004, FR-015, FR-021, BH-06-1-01, BH-07-1-03): Verify multi-service HTTP delegation (BFF -> Orchestrator -> WorkWeek/ServiceDesk/PolicyRAG MCPs), cross-user isolation, and DE confirmation resolution."""
    from hr_agent.orchestrator import AgentCoordinator
    from hr_agent.orchestrator import create_http_server as create_orch_http_server
    from hr_agent.policy_rag import create_http_server as create_rag_http_server
    from hr_agent.servicedesk_mcp import create_http_server as create_sd_http_server
    from hr_agent.workweek_mcp import create_http_server as create_ww_http_server

    monkeypatch.setenv("HR_AGENT_UCT_SIGNING_KEY", "test-it027-uct-seed")
    monkeypatch.setenv("HR_AGENT_CT_SIGNING_KEY", "test-it027-ct-seed")

    ww_srv = create_ww_http_server(host="127.0.0.1", port=0)
    sd_srv = create_sd_http_server(host="127.0.0.1", port=0)
    rag_srv = create_rag_http_server(host="127.0.0.1", port=0)
    ww_url = f"http://127.0.0.1:{ww_srv.server_address[1]}"
    sd_url = f"http://127.0.0.1:{sd_srv.server_address[1]}"
    rag_url = f"http://127.0.0.1:{rag_srv.server_address[1]}"

    coord = AgentCoordinator(
        state_dir=str(tmp_path),
        workweek_mcp_url=ww_url,
        servicedesk_mcp_url=sd_url,
        policy_rag_url=rag_url,
    )
    orch_srv = create_orch_http_server(host="127.0.0.1", port=0, coordinator=coord)
    orch_url = f"http://127.0.0.1:{orch_srv.server_address[1]}"

    bff = BffService(
        state_dir=str(tmp_path),
        orchestrator_url=orch_url,
        workweek_mcp_url=ww_url,
        servicedesk_mcp_url=sd_url,
    )
    srv = create_bff_http_server(host="127.0.0.1", port=0, service=bff)
    port = srv.server_address[1]
    base_url = f"http://127.0.0.1:{port}"

    servers = (ww_srv, sd_srv, rag_srv, orch_srv, srv)
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        st_s, s_body = _http_req(
            f"{base_url}/api/v1/sessions",
            method="POST",
            payload={
                "now_epoch": 1780000100,
                "headers": {"X-Goog-IAP-JWT-Assertion": "jwt-de-002"},
            },
        )
        assert st_s == 201
        sid_de = s_body["session_id"]

        st_m, m_body = _http_req(
            f"{base_url}/api/v1/sessions/{sid_de}/messages",
            method="POST",
            payload={
                "text": "Book vacation from 2026-06-15 to 2026-06-17 per policy",
                "now_epoch": 1780000105,
                "headers": {"X-Goog-IAP-JWT-Assertion": "jwt-de-002"},
            },
        )
        assert st_m == 200
        tool_status = m_body["events"][0]["data"]
        assert tool_status["delegated_via_http"] is True
        assert tool_status["orchestrator_target"] == f"{orch_url}/v1/turn"
        assert (
            f"{ww_url}/mcp/v1/tools/get_my_leave_balances"
            in tool_status["downstream_http_targets"]
        )
        assert (
            f"{rag_url}/rag/v1/retrieve_policy_chunks"
            in tool_status["downstream_http_targets"]
        )
        conf_id = next(
            e for e in m_body["events"] if e["event"] == "confirmation_required"
        )["data"]["confirmation_id"]

        # Cross-user confirmation attempt by EMP-NL-001 -> HTTP 403
        st_cross, cross_body = _http_req(
            f"{base_url}/api/v1/sessions/{sid_de}/confirmations/{conf_id}",
            method="POST",
            payload={
                "decision": "confirm",
                "now_epoch": 1780000110,
                "headers": {"X-Goog-IAP-JWT-Assertion": "jwt-nl-001"},
            },
        )
        assert st_cross == 403
        assert cross_body["error_code"] == "CROSS_USER_ACCESS_DENIED"

        # Owner EMP-DE-002 confirms -> HTTP 200 COMPLETED across Orchestrator & WorkWeek MCP HTTP boundaries
        st_ok, ok_body = _http_req(
            f"{base_url}/api/v1/sessions/{sid_de}/confirmations/{conf_id}",
            method="POST",
            payload={
                "decision": "confirm",
                "now_epoch": 1780000115,
                "headers": {"X-Goog-IAP-JWT-Assertion": "jwt-de-002"},
            },
        )
        assert st_ok == 200
        assert ok_body["status"] == "COMPLETED"
        assert ok_body["delegated_via_http"] is True
        assert ok_body["orchestrator_target"] == f"{orch_url}/v1/plan/execute"

        # Verify downstream HTTP servers recorded POST requests
        _, orch_hz = _http_req(f"{orch_url}/healthz")
        _, ww_hz = _http_req(f"{ww_url}/healthz")
        _, rag_hz = _http_req(f"{rag_url}/healthz")
        assert orch_hz["post_request_count"] >= 2
        assert ww_hz["post_request_count"] >= 2
        assert rag_hz["post_request_count"] >= 1
    finally:
        for s in reversed(servers):
            s.shutdown()
            s.server_close()


def test_tc_it_028_cross_process_sqlite_kill_switch_and_audit_trail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-IT-028 (NFR-045, ADR-019, Runbook §8.2, BH-07-1-03): Verify SQLite-backed runtime_flags synchronization, live BFF HTTP 503 write rejection, HTTP 200 read availability, and KILL_SWITCH_CHANGED audit trail."""
    fs_dir = tmp_path / "firestore"
    audit_file = tmp_path / "hr-agent-audit.jsonl"
    monkeypatch.setenv("HR_AGENT_MULTIPROCESS_STATE_DIR", str(fs_dir))
    monkeypatch.setenv("HR_AGENT_AUDIT_LOG_PATH", str(audit_file))

    admin = HrAdminService(state_dir=str(fs_dir), reset_on_init=True)
    srv = create_ops_http_server(host="127.0.0.1", port=0, admin_service=admin)
    port = srv.server_address[1]
    admin_url = f"http://127.0.0.1:{port}"

    bff = BffService(state_dir=str(fs_dir))
    bff_srv = create_bff_http_server(host="127.0.0.1", port=0, service=bff)
    bff_url = f"http://127.0.0.1:{bff_srv.server_address[1]}"

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=bff_srv.serve_forever, daemon=True).start()
    try:
        st_off, body_off = _http_req(
            f"{admin_url}/admin/v1/kill-switch",
            method="POST",
            payload={
                "flag_name": "writes_enabled",
                "enabled": False,
                "reason": "ROLLBACK_PRE_CUTOVER_DRAIN",
                "caller_sa": "hr-admin-sa",
                "now_epoch": 1780000200,
            },
        )
        assert st_off == 200 and body_off["writes_enabled"] is False
        assert get_shared_runtime_flags()["writes_enabled"] is False

        # Live HTTP write confirmation rejected with HTTP 503 WRITES_DISABLED
        st_write, body_write = _http_req(
            f"{bff_url}/api/v1/sessions/sess-nl-001/confirmations/conf-001",
            method="POST",
            payload={"decision": "confirm", "now_epoch": 1780000205},
        )
        assert st_write == 503
        assert body_write["error_code"] == "WRITES_DISABLED"

        # Live HTTP read query (CUJ-1) remains available with HTTP 200
        st_read, body_read = _http_req(
            f"{bff_url}/api/v1/sessions/sess-nl-001/messages",
            method="POST",
            payload={
                "text": "What is my vacation policy in the Netherlands?",
                "now_epoch": 1780000206,
            },
        )
        assert st_read == 200
        assert any(
            e["event"] == "citation" and e["data"]["doc_id"] == "POL-NL-LEAVE-2026"
            for e in body_read["events"]
        )

        # Restore writes_enabled=True and verify confirmation succeeds with HTTP 200 COMPLETED
        st_on, body_on = _http_req(
            f"{admin_url}/admin/v1/kill-switch",
            method="POST",
            payload={
                "flag_name": "writes_enabled",
                "enabled": True,
                "reason": "POST_ROLLBACK_VERIFIED",
                "caller_sa": "hr-admin-sa",
                "now_epoch": 1780000210,
            },
        )
        assert st_on == 200 and body_on["writes_enabled"] is True
        st_write_ok, body_write_ok = _http_req(
            f"{bff_url}/api/v1/sessions/sess-nl-001/confirmations/conf-001",
            method="POST",
            payload={"decision": "confirm", "now_epoch": 1780000215},
        )
        assert st_write_ok == 200
        assert body_write_ok["status"] == "COMPLETED"

        audit_lines = [
            json.loads(line)
            for line in audit_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(
            e.get("event_type") == "KILL_SWITCH_CHANGED"
            and e.get("writes_enabled") is False
            for e in audit_lines
        )
        assert any(
            e.get("event_type") == "KILL_SWITCH_CHANGED"
            and e.get("writes_enabled") is True
            for e in audit_lines
        )
    finally:
        reset_shared_runtime_flags()
        bff_srv.shutdown()
        bff_srv.server_close()
        srv.shutdown()
        srv.server_close()


def test_tc_it_029_live_http_version_hot_reload_and_synthetic_prober(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-IT-029 (FR-001, FR-002, NFR-022, Runbook §6.2, §7.1, §8.3): Verify /version runtime state hot reload and live synthetic_prober HTTP check."""
    from hr_agent.tools.synthetic_prober import SAFE_OTEL_ENV

    for k, v in SAFE_OTEL_ENV.items():
        monkeypatch.setenv(k, v)
    rt_file = tmp_path / "runtime_state.json"
    rt_file.write_text(
        json.dumps(
            {
                "config_version": "v1.2.4",
                "image_digest": "sha256:1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
                "active_reasoning_engine": "hr-agent-blue-v1-2-4",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HR_AGENT_RUNTIME_STATE_FILE", str(rt_file))

    srv = create_bff_http_server(
        host="127.0.0.1", port=0, service=BffService(state_dir=str(tmp_path))
    )
    port = srv.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        st_r, r_body = _http_req(f"{base_url}/readyz")
        assert st_r == 200 and r_body["status"] == "ready"
        assert r_body["dependencies"]["firestore_flags"] == "ok"

        st_v1, v1 = _http_req(f"{base_url}/version")
        assert st_v1 == 200
        assert v1["config_version"] == "v1.2.4"
        assert v1["active_reasoning_engine"] == "hr-agent-blue-v1-2-4"

        # Cutover to Green v1.3.0
        rt_file.write_text(
            json.dumps(
                {
                    "config_version": "v1.3.0",
                    "image_digest": "sha256:4a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b",
                    "active_reasoning_engine": "hr-agent-green-v1-3-0",
                }
            ),
            encoding="utf-8",
        )
        st_v2, v2 = _http_req(f"{base_url}/version")
        assert st_v2 == 200
        assert v2["config_version"] == "v1.3.0"
        assert v2["active_reasoning_engine"] == "hr-agent-green-v1-3-0"

        # Live synthetic_prober and engine_drift_job CLI invocations
        assert (
            prober_main(
                [
                    "--env",
                    "local-staging",
                    "--url",
                    base_url,
                    "--assert-p95-first-content-ms",
                    "10000",
                    "--assert-probe-cutoff-ms",
                    "20000",
                    "--assert-zero-errors",
                ]
            )
            == 0
        )
        assert drift_main(["--env", "local-staging", "--state-dir", str(tmp_path)]) == 0
    finally:
        srv.shutdown()
        srv.server_close()


def test_tc_it_030_local_stack_and_hrctl_cli_contracts(tmp_path: Path) -> None:
    """TC-IT-030 (BH-06-1-03, RB-DEF-001, RB-DEF-002): Verify local_stack.py positional/flag args and hrctl.py HTTP --admin-url contract."""
    import importlib.util

    repo_root = Path(__file__).resolve().parent.parent.parent
    ls_spec = importlib.util.spec_from_file_location(
        "local_stack", repo_root / "scripts" / "local_stack.py"
    )
    assert ls_spec and ls_spec.loader
    ls_mod = importlib.util.module_from_spec(ls_spec)
    ls_spec.loader.exec_module(ls_mod)

    hrctl_spec = importlib.util.spec_from_file_location(
        "hrctl", repo_root / "scripts" / "hrctl.py"
    )
    assert hrctl_spec and hrctl_spec.loader
    hrctl_mod = importlib.util.module_from_spec(hrctl_spec)
    hrctl_spec.loader.exec_module(hrctl_mod)

    # Positional environment argument (`verify local-staging`) and flag (`--action verify --env local-prod`)
    assert (
        ls_mod.main(["verify", "local-staging", "--state-dir", str(tmp_path / "st")])
        == 0
    )
    assert (
        ls_mod.main(
            [
                "--action",
                "verify",
                "--env",
                "local-prod",
                "--state-dir",
                str(tmp_path / "pr"),
            ]
        )
        == 0
    )
    assert (
        ls_mod.main(
            [
                "set-traffic",
                "--env",
                "local-prod",
                "--green-pct",
                "100",
                "--state-dir",
                str(tmp_path / "pr"),
            ]
        )
        == 0
    )
    assert (
        ls_mod.main(
            [
                "rollback",
                "--env",
                "local-prod",
                "--prev-version",
                "v1.2.4",
                "--state-dir",
                str(tmp_path / "pr"),
            ]
        )
        == 0
    )

    admin = HrAdminService(state_dir=str(tmp_path / "admin"), reset_on_init=True)
    srv = create_ops_http_server(host="127.0.0.1", port=0, admin_service=admin)
    port = srv.server_address[1]
    admin_url = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert (
            hrctl_mod.main(
                [
                    "kill-switch",
                    "writes",
                    "off",
                    "--admin-url",
                    admin_url,
                    "--reason",
                    "TEST_DRAIN",
                ]
            )
            == 0
        )
        assert hrctl_mod.main(["status", "--admin-url", admin_url]) == 0
        assert (
            hrctl_mod.main(
                [
                    "kill-switch",
                    "writes",
                    "on",
                    "--admin-url",
                    admin_url,
                    "--reason",
                    "TEST_RESTORE",
                ]
            )
            == 0
        )
    finally:
        reset_shared_runtime_flags()
        srv.shutdown()
        srv.server_close()
