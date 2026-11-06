#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
if command -v uv >/dev/null 2>&1; then
  UV=uv
else
  "$PYTHON" -m venv .bootstrap
  .bootstrap/bin/python -m pip install 'uv==0.12.13'
  UV=.bootstrap/bin/uv
fi
"$UV" sync --locked --python "$PYTHON"
printf '\nReady. Run .venv/bin/foundry demo (no API calls).\n'
