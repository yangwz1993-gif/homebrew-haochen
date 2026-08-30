#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export UV_PROJECT_ENVIRONMENT="$ROOT/app/.venv"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required: https://docs.astral.sh/uv/" >&2
  exit 1
}
command -v npm >/dev/null 2>&1 || {
  echo "Node.js/npm is required (see .node-version)." >&2
  exit 1
}
command -v bun >/dev/null 2>&1 || {
  echo "Bun is required (see .bun-version)." >&2
  exit 1
}

uv sync --frozen
npm --prefix pi-source ci --ignore-scripts

echo "Development environment is ready. Run: make check"
