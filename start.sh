#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PORT="${1:-${PORT:-8501}}"
HOST="${HOST:-0.0.0.0}"
PYTHON_BIN="${PYTHON_BIN:-python}"

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( 10#$PORT < 1 || 10#$PORT > 65535 )); then
    echo "Error: port must be an integer between 1 and 65535." >&2
    exit 2
fi

if ! "$PYTHON_BIN" -c "import streamlit" >/dev/null 2>&1; then
    echo "Error: Streamlit is not installed for $PYTHON_BIN." >&2
    echo "Install the project dependencies before running the app." >&2
    exit 1
fi

echo "Starting Fashion CMS Upload Generator"
echo "Local URL: http://localhost:${PORT}"

if [[ "${CODESPACES:-}" == "true" ]] \
    && [[ -n "${CODESPACE_NAME:-}" ]] \
    && [[ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
    echo "Codespaces URL: https://${CODESPACE_NAME}-${PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
fi

exec "$PYTHON_BIN" -m streamlit run app.py \
    --server.address "$HOST" \
    --server.port "$PORT" \
    --server.headless true \
    --browser.gatherUsageStats false
