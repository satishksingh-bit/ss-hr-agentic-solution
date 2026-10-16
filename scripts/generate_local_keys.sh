#!/usr/bin/env bash
# Generate local ephemeral ES256 and HMAC keys for Cloudtop development (03-deployment §2.1)
set -euo pipefail

KEY_DIR="${1:-.local_state/keys}"
mkdir -p "${KEY_DIR}"
chmod 700 "${KEY_DIR}"
echo "[generate_local_keys] Initialized ephemeral key directory at ${KEY_DIR}"
