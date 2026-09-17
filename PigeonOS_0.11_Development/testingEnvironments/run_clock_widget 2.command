#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$APP_DIR"

VENV_BIN="${APP_DIR}/.venv/bin"
if [[ ! -d "${APP_DIR}/.venv" ]]; then
  python3 -m venv "${APP_DIR}/.venv"
fi

PY="${VENV_BIN}/python3"
[[ -x "$PY" ]] || PY="${VENV_BIN}/python"
[[ -x "$PY" ]] || {
  echo "No usable Python in .venv. Install Python 3, then: python3 -m venv .venv" >&2
  exit 1
}

"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -r requirements.txt

exec "$PY" pigeon_widget_preview.py --widget clock "$@"
