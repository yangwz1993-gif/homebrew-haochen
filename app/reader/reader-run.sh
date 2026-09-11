#!/bin/bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$APP_DIR/.venv/bin/python" -m reader.haochen_reader "$@"
