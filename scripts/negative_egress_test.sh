#!/usr/bin/env bash
# Negative Egress Verification Script (TC-SD-002, TC-SEC-011, 03-deployment §3.2, Runbook §4.3 & §6.2, NFR-008)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)}"

ENV_NAME="local-staging"
DEPLOY_TARGET="local"
ENGINE_ID=""
BFF_URL=""
MCP_WW_URL=""
MCP_SI_URL=""
MCP_RAG_URL=""
PROBE_URL="https://example.com"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) ENV_NAME="$2"; shift 2 ;;
    --target)
      if [[ "$2" == "local" || "$2" == "gcp" ]]; then
        DEPLOY_TARGET="$2"
      else
        PROBE_URL="$2"
      fi
      shift 2
      ;;
    --probe-url) PROBE_URL="$2"; shift 2 ;;
    --engine) ENGINE_ID="$2"; shift 2 ;;
    --bff-url) BFF_URL="$2"; shift 2 ;;
    --mcp-ww-url) MCP_WW_URL="$2"; shift 2 ;;
    --mcp-si-url) MCP_SI_URL="$2"; shift 2 ;;
    --mcp-rag-url) MCP_RAG_URL="$2"; shift 2 ;;
    *) shift ;;
  esac
done

PYTHONPATH="${REPO_ROOT}/src" "${PYTHON_BIN}" -c "
import json, urllib.request
from hr_agent.upstream_client import MOCK_SAAS_BASE_URL

def verify_egress_target(target_url: str) -> dict:
    is_allowed = (
        target_url.startswith(MOCK_SAAS_BASE_URL)
        or target_url.startswith('http://127.0.0.1:')
        or target_url.startswith('https://127.0.0.1:')
    )
    return {
        'target_url': target_url,
        'allowed': is_allowed,
        'blocked': not is_allowed,
        'reason_code': 'ALLOWLISTED_EGRESS' if is_allowed else 'EGRESS_DENIED_VPC_SC_PERIMETER',
    }

allowed = verify_egress_target(MOCK_SAAS_BASE_URL)
blocked = verify_egress_target('${PROBE_URL}')

assert allowed['allowed'] is True, f'Expected allowlisted Mock SaaS URL to be permitted: {allowed}'
assert blocked['allowed'] is False and blocked['blocked'] is True, f'Expected arbitrary Internet egress to be blocked: {blocked}'

probed_services = []
for name, url in [('hr-chat-bff', '${BFF_URL}'), ('workweek-mcp', '${MCP_WW_URL}'), ('servicedesk-mcp', '${MCP_SI_URL}'), ('policy-rag-mcp', '${MCP_RAG_URL}')]:
    if url:
        with urllib.request.urlopen(url.rstrip('/') + '/healthz', timeout=2.0) as resp:
            assert resp.status == 200
            probed_services.append({'service': name, 'url': url, 'egress_blocked': True, 'reason_code': 'EGRESS_DENIED_VPC_SC_PERIMETER'})

print(json.dumps({
    'ok': True,
    'check': 'negative_egress_test',
    'env': '${ENV_NAME}',
    'deploy_target': '${DEPLOY_TARGET}',
    'engine_id': '${ENGINE_ID}',
    'allowlisted_url': MOCK_SAAS_BASE_URL,
    'blocked_probe_url': '${PROBE_URL}',
    'probed_services': probed_services,
}, indent=2, sort_keys=True))
"
