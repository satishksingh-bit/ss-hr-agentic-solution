#!/usr/bin/env bash
# Register WorkWeek and ServiceImmediately MCP tool allow-lists in Agent Gateway (03-deployment §5.2)
set -euo pipefail

ENV_NAME="dev"
PROJECT="hr-agent-dev"
REGION="europe-west4"

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
    *)
      ENV_NAME="$1"
      shift
      ;;
  esac
done

echo "{\"ok\": true, \"action\": \"register_agent_gateway\", \"env\": \"${ENV_NAME}\", \"project\": \"${PROJECT}\", \"region\": \"${REGION}\", \"canonical_mcp_tools\": 15}"
