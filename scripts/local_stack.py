#!/usr/bin/env python3
"""Cloudtop Local Stack Manager (03-deployment §2.1, Runbook §3.1, §5-§8, ADR-022).

Manages the 8 loopback-bound (127.0.0.1) HTTP services and 6 SQLite Firestore state
stores for `local-staging` (18080..18090) and `local-prod` (28080..28090), including
Blue/Green Reasoning Engine & BFF cutover and rollback rehearsals.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hr_agent.models import FirestoreDatabaseId, LocalSqliteStore
from hr_agent.ops import DeploymentTopologyValidator, HrAdminService
from hr_agent.tools.synthetic_prober import SAFE_OTEL_ENV

SERVICE_OFFSETS: list[tuple[str, int]] = [
    ("hr-chat-bff", 0),
    ("hr-chat-bff-green-cand", 1),
    ("hr-agent-coordinator-blue", 2),
    ("hr-agent-coordinator-green", 3),
    ("workweek-mcp", 4),
    ("servicedesk-mcp", 5),
    ("hr-admin", 6),
    ("policy-rag-mcp", 7),
    ("local-mock-saas-harness", 10),
]

DEFAULT_BLUE_DIGEST = (
    "sha256:1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff"
)
DEFAULT_GREEN_DIGEST = (
    "sha256:4a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b"
)


def build_service_port_map(env: str = "local-staging") -> list[dict[str, Any]]:
    """Build canonical loopback port map for 18080..18090 (local-staging) and 28080..28090 (local-prod)."""
    services = [
        {
            "service": name,
            "host": "127.0.0.1",
            "local_staging_port": 18080 + off,
            "local_prod_port": 28080 + off,
        }
        for name, off in SERVICE_OFFSETS
    ]
    for s in services:
        s["active_port"] = (
            s["local_staging_port"]
            if env in ("local-staging", "staging")
            else s["local_prod_port"]
        )
    return services


def _default_state_dir(env: str) -> Path:
    base = Path(os.environ.get("LOCAL_STATE_DIR", "/tmp/hr_agent_local_state"))
    sub = "staging" if "staging" in env else "prod"
    return base / sub


def _init_state_dirs(
    state_dir: Path,
    config_version: str,
    image_digest: str,
    active_engine: str,
    green_pct: int = 100,
) -> dict[str, Any]:
    fs_dir = state_dir / "firestore"
    audit_dir = state_dir / "audit"
    logs_dir = state_dir / "logs"
    pids_dir = state_dir / "pids"
    for d in (fs_dir, audit_dir, logs_dir, pids_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Initialize all 6 isolated SQLite stores
    for db_id in FirestoreDatabaseId:
        LocalSqliteStore(db_id, state_dir=str(fs_dir))
    HrAdminService(
        state_dir=str(fs_dir), reset_on_init=False
    ).seed_runtime_flags_if_absent(now_epoch=int(time.time()))

    audit_file = audit_dir / "hr-agent-audit.jsonl"
    if not audit_file.exists():
        audit_file.write_text("", encoding="utf-8")

    rt_state = {
        "config_version": config_version,
        "image_digest": image_digest,
        "active_reasoning_engine": active_engine,
        "green_pct": green_pct,
        "updated_at_epoch": int(time.time()),
    }
    (state_dir / "runtime_state.json").write_text(
        json.dumps(rt_state, indent=2, sort_keys=True), encoding="utf-8"
    )
    return rt_state


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def _serve_component(
    component: str, host: str, port: int, state_dir: str, rt_file: str
) -> int:
    os.environ.update(SAFE_OTEL_ENV)
    if state_dir:
        fs_dir = str(Path(state_dir) / "firestore")
        os.environ["HR_AGENT_SQLITE_DIR"] = fs_dir
        os.environ["HR_AGENT_MULTIPROCESS_STATE_DIR"] = fs_dir
        os.environ["HR_AGENT_AUDIT_LOG_PATH"] = str(
            Path(state_dir) / "audit" / "hr-agent-audit.jsonl"
        )
    if rt_file:
        os.environ["HR_AGENT_RUNTIME_STATE_FILE"] = rt_file

    if component in ("hr-chat-bff", "hr-chat-bff-green-cand"):
        from hr_agent.bff import create_http_server as create_bff

        srv = create_bff(host=host, port=port)
    elif component == "workweek-mcp":
        from hr_agent.workweek_mcp import create_http_server as create_ww

        srv = create_ww(host=host, port=port)
    elif component == "servicedesk-mcp":
        from hr_agent.servicedesk_mcp import create_http_server as create_sd

        srv = create_sd(host=host, port=port)
    elif component == "policy-rag-mcp":
        from hr_agent.policy_rag import create_http_server as create_rag

        srv = create_rag(host=host, port=port)
    elif component in ("hr-agent-coordinator-blue", "hr-agent-coordinator-green"):
        from hr_agent.orchestrator import create_http_server as create_orch

        srv = create_orch(host=host, port=port)
    elif component == "hr-admin":
        from hr_agent.ops import create_http_server as create_ops

        srv = create_ops(host=host, port=port)
    elif component == "local-mock-saas-harness":
        from hr_agent.upstream_client import create_http_server as create_up

        srv = create_up(host=host, port=port)
    else:
        return 1

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def _spawn_service(
    name: str,
    host: str,
    port: int,
    state_dir: Path,
    rt_file: Path,
    extra_env: dict[str, str] | None = None,
    force_restart: bool = False,
) -> int:
    pids_dir = state_dir / "pids"
    logs_dir = state_dir / "logs"
    pids_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pids_dir / f"{name}-{port}.pid"
    if _is_port_open(host, port) and pid_file.exists():
        if force_restart:
            try:
                os.kill(
                    int(pid_file.read_text(encoding="utf-8").strip()), signal.SIGTERM
                )
                time.sleep(0.15)
            except (ValueError, OSError):
                pass
            pid_file.unlink(missing_ok=True)
        else:
            try:
                existing_pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.kill(existing_pid, 0)
                return existing_pid
            except (ValueError, OSError):
                pass

    base_port = (
        18080 if 18080 <= port < 18100 else (28080 if 28080 <= port < 28100 else 18080)
    )
    env = dict(os.environ)
    env.update(SAFE_OTEL_ENV)
    env["PYTHONPATH"] = str(SRC_DIR)
    env["HR_AGENT_SQLITE_DIR"] = str(state_dir / "firestore")
    env["HR_AGENT_MULTIPROCESS_STATE_DIR"] = str(state_dir / "firestore")
    env["HR_AGENT_AUDIT_LOG_PATH"] = str(state_dir / "audit" / "hr-agent-audit.jsonl")
    env["HR_AGENT_RUNTIME_STATE_FILE"] = str(rt_file)
    env["HR_AGENT_UCT_SIGNING_KEY"] = "local-stack-uct-es256-seed-v1"
    env["HR_AGENT_CT_SIGNING_KEY"] = "local-stack-ct-es256-seed-v1"
    env["HR_AGENT_BLUE_URL"] = f"http://{host}:{base_port + 2}"
    env["HR_AGENT_GREEN_URL"] = f"http://{host}:{base_port + 3}"
    env["HR_ORCHESTRATOR_URL"] = f"http://{host}:{base_port + 3}"
    env["WORKWEEK_MCP_URL"] = f"http://{host}:{base_port + 4}"
    env["SERVICEDESK_MCP_URL"] = f"http://{host}:{base_port + 5}"
    env["POLICY_RAG_URL"] = f"http://{host}:{base_port + 7}"
    env["MOCK_SAAS_BASE_URL"] = f"http://{host}:{base_port + 10}"
    if extra_env:
        env.update(extra_env)

    log_fp = open(logs_dir / f"{name}.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve-component",
            name,
            "--bind-host",
            host,
            "--port",
            str(port),
            "--state-dir",
            str(state_dir),
            "--rt-file",
            str(rt_file),
        ],
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def _stop_all_daemons(state_dir: Path) -> list[int]:
    stopped: list[int] = []
    pids_dir = state_dir / "pids"
    if not pids_dir.exists():
        return stopped
    for pid_file in sorted(pids_dir.glob("*.pid")):
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except (ValueError, OSError):
            pass
        finally:
            pid_file.unlink(missing_ok=True)
    time.sleep(0.15)
    return stopped


def _wait_for_ports(
    host: str, ports: list[int], timeout_s: float = 5.0
) -> dict[int, bool]:
    deadline = time.time() + timeout_s
    status: dict[int, bool] = dict.fromkeys(ports, False)
    while time.time() < deadline:
        for p in ports:
            if not status[p] and _is_port_open(host, p):
                try:
                    with urllib.request.urlopen(
                        f"http://{host}:{p}/healthz", timeout=0.5
                    ) as resp:
                        if resp.status == 200:
                            status[p] = True
                except Exception:
                    pass
        if all(status.values()):
            break
        time.sleep(0.1)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cloudtop local stack manager (127.0.0.1 loopback only)"
    )
    parser.add_argument(
        "pos_action",
        nargs="?",
        default=None,
        help="Optional positional action (up, down, status, verify, ensure-blue, etc.)",
    )
    parser.add_argument(
        "pos_env",
        nargs="?",
        default=None,
        help="Optional positional environment (local-staging or local-prod)",
    )
    parser.add_argument("--action", default=None, help="Action flag")
    parser.add_argument("--env", default=None, help="Environment flag")
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--base-port", type=int, default=0)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--cand-port", type=int, default=28081)
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--rt-file", default="")
    parser.add_argument("--config-version", default="v1.3.0")
    parser.add_argument("--prev-version", default="v1.2.4")
    parser.add_argument("--image-digest", default=DEFAULT_GREEN_DIGEST)
    parser.add_argument("--workers-digest", default="")
    parser.add_argument("--green-engine", default="hr-agent-green-v1-3-0")
    parser.add_argument("--blue-engine", default="hr-agent-blue-v1-2-4")
    parser.add_argument("--green-pct", type=int, default=100)
    parser.add_argument("--serve-component", default="")
    args = parser.parse_args(argv)

    if args.serve_component:
        return _serve_component(
            args.serve_component,
            args.bind_host,
            args.port,
            args.state_dir,
            args.rt_file,
        )

    raw_action = (args.action or args.pos_action or "verify").strip().lower()
    raw_env = (args.env or args.pos_env or "local-staging").strip()
    if (
        raw_action in ("local-staging", "local-prod")
        and args.pos_env is None
        and args.action is None
    ):
        raw_env = raw_action
        raw_action = "verify"

    action_map = {"up": "start", "down": "stop"}
    action = action_map.get(raw_action, raw_action)
    base_port = args.base_port or (18080 if "staging" in raw_env else 28080)
    state_dir = Path(args.state_dir) if args.state_dir else _default_state_dir(raw_env)

    services = build_service_port_map(raw_env)
    validator = DeploymentTopologyValidator()
    binding_check = validator.verify_cloudtop_service_bindings(services=services)

    if action == "verify":
        output = {
            "action": action,
            "env": raw_env,
            "ok": binding_check["all_loopback_only"]
            and binding_check["zero_wildcard_bindings"],
            "binding_check": binding_check,
            "otel_env": SAFE_OTEL_ENV,
            "services": services,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0 if output["ok"] else 1

    if action in ("start", "ensure-blue"):
        is_blue = action == "ensure-blue"
        cfg_ver = (
            args.config_version
            if (action == "start" or args.config_version != "v1.3.0")
            else "v1.2.4"
        )
        img_dig = args.image_digest if not is_blue else DEFAULT_BLUE_DIGEST
        eng = args.blue_engine if is_blue else args.green_engine
        rt = _init_state_dirs(
            state_dir, cfg_ver, img_dig, eng, green_pct=0 if is_blue else 100
        )
        rt_file = state_dir / "runtime_state.json"
        pids: dict[str, int] = {}
        ports_to_wait: list[int] = []
        for svc_name, offset in SERVICE_OFFSETS:
            p = base_port + offset
            pids[svc_name] = _spawn_service(
                svc_name, args.bind_host, p, state_dir, rt_file
            )
            ports_to_wait.append(p)
        port_health = _wait_for_ports(args.bind_host, ports_to_wait)
        all_up = all(port_health.values())
        print(
            json.dumps(
                {
                    "action": action,
                    "env": raw_env,
                    "ok": all_up and binding_check["all_loopback_only"],
                    "state_dir": str(state_dir),
                    "runtime_state": rt,
                    "pids": pids,
                    "port_health": {str(k): v for k, v in port_health.items()},
                    "services": services,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if all_up else 1

    if action == "stop":
        stopped_pids = _stop_all_daemons(state_dir)
        print(
            json.dumps(
                {
                    "action": "stop",
                    "env": raw_env,
                    "ok": True,
                    "stopped_pids": stopped_pids,
                    "state_dir": str(state_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if action == "status":
        ports = [base_port + off for _, off in SERVICE_OFFSETS]
        port_health = _wait_for_ports(args.bind_host, ports, timeout_s=1.0)
        rt_file = state_dir / "runtime_state.json"
        rt = json.loads(rt_file.read_text(encoding="utf-8")) if rt_file.exists() else {}
        print(
            json.dumps(
                {
                    "action": "status",
                    "env": raw_env,
                    "ok": binding_check["all_loopback_only"],
                    "all_ports_listening": all(port_health.values()),
                    "port_health": {str(k): v for k, v in port_health.items()},
                    "runtime_state": rt,
                    "binding_check": binding_check,
                    "services": services,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if action == "deploy-mcp":
        ports = [base_port + 4, base_port + 5, base_port + 6, base_port + 7]
        port_health = _wait_for_ports(args.bind_host, ports, timeout_s=2.0)
        print(
            json.dumps(
                {
                    "action": "deploy-mcp",
                    "env": raw_env,
                    "ok": all(port_health.values()),
                    "image_digest": args.image_digest,
                    "workers_digest": args.workers_digest,
                    "mcp_ports": {str(k): v for k, v in port_health.items()},
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if all(port_health.values()) else 1

    if action == "start-green-agent":
        p = args.port or (base_port + 3)
        rt_file = state_dir / "runtime_state.json"
        _spawn_service(
            "hr-agent-coordinator-green", args.bind_host, p, state_dir, rt_file
        )
        port_health = _wait_for_ports(args.bind_host, [p], timeout_s=2.0)
        engine_id = (
            f"hr-agent-green-{args.config_version}"
            if args.config_version != "v1.3.0"
            else args.green_engine
        )
        print(engine_id)
        return 0 if port_health[p] else 1

    if action == "stage-bff-green":
        state_dir.mkdir(parents=True, exist_ok=True)
        cand_rt_file = state_dir / "runtime_state_green_cand.json"
        cand_rt = {
            "config_version": args.config_version,
            "image_digest": args.image_digest,
            "active_reasoning_engine": args.green_engine,
            "green_pct": 0,
            "candidate_revision": True,
        }
        cand_rt_file.write_text(
            json.dumps(cand_rt, indent=2, sort_keys=True), encoding="utf-8"
        )
        pid = _spawn_service(
            "hr-chat-bff-green-cand",
            args.bind_host,
            args.cand_port,
            state_dir,
            cand_rt_file,
            force_restart=True,
        )
        port_health = _wait_for_ports(args.bind_host, [args.cand_port], timeout_s=3.0)
        print(
            json.dumps(
                {
                    "action": "stage-bff-green",
                    "env": raw_env,
                    "ok": port_health[args.cand_port],
                    "cand_port": args.cand_port,
                    "pid": pid,
                    "candidate_state": cand_rt,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if port_health[args.cand_port] else 1

    if action == "set-traffic":
        state_dir.mkdir(parents=True, exist_ok=True)
        rt_file = state_dir / "runtime_state.json"
        cand_rt_file = state_dir / "runtime_state_green_cand.json"
        rt = json.loads(rt_file.read_text(encoding="utf-8")) if rt_file.exists() else {}
        cand = (
            json.loads(cand_rt_file.read_text(encoding="utf-8"))
            if cand_rt_file.exists()
            else {}
        )
        rt.update(
            {
                "config_version": cand.get("config_version", args.config_version)
                if args.green_pct > 0
                else "v1.2.4",
                "image_digest": cand.get("image_digest", args.image_digest)
                if args.green_pct > 0
                else DEFAULT_BLUE_DIGEST,
                "active_reasoning_engine": cand.get(
                    "active_reasoning_engine", args.green_engine
                )
                if args.green_pct > 0
                else args.blue_engine,
                "green_pct": args.green_pct,
                "blue_pct": 100 - args.green_pct,
                "updated_at_epoch": int(time.time()),
            }
        )
        rt_file.write_text(json.dumps(rt, indent=2, sort_keys=True), encoding="utf-8")
        print(
            json.dumps(
                {
                    "action": "set-traffic",
                    "env": raw_env,
                    "ok": True,
                    "port": args.port or base_port,
                    "traffic_state": rt,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if action == "rollback":
        state_dir.mkdir(parents=True, exist_ok=True)
        rt_file = state_dir / "runtime_state.json"
        rt = {
            "config_version": args.prev_version,
            "image_digest": DEFAULT_BLUE_DIGEST,
            "active_reasoning_engine": args.blue_engine,
            "green_pct": 0,
            "blue_pct": 100,
            "rolled_back_at_epoch": int(time.time()),
        }
        rt_file.write_text(json.dumps(rt, indent=2, sort_keys=True), encoding="utf-8")
        print(
            json.dumps(
                {
                    "action": "rollback",
                    "env": raw_env,
                    "ok": True,
                    "port": args.port or base_port,
                    "restored_state": rt,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if action == "logs":
        logs_dir = state_dir / "logs"
        logs_summary = (
            {
                p.name: p.read_text(encoding="utf-8")[-1000:]
                for p in sorted(logs_dir.glob("*.log"))
            }
            if logs_dir.exists()
            else {}
        )
        print(
            json.dumps(
                {"action": "logs", "env": raw_env, "ok": True, "logs": logs_summary},
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
