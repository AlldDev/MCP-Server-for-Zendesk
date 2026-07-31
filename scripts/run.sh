#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec uvicorn mcp_zendesk.server:app --host 0.0.0.0 --port 8080
