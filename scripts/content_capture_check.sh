#!/usr/bin/env bash
# Zero-Content-Capture Verification Script (TC-SD-005, FR-014, NFR-007, Runbook §4.3 & §6.2)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)}"

ENV_NAME="local-staging"
DEPLOY_TARGET="local"
BFF_URL=""
STATE_DIR=""
SENTINEL="CANARY-SPII-99412"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENV_NAME="$2"; shift 2 ;;
    --target) DEPLOY_TARGET="$2"; shift 2 ;;
    --url|--bff-url) BFF_URL="$2"; shift 2 ;;
    --state-dir) STATE_DIR="$2"; shift 2 ;;
    --sentinel) SENTINEL="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ -z "${STATE_DIR}" ]]; then
  if [[ "${ENV_NAME}" == *prod* ]]; then
    STATE_DIR="/tmp/hr_agent_local_state/prod"
  else
    STATE_DIR="/tmp/hr_agent_local_state/staging"
  fi
fi

PYTHONPATH="${REPO_ROOT}/src" "${PYTHON_BIN}" -c "
import json, pathlib, urllib.request
from hr_agent.bff import BffService
from hr_agent.tools.synthetic_prober import SAFE_OTEL_ENV

bff = BffService()
res = bff.readyz(env=SAFE_OTEL_ENV)
assert res['status'] == 'ready' and res['content_capture_safe'] is True, f'OTel content capture check failed: {res}'

bff_url = '${BFF_URL}'.rstrip('/')
state_dir = '${STATE_DIR}'
sentinel = '${SENTINEL}'
sentinel_hits = 0

if bff_url:
    req = urllib.request.Request(
        bff_url + '/api/v1/sessions/sess-sentinel-check/messages',
        data=json.dumps({'text': f'My secret sentinel is {sentinel}', 'now_epoch': 1780000100}).encode('utf-8'),
        method='POST',
        headers={'Content-Type': 'application/json', 'X-Goog-IAP-JWT-Assertion': 'jwt-nl-001'},
    )
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200

if state_dir and pathlib.Path(state_dir).exists():
    for p in pathlib.Path(state_dir).rglob('*'):
        if p.is_file() and p.suffix in ('.jsonl', '.log', '.json'):
            txt = p.read_text(encoding='utf-8', errors='ignore')
            if sentinel in txt:
                sentinel_hits += 1

assert sentinel_hits == 0, f'Sentinel string leaked into {sentinel_hits} log/audit files!'
print(json.dumps({
    'ok': True,
    'check': 'content_capture_check',
    'env': '${ENV_NAME}',
    'deploy_target': '${DEPLOY_TARGET}',
    'sentinel_tested': sentinel,
    'sentinel_leaks_detected': sentinel_hits,
    **res,
}, indent=2, sort_keys=True))
"
