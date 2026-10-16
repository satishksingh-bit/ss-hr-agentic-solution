#!/usr/bin/env bash
# Cloudtop Loopback Stack Wrapper (03-deployment §2.1, ADR-022)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)}"

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/local_stack.py" "$@"
