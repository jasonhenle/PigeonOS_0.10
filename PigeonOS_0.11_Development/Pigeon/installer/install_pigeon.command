#!/bin/bash
set -euo pipefail
INSTALLER_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${INSTALLER_DIR}"
exec bash "${INSTALLER_DIR}/install_pigeon.sh" "$@"
