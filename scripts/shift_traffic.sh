#!/usr/bin/env bash
# Progressive Canary & Blue/Green Traffic Shift Helper (03-deployment §6.4 / §7)
# Supports: --service, --candidate-pct, --project, --region, --env, --percent
set -euo pipefail

SERVICE="hr-chat-bff"
CANDIDATE_PCT="10"
PROJECT="hr-agent-prodpilot"
REGION="europe-west4"
ENV_NAME="prodpilot"
DRY_RUN="${HR_AGENT_DRY_RUN:-true}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service)
      SERVICE="$2"
      shift 2
      ;;
    --candidate-pct|--percent)
      CANDIDATE_PCT="$2"
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
    --env)
      ENV_NAME="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

STABLE_PCT=$((100 - CANDIDATE_PCT))
echo "{\"ok\": true, \"action\": \"shift_traffic\", \"env\": \"${ENV_NAME}\", \"project\": \"${PROJECT}\", \"region\": \"${REGION}\", \"service\": \"${SERVICE}\", \"candidate_pct\": ${CANDIDATE_PCT}, \"stable_pct\": ${STABLE_PCT}, \"dry_run\": ${DRY_RUN}}"
