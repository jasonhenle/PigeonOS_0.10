#!/bin/bash
# PigeonOS 0.10 launcher for macOS.
set -euo pipefail

INSTALLER_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${INSTALLER_DIR}/.." && pwd)"
cd "${ROOT}"
SYSTEM_DIR="${ROOT}/pigeonSystem"
EXPECTED_VENV="${SYSTEM_DIR}/.venv"
VENV_REBUILD_REASON=""

python_is_supported() {
  local py="${1:-}"
  [[ -n "${py}" && -x "${py}" ]] || return 1
  "${py}" -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1
}

python_pip_works() {
  local py="${1:-}"
  [[ -n "${py}" && -x "${py}" ]] || return 1
  "${py}" -m pip --version >/dev/null 2>&1
}

pick_python_with_tk() {
  local candidate=""
  local resolved=""
  # Prefer 3.12/3.11 over bare ``python3`` (often 3.14 on newer Macs).
  for candidate in \
    python3.12 python3.11 python3.13 python3.10 \
    /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.10 \
    /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3.13 /usr/local/bin/python3.10 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 \
    python3 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3; do
    if [[ "${candidate}" == /* ]]; then
      [[ -x "${candidate}" ]] || continue
      resolved="${candidate}"
    else
      command -v "${candidate}" >/dev/null 2>&1 || continue
      resolved="$(command -v "${candidate}")"
    fi
    if python_is_supported "${resolved}"; then
      "${resolved}" -c "import sys; print(sys.executable)"
      return 0
    fi
  done
  return 1
}

rebuild_venv() {
  local reason="${1:-rebuild}"
  local base_py=""
  base_py="$(pick_python_with_tk || true)"
  if [[ -z "${base_py}" ]]; then
    echo "pigeon: no Python 3.10+ with tkinter support found on this Mac." >&2
    echo "pigeon: install a Python.org 3.12/3.11 build (includes Tk), then re-run launcher." >&2
    echo "pigeon: download https://www.python.org/downloads/macos/" >&2
    exit 1
  fi
  echo "pigeon: rebuilding python environment (${reason})…" >&2
  VENV_REBUILD_REASON="${reason}"
  rm -rf "${EXPECTED_VENV}"
  "${base_py}" -m venv "${EXPECTED_VENV}"
}

ensure_venv_pip() {
  local py="${1:-}"
  if python_pip_works "${py}"; then
    return 0
  fi
  echo "pigeon: pip missing in .venv — trying ensurepip…" >&2
  if "${py}" -m ensurepip --upgrade >/dev/null 2>&1 && python_pip_works "${py}"; then
    return 0
  fi
  rebuild_venv "broken-pip"
  py="${VENV_BIN}/python3"
  [[ -x "$py" ]] || py="${VENV_BIN}/python"
  if ! python_pip_works "${py}"; then
    echo "pigeon: could not restore pip in .venv." >&2
    exit 1
  fi
  return 0
}

MAIN_PY="${SYSTEM_DIR}/pigeon_0_9.py"
if [[ ! -f "${MAIN_PY}" ]]; then
  echo "pigeon: missing pigeonSystem/pigeon_0_9.py — copy or pull the latest Pigeon_python folder." >&2
  exit 1
fi

# If the project folder was moved/copied, an old .venv still points pip/scripts at the
# previous path (pyvenv.cfg "command = ... -m venv <path>"). Rebuild in that case.
if [[ -f "${EXPECTED_VENV}/pyvenv.cfg" ]]; then
  VENV_FROM_CFG="$(grep '^command = ' "${EXPECTED_VENV}/pyvenv.cfg" 2>/dev/null | sed -n 's/.* -m venv //p' | tr -d '\r' || true)"
  if [[ -n "${VENV_FROM_CFG}" && "${VENV_FROM_CFG}" != "${EXPECTED_VENV}" ]]; then
    echo "pigeon: detected venv path mismatch for this machine. Rebuilding..." >&2
    VENV_REBUILD_REASON="path-mismatch"
    rm -rf "${EXPECTED_VENV}"
  fi
fi

VENV_BIN="${SYSTEM_DIR}/.venv/bin"

if [[ ! -d "${SYSTEM_DIR}/.venv" ]]; then
  BASE_PY="$(pick_python_with_tk || true)"
  if [[ -z "${BASE_PY}" ]]; then
    echo "pigeon: no Python 3.10+ with tkinter support found on this Mac." >&2
    echo "pigeon: install a Python.org 3.12/3.11 build (includes Tk), then re-run launcher." >&2
    echo "pigeon: download https://www.python.org/downloads/macos/" >&2
    exit 1
  fi
  [[ -n "${VENV_REBUILD_REASON}" ]] || VENV_REBUILD_REASON="missing-venv"
  "${BASE_PY}" -m venv "${SYSTEM_DIR}/.venv"
fi

# Use the venv interpreter directly — `python` is often missing on macOS outside the venv.
PY="${VENV_BIN}/python3"
[[ -x "$PY" ]] || PY="${VENV_BIN}/python"
if [[ ! -x "$PY" ]]; then
  BASE_PY="$(pick_python_with_tk || true)"
  [[ -n "${BASE_PY}" ]] || {
    echo "pigeon: no Python 3.10+ with tkinter support found on this Mac." >&2
    echo "pigeon: install a Python.org 3.12/3.11 build (includes Tk), then re-run launcher." >&2
    echo "pigeon: download https://www.python.org/downloads/macos/" >&2
    exit 1
  }
  echo "pigeon: venv interpreter is broken on this machine. Rebuilding..." >&2
  VENV_REBUILD_REASON="broken-interpreter"
  rm -rf "${SYSTEM_DIR}/.venv"
  "${BASE_PY}" -m venv "${SYSTEM_DIR}/.venv"
  PY="${VENV_BIN}/python3"
  [[ -x "$PY" ]] || PY="${VENV_BIN}/python"
fi

if ! python_is_supported "$PY"; then
  BASE_PY="$(pick_python_with_tk || true)"
  [[ -n "${BASE_PY}" ]] || {
    echo "pigeon: current venv python is not usable (needs Python 3.10+ with tkinter)." >&2
    echo "pigeon: install a Python.org 3.12/3.11 build (includes Tk), then re-run launcher." >&2
    echo "pigeon: download https://www.python.org/downloads/macos/" >&2
    exit 1
  }
  echo "pigeon: venv python is missing tkinter or too old. Rebuilding..." >&2
  VENV_REBUILD_REASON="unsupported-venv-python"
  rm -rf "${SYSTEM_DIR}/.venv"
  "${BASE_PY}" -m venv "${SYSTEM_DIR}/.venv"
  PY="${VENV_BIN}/python3"
  [[ -x "$PY" ]] || PY="${VENV_BIN}/python"
fi
[[ -x "$PY" ]] || {
  echo "No usable Python in .venv. Install Python 3, then: python3 -m venv .venv" >&2
  exit 1
}

ensure_venv_pip "$PY"
PY="${VENV_BIN}/python3"
[[ -x "$PY" ]] || PY="${VENV_BIN}/python"

# Fast path: if core deps already import, skip network pip (avoids lock hangs when
# another launcher/pip is already running — common “Pigeon not loading” on Mac).
deps_ok=0
if "$PY" -c "import cv2, PIL, numpy, pyatv, numbers_parser, fitz, serial, pytesseract" >/dev/null 2>&1; then
  deps_ok=1
fi

if [[ "${deps_ok}" -ne 1 ]]; then
  if ! "$PY" -m pip install --upgrade pip >/dev/null 2>&1; then
    rebuild_venv "pip-install-failed"
    PY="${VENV_BIN}/python3"
    [[ -x "$PY" ]] || PY="${VENV_BIN}/python"
    ensure_venv_pip "$PY"
    "$PY" -m pip install --upgrade pip >/dev/null
  fi
  echo "pigeon: installing Python dependencies…" >&2
  if ! "$PY" -m pip install -r "${SYSTEM_DIR}/requirements.txt"; then
    echo "pigeon: pip install failed; see messages above." >&2
    exit 1
  fi
else
  echo "pigeon: dependencies already present — skipping pip." >&2
fi

if [[ -n "${VENV_REBUILD_REASON}" ]]; then
  echo "pigeon: python environment ready (${VENV_REBUILD_REASON})." >&2
fi
echo "Starting Pigeon…" >&2
# -u: unbuffered stderr so startup lines (e.g. pigeon: running script) appear immediately.
export PYTHONPYCACHEPREFIX="${ROOT}/pigeonCashe"
exec "$PY" -u "${MAIN_PY}" "$@"
