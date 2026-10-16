#!/usr/bin/env python3
"""Operator CLI (hrctl) for toggling runtime_flags/global kill switches (C20, SDD §5.8, NFR-045, Runbook §8)."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hr_agent.ops import HrAdminService, get_shared_runtime_flags


def _http_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        return dict(json.loads(resp.read().decode("utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="hrctl — HR Agent Runtime Kill-Switch CLI"
    )
    parser.add_argument("--version", action="version", version="hrctl 0.1.0")
    parser.add_argument(
        "pos_args",
        nargs="*",
        help="Optional positional command: status | kill-switch <writes|assistant> <on|off>",
    )
    parser.add_argument(
        "--action",
        choices=(
            "status",
            "disable-writes",
            "enable-writes",
            "disable-assistant",
            "enable-assistant",
        ),
        default=None,
    )
    parser.add_argument(
        "--admin-url",
        default="",
        help="Optional HTTP URL of hr-admin service (e.g. http://127.0.0.1:28086)",
    )
    parser.add_argument("--reason", default="operator_cli_action")
    parser.add_argument(
        "--caller-sa", default="hr-admin-sa@hr-agent-prodpilot.iam.gserviceaccount.com"
    )
    args = parser.parse_args(argv)

    action = args.action
    if not action and args.pos_args:
        cmd = args.pos_args[0].lower()
        if cmd == "status":
            action = "status"
        elif cmd == "kill-switch" and len(args.pos_args) >= 3:
            target = args.pos_args[1].lower()
            state = args.pos_args[2].lower()
            if target in ("writes", "writes_enabled"):
                action = (
                    "enable-writes"
                    if state in ("on", "true", "enable")
                    else "disable-writes"
                )
            elif target in ("assistant", "assistant_enabled"):
                action = (
                    "enable-assistant"
                    if state in ("on", "true", "enable")
                    else "disable-assistant"
                )
    action = action or "status"

    if args.admin_url:
        base = args.admin_url.rstrip("/")
        if action == "status":
            res = _http_json(f"{base}/admin/v1/status", method="GET")
            print(json.dumps(res, indent=2, sort_keys=True))
            return 0
        flag_name = "writes_enabled" if "writes" in action else "assistant_enabled"
        enabled = action.startswith("enable-")
        res = _http_json(
            f"{base}/admin/v1/kill-switch",
            method="POST",
            payload={
                "flag_name": flag_name,
                "enabled": enabled,
                "reason": args.reason,
                "caller_sa": args.caller_sa,
                "now_epoch": int(time.time()),
            },
        )
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0 if res.get("ok") else 1

    admin = HrAdminService(reset_on_init=False)
    now_epoch = int(time.time())

    if action == "status":
        flags = get_shared_runtime_flags()
        print(
            json.dumps(
                {"ok": True, "action": "status", **flags}, indent=2, sort_keys=True
            )
        )
        return 0

    flag_name = "writes_enabled" if "writes" in action else "assistant_enabled"
    enabled = action.startswith("enable-")
    res = admin.set_kill_switch(
        flag_name=flag_name,
        enabled=enabled,
        reason=args.reason,
        caller_sa=args.caller_sa,
        now_epoch=now_epoch,
    )
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
