#!/bin/bash
# Pigeon installer — detects macOS vs Linux / Raspberry Pi and runs the right steps.
set -euo pipefail

INSTALLER_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${INSTALLER_DIR}/.." && pwd)"

show_help() {
  # shellcheck source=common.sh
  source "${INSTALLER_DIR}/common.sh"
  pigeon_print_usage "${ROOT}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

case "$(uname -s)" in
  Darwin)
    exec bash "${INSTALLER_DIR}/install_mac.sh" "$@"
    ;;
  Linux)
    if [[ -f "${INSTALLER_DIR}/install_on_pi.sh" ]]; then
      exec bash "${INSTALLER_DIR}/install_on_pi.sh" "$@"
    fi
    echo "pigeon: Linux installer not found in this build." >&2
    exit 1
    ;;
  *)
    echo "pigeon: unsupported platform: $(uname -s)" >&2
    exit 1
    ;;
esac
