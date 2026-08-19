#!/bin/bash
# Build a Raspberry Pi–ready tarball from this Mac (or any Linux host).
# Output: raspberryPi/dist/pigeon_<version>_raspberry_pi.tar.gz (version from pigeonSystem/pigeon/version.py)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIGEON_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../installer/common.sh
source "${PIGEON_ROOT}/installer/common.sh"

VERSION="$(pigeon_version_string "${PIGEON_ROOT}")"
APP_DIR="$(pigeon_install_dir_basename "${PIGEON_ROOT}")"
DIST_DIR="${SCRIPT_DIR}/dist"
STAGING="${DIST_DIR}/staging"
ARCHIVE_NAME="pigeon_${VERSION}_raspberry_pi.tar.gz"

rm -f "${DIST_DIR}"/pigeon_*_raspberry_pi.tar.gz
rm -rf "${STAGING}"
mkdir -p "${STAGING}" "${DIST_DIR}"

echo "==> Staging Pigeon ${VERSION} (excluding Mac venv, caches, and local TMDB art)…"
rsync -a \
  --exclude '.DS_Store' \
  --exclude 'pigeonSystem/.venv' \
  --exclude 'pigeonSystem/__pycache__' \
  --exclude 'pigeonSystem/.cursor' \
  --exclude 'pigeonSystem/pigeon/**/__pycache__' \
  --exclude 'pigeonCashe' \
  --exclude 'raspberryPi/dist' \
  --exclude 'pigeonTMDB/pigeonTMDB_BD' \
  --exclude 'pigeonTMDB/pigeonTMDB_ORIGINAL' \
  --exclude 'pigeonTMDB/pigeonTMDB_Poster' \
  --exclude 'pigeonTMDB/pigeonTMDB_TT' \
  --exclude 'pigeonTMDB/*.jpg' \
  --exclude 'pigeonTMDB/*.png' \
  --exclude 'installer/install_pigeon.command' \
  --exclude 'installer/install-pigeon.sh' \
  --exclude 'installer/Install-Pigeon.desktop' \
  --exclude 'installer/Run-Pigeon.desktop' \
  --exclude 'installer/install_mac.sh' \
  "${PIGEON_ROOT}/" "${STAGING}/${APP_DIR}/"

# Guard against empty or partial staging (e.g. wrong cwd, missing assets after a bad copy).
STAGING_APP="${STAGING}/${APP_DIR}"
STAGING_BYTES="$(du -sk "${STAGING_APP}" | awk '{print $1}')"
if [[ ! -f "${STAGING_APP}/pigeonSystem/pigeon_0_9.py" ]]; then
  echo "ERROR: staging is missing pigeonSystem/pigeon_0_9.py — run from a full Pigeon tree." >&2
  exit 1
fi
if [[ ! -d "${STAGING_APP}/pigeonAssets/App logos" ]]; then
  echo "ERROR: staging is missing pigeonAssets — app tree looks incomplete." >&2
  exit 1
fi
if [[ "${STAGING_BYTES}" -lt 20480 ]]; then
  echo "ERROR: staging is only ${STAGING_BYTES} KB (expected tens of MB). Aborting." >&2
  exit 1
fi

mkdir -p \
  "${STAGING_APP}/pigeonCashe" \
  "${STAGING_APP}/pigeonTMDB/pigeonTMDB_BD" \
  "${STAGING_APP}/pigeonTMDB/pigeonTMDB_ORIGINAL" \
  "${STAGING_APP}/pigeonTMDB/pigeonTMDB_Poster" \
  "${STAGING_APP}/pigeonTMDB/pigeonTMDB_TT"

chmod +x \
  "${STAGING_APP}/installer/run_pigeon_0_10.sh" \
  "${STAGING_APP}/installer/run_pigeon_0_10.command" \
  "${STAGING_APP}/installer/install_pigeon.sh" \
  "${STAGING_APP}/installer/run-pigeon.sh" \
  "${STAGING_APP}/installer/Install-Pigeon" \
  "${STAGING_APP}/installer/Run-Pigeon" \
  "${STAGING_APP}/installer/click_install_pi.sh" \
  "${STAGING_APP}/installer/click_run_pigeon_pi.sh" \
  "${STAGING_APP}/installer/install_on_pi.sh" \
  "${STAGING_APP}/installer/pigeon_github_update.sh" \
  "${STAGING_APP}/installer/pi_update_from_github.sh" \
  "${STAGING_APP}/installer/install_from_github.sh" \
  "${STAGING_APP}/installer/install_from_github.sh"

# Stamp versioned folder name into Pi quick-start (dev tree uses placeholders).
if [[ -f "${PIGEON_ROOT}/installer/START-HERE.txt" ]]; then
  sed \
    -e "s|@APP_DIR@|${APP_DIR}|g" \
    -e "s|@VERSION@|${VERSION}|g" \
    "${PIGEON_ROOT}/installer/START-HERE.txt" > "${STAGING_APP}/installer/START-HERE.txt"
fi
echo "${VERSION}" > "${STAGING_APP}/BUILD_VERSION.txt"

echo "==> Writing ${DIST_DIR}/${ARCHIVE_NAME}"
tar -C "${STAGING}" -czf "${DIST_DIR}/${ARCHIVE_NAME}" "${APP_DIR}"

BYTES="$(wc -c < "${DIST_DIR}/${ARCHIVE_NAME}" | tr -d ' ')"
if [[ "${BYTES}" -lt 1048576 ]]; then
  echo "ERROR: ${ARCHIVE_NAME} is only ${BYTES} bytes — tarball looks empty or corrupt." >&2
  exit 1
fi
MB="$(awk "BEGIN {printf \"%.1f\", ${BYTES}/1048576}")"
echo ""
echo "Done: ${DIST_DIR}/${ARCHIVE_NAME} (${MB} MB)"
echo ""
echo "Copy to the Pi, then on the Pi:"
echo "  tar -xzf ${ARCHIVE_NAME}"
echo "  Open the ${APP_DIR}/installer folder and double-click Install-Pigeon"
