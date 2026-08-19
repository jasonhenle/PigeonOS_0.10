#!/bin/bash
# Double-click launcher for Pigeon on Raspberry Pi OS.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
# shellcheck source=common.sh
source "${HERE}/common.sh"
INSTALL_DIR="${PIGEON_INSTALL_DIR:-${HOME}/$(pigeon_install_dir_basename "${ROOT}")}"

if [[ -x "${INSTALL_DIR}/installer/run_pigeon_0_10.sh" ]]; then
  cd "${INSTALL_DIR}"
  exec ./installer/run_pigeon_0_10.sh
fi

if [[ -x "${HERE}/run_pigeon_0_10.sh" ]]; then
  cd "${HERE}"
  exec ./run_pigeon_0_10.sh
fi

if [[ -x "${ROOT}/run_pigeon_0_10.sh" ]]; then
  cd "${ROOT}"
  exec ./run_pigeon_0_10.sh
fi

if command -v zenity >/dev/null 2>&1; then
  zenity --no-markup --error --title="Pigeon" \
    --text="Pigeon is not installed yet.

Double-click Install-Pigeon first."
else
  printf 'Pigeon is not installed. Run Install Pigeon first.\n' >&2
fi
exit 1
