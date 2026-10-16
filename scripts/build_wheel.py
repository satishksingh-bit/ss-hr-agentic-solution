#!/usr/bin/env python3
"""Deterministic offline-safe PEP 427 wheel builder for HR Agent (Runbook Step 3.2)."""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def build_wheel(out_dir: Path, version: str = "0.1.0") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    wheel_name = f"hr_agent-{version}-py3-none-any.whl"
    wheel_path = out_dir / wheel_name
    dist_info = f"hr_agent-{version}.dist-info"

    src_pkg = REPO_ROOT / "src" / "hr_agent"
    records: list[str] = []

    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(src_pkg.rglob("*.py")):
            rel_arc = f"hr_agent/{file_path.relative_to(src_pkg).as_posix()}"
            content = file_path.read_bytes()
            zi = zipfile.ZipInfo(rel_arc, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, content)
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(content).digest())
                .decode("ascii")
                .rstrip("=")
            )
            records.append(f"{rel_arc},sha256={digest},{len(content)}")

        metadata = (
            "Metadata-Version: 2.1\n"
            "Name: hr-agentic-solution\n"
            f"Version: {version}\n"
            "Summary: HR Agentic Solution (MVP 1)\n"
            "Requires-Python: >=3.12\n"
        ).encode()
        wheel_meta = (
            b"Wheel-Version: 1.0\n"
            b"Generator: hr-agent-offline-wheel-builder\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        )

        for arc_name, payload in (
            (f"{dist_info}/METADATA", metadata),
            (f"{dist_info}/WHEEL", wheel_meta),
        ):
            zi = zipfile.ZipInfo(arc_name, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, payload)
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                .decode("ascii")
                .rstrip("=")
            )
            records.append(f"{arc_name},sha256={digest},{len(payload)}")

        record_arc = f"{dist_info}/RECORD"
        records.append(f"{record_arc},,")
        record_bytes = ("\n".join(records) + "\n").encode("utf-8")
        zi = zipfile.ZipInfo(record_arc, date_time=(2026, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(zi, record_bytes)

    return wheel_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic PEP 427 wheel for HR Agent"
    )
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "dist"))
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args(argv)
    wheel_path = build_wheel(Path(args.out_dir), version=args.version)
    print(str(wheel_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
