#!/bin/bash
# One-time setup on an existing Pi: passwordless sudo for in-app update restarts.
# Run: sudo bash installer/configure_pigeon_restart_sudo.sh
set -euo pipefail

INSTALLER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=common.sh
source "${INSTALLER_DIR}/common.sh"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This script is for Linux / Raspberry Pi only." >&2
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Re-running with sudo…" >&2
  exec sudo -E bash "$0" "$@"
fi

INSTALL_USER="${PIGEON_USER:-${SUDO_USER:-$(logname 2>/dev/null || echo "${USER:-pi}")}}"
pigeon_install_systemd_restart_sudoers "${INSTALL_USER}"
