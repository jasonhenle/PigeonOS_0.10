#!/bin/bash
# PigeonOS 0.10 launcher for Linux / Raspberry Pi OS.
set -euo pipefail

INSTALLER_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${INSTALLER_DIR}/.." && pwd)"
cd "${ROOT}"
SYSTEM_DIR="${ROOT}/pigeonSystem"
VENV_REBUILD_REASON=""

if command -v flock >/dev/null 2>&1; then
  LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/pigeon-$(id -u).lock"
  exec 9>"${LOCK_FILE}"
  if ! flock -n 9; then
    echo "pigeon: another Pigeon instance is already running." >&2
    exit 0
  fi
fi

python_is_supported() {
  local py="${1:-}"
  [[ -n "${py}" && -x "${py}" ]] || return 1
  "${py}" -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1
}

pick_python_with_tk() {
  local candidate=""
  local resolved=""
  for candidate in \
    python3 python3.13 python3.12 python3.11 python3.10 \
    /usr/bin/python3 /usr/local/bin/python3; do
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

MAIN_PY="${SYSTEM_DIR}/pigeon_0_9.py"
if [[ ! -f "${MAIN_PY}" ]]; then
  echo "pigeon: missing pigeonSystem/pigeon_0_9.py — copy or pull the latest Pigeon build." >&2
  exit 1
fi

EXPECTED_VENV="${SYSTEM_DIR}/.venv"
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
    echo "pigeon: no Python 3.10+ with tkinter found." >&2
    echo "pigeon: on Raspberry Pi OS run: sudo apt install python3 python3-venv python3-tk" >&2
    echo "pigeon: or run installer/install_on_pi.sh from this folder." >&2
    exit 1
  fi
  [[ -n "${VENV_REBUILD_REASON}" ]] || VENV_REBUILD_REASON="missing-venv"
  "${BASE_PY}" -m venv "${SYSTEM_DIR}/.venv"
fi

PY="${VENV_BIN}/python3"
[[ -x "$PY" ]] || PY="${VENV_BIN}/python"
if [[ ! -x "$PY" ]]; then
  BASE_PY="$(pick_python_with_tk || true)"
  [[ -n "${BASE_PY}" ]] || {
    echo "pigeon: no Python 3.10+ with tkinter found." >&2
    echo "pigeon: on Raspberry Pi OS run: sudo apt install python3 python3-venv python3-tk" >&2
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
    echo "pigeon: on Raspberry Pi OS run: sudo apt install python3 python3-venv python3-tk" >&2
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

"$PY" -m pip install --upgrade pip >/dev/null
REQ_FILE="${SYSTEM_DIR}/requirements.txt"
if [[ "$(uname -s)" == "Linux" && -f "${SYSTEM_DIR}/requirements-pi.txt" ]]; then
  REQ_FILE="${SYSTEM_DIR}/requirements-pi.txt"
fi
"$PY" -m pip install -r "${REQ_FILE}"

if [[ "${1:-}" == "--bootstrap-only" ]]; then
  echo "pigeon: python environment ready." >&2
  exit 0
fi

if [[ -n "${VENV_REBUILD_REASON}" ]]; then
  echo "pigeon: python environment ready (${VENV_REBUILD_REASON})." >&2
fi
echo "Starting Pigeon…" >&2
export PYTHONPYCACHEPREFIX="${ROOT}/pigeonCashe"
FONT_DIR="${ROOT}/pigeonAssets/fonts"
if [[ -f "${FONT_DIR}/SharpSansExtrabold.otf" ]]; then
  export PIGEON_FONT_EXTRABOLD="${FONT_DIR}/SharpSansExtrabold.otf"
  export PIGEON_FONT="${PIGEON_FONT_EXTRABOLD}"
elif [[ -f "${FONT_DIR}/Sharp Sans Extrabold.otf" ]]; then
  export PIGEON_FONT_EXTRABOLD="${FONT_DIR}/Sharp Sans Extrabold.otf"
  export PIGEON_FONT="${PIGEON_FONT_EXTRABOLD}"
fi
if [[ -f "${FONT_DIR}/SharpSansMedium.otf" ]]; then
  export PIGEON_FONT_MEDIUM="${FONT_DIR}/SharpSansMedium.otf"
elif [[ -f "${FONT_DIR}/Sharp Sans Medium.otf" ]]; then
  export PIGEON_FONT_MEDIUM="${FONT_DIR}/Sharp Sans Medium.otf"
fi
USER_FONT_DIR="${HOME}/.local/share/fonts/pigeon"
# Add pigeon fonts without replacing the system fontconfig (Tk needs system fonts).
if [[ -d "${USER_FONT_DIR}" ]]; then
  export FONTCONFIG_PATH="${USER_FONT_DIR}${FONTCONFIG_PATH:+:${FONTCONFIG_PATH}}"
fi
# Only use a custom FONTCONFIG_FILE when it includes the system config.
if [[ -f "${USER_FONT_DIR}/fonts.conf" ]] \
  && grep -q 'include.*fonts\.conf' "${USER_FONT_DIR}/fonts.conf" 2>/dev/null; then
  export FONTCONFIG_FILE="${USER_FONT_DIR}/fonts.conf"
fi
# Pi / Linux: native 1280×800 UI target. Fullscreen fills matching displays
# and letterboxes/pillarboxes other aspect ratios without deforming the UI.
export PIGEON_DISPLAY_W="${PIGEON_DISPLAY_W:-1280}"
export PIGEON_DISPLAY_H="${PIGEON_DISPLAY_H:-800}"
export PIGEON_WINDOW_SCALE="${PIGEON_WINDOW_SCALE:-1.0}"
export PIGEON_PI_FULLSCREEN="${PIGEON_PI_FULLSCREEN:-1}"
export PIGEON_APPLE_TV_SCAN_TIMEOUT="${PIGEON_APPLE_TV_SCAN_TIMEOUT:-12}"

# Public repo: stray GitHub tokens in the environment break the in-app updater (latin-1).
unset PIGEON_UPDATE_GITHUB_TOKEN GITHUB_TOKEN GH_TOKEN GITHUB_PAT 2>/dev/null || true

STATE_DIR="${HOME}/.pigeon_0_6"
mkdir -p "${STATE_DIR}"
if [[ ! -f "${STATE_DIR}/tmdb_api_key" && ! -f "${STATE_DIR}/tmdb_read_token" ]]; then
  if [[ -f "${ROOT}/installer/setup/tmdb_api_key" ]]; then
    cp "${ROOT}/installer/setup/tmdb_api_key" "${STATE_DIR}/tmdb_api_key"
    chmod 600 "${STATE_DIR}/tmdb_api_key" 2>/dev/null || true
  elif [[ -f "${ROOT}/installer/setup/tmdb_read_token" ]]; then
    cp "${ROOT}/installer/setup/tmdb_read_token" "${STATE_DIR}/tmdb_read_token"
    chmod 600 "${STATE_DIR}/tmdb_read_token" 2>/dev/null || true
  fi
fi

exec "$PY" -u "${MAIN_PY}" "$@"
