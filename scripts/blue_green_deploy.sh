#!/usr/bin/env bash
# Blue/Green Reasoning Engine & Canary Cloud Run Deployment Orchestrator (03-deployment §6.4 / §7)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)}"

ENV_NAME="prodpilot"
PROJECT="hr-agent-prodpilot"
REGION="europe-west4"
SLOT="green"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENV_NAME="$2"
      shift 2
      ;;
    --project)
      PROJECT="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --slot)
      SLOT="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

"${PYTHON_BIN}" "${SCRIPT_DIR}/deploy_reasoning_engine.py" --env "${ENV_NAME}" --project "${PROJECT}" --region "${REGION}" --slot "${SLOT}"
bash "${SCRIPT_DIR}/shift_traffic.sh" --env "${ENV_NAME}" --project "${PROJECT}" --region "${REGION}" --service "hr-chat-bff" --candidate-pct 10
bash "${SCRIPT_DIR}/shift_traffic.sh" --env "${ENV_NAME}" --project "${PROJECT}" --region "${REGION}" --service "hr-chat-bff" --candidate-pct 50
bash "${SCRIPT_DIR}/shift_traffic.sh" --env "${ENV_NAME}" --project "${PROJECT}" --region "${REGION}" --service "hr-chat-bff" --candidate-pct 100
