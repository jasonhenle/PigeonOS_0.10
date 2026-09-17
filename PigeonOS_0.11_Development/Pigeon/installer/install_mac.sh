#!/bin/bash
# Install Pigeon on macOS: copy to ~/Applications, bootstrap venv, add Desktop shortcut.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIGEON_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

PIGEON_VERSION="$(pigeon_version_string "${PIGEON_ROOT}")"
INSTALL_DIR="${PIGEON_INSTALL_DIR:-${HOME}/Applications/Pigeon_${PIGEON_VERSION}}"
IN_PLACE=0
MAKE_SHORTCUT=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    --in-place)
      IN_PLACE=1
      shift
      ;;
    --no-shortcut)
      MAKE_SHORTCUT=0
      shift
      ;;
    -h|--help)
      pigeon_print_usage "${PIGEON_ROOT}"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      pigeon_print_usage "${PIGEON_ROOT}" >&2
      exit 1
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "install_mac.sh is for macOS only. Use ./installer/install_pigeon.sh on Linux." >&2
  exit 1
fi

VERSION="$(pigeon_version_string "${PIGEON_ROOT}")"
echo "==> Pigeon ${VERSION} installer (macOS)"

python_is_supported() {
  local py="${1:-}"
  [[ -n "${py}" && -x "${py}" ]] || return 1
  "${py}" -c "import sys, tkinter; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1
}

pick_python_with_tk() {
  local candidate="" resolved=""
  for candidate in \
    python3 python3.13 python3.12 python3.11 python3.10 \
    /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 \
    /usr/local/bin/python3 /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3.10 \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.10/bin/python3; do
    if [[ "${candidate}" == /* ]]; then
      [[ -x "${candidate}" ]] || continue
      resolved="${candidate}"
    else
      command -v "${candidate}" >/dev/null 2>&1 || continue
      resolved="$(command -v "${candidate}")"
    fi
    if python_is_supported "${resolved}"; then
      echo "${resolved}"
      return 0
    fi
  done
  return 1
}

BASE_PY="$(pick_python_with_tk || true)"
if [[ -z "${BASE_PY}" ]]; then
  echo "pigeon: no Python 3.10+ with tkinter found." >&2
  echo "pigeon: install Python from https://www.python.org/downloads/macos/ (includes Tk)." >&2
  echo "pigeon: Python 3.12 or 3.11 is recommended." >&2
  exit 1
fi
echo "==> Using Python: ${BASE_PY}"

if [[ "${IN_PLACE}" -eq 1 ]]; then
  INSTALL_DIR="${PIGEON_ROOT}"
  echo "==> Installing in place at ${INSTALL_DIR}"
else
  echo "==> Copying Pigeon to ${INSTALL_DIR}"
  mkdir -p "$(dirname "${INSTALL_DIR}")"
  pigeon_rsync_tree "${PIGEON_ROOT}" "${INSTALL_DIR}"
fi

pigeon_prepare_runtime_dirs "${INSTALL_DIR}"
chmod +x "${INSTALL_DIR}/installer/run_pigeon_0_10.command" "${INSTALL_DIR}/installer/run_pigeon_0_10.sh" 2>/dev/null || true
chmod +x "${INSTALL_DIR}/installer/install_pigeon.sh" "${INSTALL_DIR}/installer/install_pigeon.command" 2>/dev/null || true

echo "==> Creating Python environment and installing dependencies (first run may take a minute)…"
bash "${INSTALL_DIR}/installer/run_pigeon_0_10.command" --help >/dev/null

if [[ "${MAKE_SHORTCUT}" -eq 1 ]]; then
  SHORTCUT="${HOME}/Desktop/Pigeon.command"
  cat > "${SHORTCUT}" <<EOF
#!/bin/bash
exec "${INSTALL_DIR}/installer/run_pigeon_0_10.command"
EOF
  chmod +x "${SHORTCUT}"
  echo "==> Desktop shortcut: ${SHORTCUT}"
fi

echo ""
echo "Pigeon ${VERSION} is installed."
echo ""
echo "  Install folder: ${INSTALL_DIR}"
echo "  Launch:         ${INSTALL_DIR}/installer/run_pigeon_0_10.command"
if [[ "${MAKE_SHORTCUT}" -eq 1 ]]; then
  echo "  Or double-click: ~/Desktop/Pigeon.command"
fi
echo ""
echo "Settings and credentials: ~/.pigeon_0_6"
echo ""
