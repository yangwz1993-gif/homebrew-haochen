#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export UV_PROJECT_ENVIRONMENT="$ROOT/app/.venv"

printf '\n==> toolchain versions\n'
expected_node="v$(tr -d '[:space:]' < .node-version)"
expected_bun="$(tr -d '[:space:]' < .bun-version)"
actual_node="$(node --version)"
actual_bun="$(bun --version)"
[ "$actual_node" = "$expected_node" ] || { echo "Node $actual_node != $expected_node" >&2; exit 1; }
[ "$actual_bun" = "$expected_bun" ] || { echo "Bun $actual_bun != $expected_bun" >&2; exit 1; }
echo "Node $actual_node; Bun $actual_bun"

printf '\n==> version consistency\n'
uv run python scripts/version.py check

printf '\n==> secret scan\n'
uv run python scripts/check_secrets.py

printf '\n==> Python lint\n'
uv run ruff check app scripts tests

printf '\n==> Python type check\n'
uv run pyright

printf '\n==> shellcheck\n'
uv run shellcheck \
  engine/build.sh \
  packaging/build.sh \
  packaging/notarize.sh \
  tests/run_all_regressions.sh \
  scripts/bootstrap.sh \
  scripts/quality.sh

printf '\n==> unit tests + coverage report\n'
QT_QPA_PLATFORM=offscreen uv run pytest \
  --cov --cov-report=term-missing --cov-report=xml

printf '\nAll quality checks passed.\n'
