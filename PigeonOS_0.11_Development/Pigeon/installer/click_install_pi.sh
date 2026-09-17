#!/bin/bash
# GUI / clickable installer for Raspberry Pi OS.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
cd "${ROOT}"

# shellcheck source=common.sh
source "${HERE}/common.sh"
VERSION="$(pigeon_version_string "${ROOT}")"
DESKTOP_USER="$(logname 2>/dev/null || id -un 2>/dev/null || echo pi)"
INSTALL_DIR="${PIGEON_INSTALL_DIR:-${ROOT}}"
LOG_FILE="${HOME}/pigeon-install.log"
TERMINAL_CHILD=0
[[ "${1:-}" == "--terminal-child" ]] && TERMINAL_CHILD=1

# zenity treats < > & as markup unless --no-markup; apt logs contain (>= 7.0) etc.
zenity_plain() {
  zenity --no-markup "$@"
}

log_tail_plain() {
  if [[ -f "${LOG_FILE}" ]]; then
    tail -n 12 "${LOG_FILE}" | tr -d '\r' | sed 's/[<>]/ /g'
  else
    echo "(no log file yet)"
  fi
}

show_error() {
  local msg="${1:-Install failed.}"
  local tail_text
  tail_text="$(log_tail_plain)"
  if command -v zenity >/dev/null 2>&1; then
    if [[ -f "${LOG_FILE}" ]]; then
      zenity_plain --text-info \
        --title="Pigeon install failed" \
        --width=620 --height=420 \
        --filename="${LOG_FILE}" \
        --text="Install did not complete. Log file:" 2>/dev/null \
        || zenity_plain --error \
          --title="Pigeon install failed" \
          --width=520 --height=320 \
          --text="${msg}

Last log lines:
${tail_text}

Full log:
${LOG_FILE}"
    else
      zenity_plain --error \
        --title="Pigeon install failed" \
        --width=480 \
        --text="${msg}"
    fi
  else
    printf '%s\n\nLast log lines:\n%s\n\nFull log: %s\n' "${msg}" "${tail_text}" "${LOG_FILE}" >&2
  fi
}

show_success() {
  if command -v zenity >/dev/null 2>&1; then
    zenity_plain --info \
      --title="Pigeon installed" \
      --width=440 \
      --text="Pigeon ${VERSION} is ready.

Folder:
${INSTALL_DIR}

Double-click Run-Pigeon on your Desktop to launch."
  fi
}

run_install_logged() {
  local rc=0
  {
    echo "=== Pigeon ${VERSION} install $(date) ==="
    echo "User: ${DESKTOP_USER}"
    echo "Folder: ${INSTALL_DIR}"
    echo
    if [[ "${EUID}" -eq 0 ]]; then
      PIGEON_USER="${DESKTOP_USER}" PIGEON_INSTALL_DIR="${INSTALL_DIR}" \
        bash "${HERE}/install_pigeon.sh" --in-place
    else
      sudo -E PIGEON_USER="${DESKTOP_USER}" PIGEON_INSTALL_DIR="${INSTALL_DIR}" \
        bash "${HERE}/install_pigeon.sh" --in-place
    fi
  } > >(tee "${LOG_FILE}") 2>&1
  rc=${PIPESTATUS[0]}
  return "${rc}"
}

# Child process inside lxterminal — install only, no dialogs, no new terminal.
if [[ "${TERMINAL_CHILD}" -eq 1 ]]; then
  echo "=== Pigeon ${VERSION} install $(date) ==="
  echo "User: ${DESKTOP_USER}"
  echo "Folder: ${INSTALL_DIR}"
  echo "Log: ${LOG_FILE}"
  echo
  if run_install_logged; then
    show_success
    exit 0
  fi
  echo
  echo "---- last log lines ----"
  log_tail_plain
  echo "------------------------"
  show_error "Install did not complete."
  exit 1
fi

# Parent: one confirmation, then open a single terminal window.
if command -v zenity >/dev/null 2>&1; then
  zenity_plain --question \
    --title="Install Pigeon" \
    --width=460 \
    --text="Install Pigeon ${VERSION} here:
${INSTALL_DIR}

A window will open and show progress.
This can take 5-15 minutes on a Raspberry Pi.

Continue?" \
    || exit 0
fi

if command -v lxterminal >/dev/null 2>&1; then
  exec lxterminal -t "Install Pigeon" --geometry=100x30 -e bash -lc \
    "bash '${HERE}/click_install_pi.sh' --terminal-child; rc=\$?; echo; if [[ \$rc -eq 0 ]]; then echo 'Install finished successfully.'; else echo 'Install failed. See ${LOG_FILE}'; fi; read -p 'Press Enter to close… '"
fi

if command -v x-terminal-emulator >/dev/null 2>&1; then
  exec x-terminal-emulator -e bash -lc \
    "bash '${HERE}/click_install_pi.sh' --terminal-child; rc=\$?; echo; if [[ \$rc -eq 0 ]]; then echo 'Done.'; else echo 'Failed. See ${LOG_FILE}'; fi; read -p 'Press Enter… '"
fi

if run_install_logged; then
  show_success
else
  show_error "Install did not complete."
  exit 1
fi
