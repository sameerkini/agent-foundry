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
# Python 3.13 skips .pth files carrying a filesystem hidden flag. Repair only
# this package's generated editable link, then verify a fresh interpreter import.
.venv/bin/python - <<'PY'
from pathlib import Path
import os
import stat
import subprocess
import sys
import sysconfig

link = Path(sysconfig.get_path('purelib')) / '_editable_impl_agent_foundry_local.pth'
if sys.platform == 'darwin' and link.exists():
    flags = link.stat().st_flags
    if flags & stat.UF_HIDDEN:
        os.chflags(link, flags & ~stat.UF_HIDDEN)
subprocess.run([sys.executable, '-c', 'import agent_foundry'], check=True)
PY
printf '\nReady. Run .venv/bin/foundry demo (no API calls).\n'
