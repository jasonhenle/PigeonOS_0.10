#!/bin/bash
# Canonical GitHub updater for Pigeon (Pi / Linux). Always fetch fresh from GitHub:
#   curl -fsSL "$URL" | bash -s -- /path/to/Pigeon_X.Y.Z
#
# Uses curl + rsync only — no Python http.client (avoids latin-1 / U+202F token errors).
set -euo pipefail

REPO="${PIGEON_UPDATE_GITHUB_USER:-jasonhenle}/${PIGEON_UPDATE_GITHUB_REPO:-PigeonOS_0.10}"
BRANCH="${PIGEON_UPDATE_GITHUB_BRANCH:-main}"
ZIP_URL="https://codeload.github.com/${REPO}/zip/refs/heads/${BRANCH}"
APP_REL="PigeonOS_0.11_Development/Pigeon"
STATE_DIR="${HOME}/.pigeon_0_6"
LOG_FILE="${STATE_DIR}/pigeon.log"

INSTALL_DIR="${1:-${PIGEON_INSTALL_ROOT:-}}"
if [[ -z "${INSTALL_DIR}" ]]; then
  for d in "${HOME}"/Pigeon_*; do
    if [[ -f "${d}/pigeonSystem/pigeon_0_9.py" || -f "${d}/pigeonSystem/pigeon_0_8.py" || -f "${d}/pigeonSystem/pigeon_0_7.py" ]]; then
      INSTALL_DIR="${d}"
      break
    fi
  done
fi

log() {
  local line="pigeon-update: $*"
  echo "${line}"
  mkdir -p "${STATE_DIR}"
  printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${line}" >> "${LOG_FILE}" 2>/dev/null || true
}

die() {
  log "ERROR: $*"
  echo "pigeon-update ERROR: $*" >&2
  exit 1
}

if [[ -z "${INSTALL_DIR}" || ! -d "${INSTALL_DIR}" ]]; then
  die "could not find Pigeon install folder. Usage: bash pigeon_github_update.sh /path/to/Pigeon_X.Y.Z"
fi

INSTALL_DIR="$(cd "${INSTALL_DIR}" && pwd)"

schedule_in_app_relaunch() {
  local parent_pid="${PIGEON_UPDATE_PARENT_PID:-}"
  local relaunch="${INSTALL_DIR}/installer/run_pigeon_0_10.sh"
  if [[ ! -x "${relaunch}" ]]; then
    relaunch="${INSTALL_DIR}/installer/click_run_pigeon_pi.sh"
  fi
  if [[ ! -x "${relaunch}" ]]; then
    log "no 0.10 launcher found for in-app relaunch"
    return 0
  fi
  log "scheduling in-app relaunch via ${relaunch}"
  # Wait for the in-app parent to exit, but never hang forever if it stalls.
  nohup bash -c '
    parent="$1"
    launcher="$2"
    if [[ -n "${parent}" && "${parent}" != "0" ]]; then
      waited=0
      while kill -0 "${parent}" 2>/dev/null; do
        sleep 0.25
        waited=$((waited + 1))
        if [[ "${waited}" -ge 120 ]]; then
          break
        fi
      done
      sleep 1
    else
      sleep 6
    fi
    exec bash "$launcher"
  ' _ "${parent_pid}" "${relaunch}" >/dev/null 2>&1 &
}

log "starting update for ${INSTALL_DIR}"
log "zip ${ZIP_URL}"

rm -f "${STATE_DIR}/github_update_token" 2>/dev/null || true
unset PIGEON_UPDATE_GITHUB_TOKEN GITHUB_TOKEN GH_TOKEN GITHUB_PAT 2>/dev/null || true

for cmd in curl rsync python3 bash; do
  command -v "${cmd}" >/dev/null 2>&1 || die "${cmd} missing — run: sudo apt install curl rsync python3"
done

WORKDIR="$(mktemp -d /tmp/pigeon-github-update.XXXXXX)"
trap 'rm -rf "${WORKDIR}"' EXIT

log "downloading zip"
curl -fsSL -o "${WORKDIR}/pigeon.zip" "${ZIP_URL}" || die "curl download failed (network or GitHub blocked)"

log "extracting archive"
python3 - <<'PY' "${WORKDIR}/pigeon.zip" "${WORKDIR}/extract" "${APP_REL}"
import sys
import zipfile
from pathlib import Path

zip_path, out, app_rel = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
marker = app_rel.rstrip("/") + "/"
out.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(zip_path) as zf:
    for info in zf.infolist():
        name = info.filename
        if marker not in name:
            continue
        target = out / name
        if info.is_dir() or name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            dst.write(src.read())
        # zipfile does not restore Unix modes; keep +x so rsync -a does not
        # strip it from installed launchers (systemd execs them directly).
        mode = (info.external_attr >> 16) & 0o7777
        if mode:
            target.chmod(mode)
PY

EXTRACT="${WORKDIR}/extract"
SRC=""
for candidate in \
  "${EXTRACT}"/*/"${APP_REL}" \
  "${EXTRACT}/${APP_REL}" \
  "${EXTRACT}"/*; do
  if [[ -f "${candidate}/pigeonSystem/pigeon_0_9.py" || -f "${candidate}/pigeonSystem/pigeon_0_8.py" ]]; then
    SRC="${candidate}"
    break
  fi
done

if [[ -z "${SRC}" || ! -d "${SRC}/pigeonSystem" ]]; then
  die "could not find ${APP_REL} inside GitHub zip"
fi

log "merging from ${SRC}"
rsync -a \
  --exclude 'pigeonSystem/.venv' \
  --exclude 'pigeonCashe' \
  --exclude 'pigeonTMDB' \
  --exclude 'raspberryPi/dist' \
  --exclude 'pigeonSystem/__pycache__' \
  --exclude '.DS_Store' \
  "${SRC}/" "${INSTALL_DIR}/"

if [[ -d "${SRC}/pigeonAssets" ]]; then
  log "refreshing pigeonAssets"
  mkdir -p "${INSTALL_DIR}/pigeonAssets"
  rsync -a "${SRC}/pigeonAssets/" "${INSTALL_DIR}/pigeonAssets/"
fi

# GitHub zip extraction drops Unix +x bits; desktop double-click launchers need them.
if [[ -d "${INSTALL_DIR}/installer" ]]; then
  chmod +x "${INSTALL_DIR}/installer/"*.sh 2>/dev/null || true
  chmod +x "${INSTALL_DIR}/installer/"*.command 2>/dev/null || true
  chmod +x "${INSTALL_DIR}/installer/Run-Pigeon" "${INSTALL_DIR}/installer/Install-Pigeon" 2>/dev/null || true
fi

# shellcheck source=common.sh
source "${INSTALL_DIR}/installer/common.sh"

log "running pip bootstrap"
if ! bash "${INSTALL_DIR}/installer/run_pigeon_0_10.sh" --bootstrap-only; then
  die "pip bootstrap failed — check ${LOG_FILE} and run: bash ${INSTALL_DIR}/installer/run_pigeon_0_10.sh --bootstrap-only"
fi

pigeon_install_bundled_fonts "${INSTALL_DIR}" "${HOME}"
pigeon_refresh_systemd_service "${INSTALL_DIR}" "$(id -un)" "${HOME}" || true

VER="$(pigeon_version_string "${INSTALL_DIR}")"
log "finished — Pigeon ${VER}"
echo ""
echo "Pigeon ${VER} installed."

if [[ "${PIGEON_UPDATE_IN_APP:-}" == "1" ]]; then
  # Do not systemctl-stop here: that can kill this updater before relaunch is
  # scheduled, and sudoers typically allows restart but not stop.
  schedule_in_app_relaunch
  log "in-app update — PigeonOS 0.10 will start after the current app exits"
  echo "Restarting Pigeon…"
  exit 0
fi

log "restarting pigeon"
if [[ -f "/etc/systemd/system/pigeon.service" ]] \
  && grep -qE "run_pigeon_0_10\.sh|run_pigeon_0_8\.sh" "/etc/systemd/system/pigeon.service" 2>/dev/null \
  && command -v systemctl >/dev/null 2>&1; then
  if sudo -n systemctl restart pigeon.service 2>/dev/null \
    || systemctl restart pigeon.service 2>/dev/null \
    || sudo -n systemctl restart pigeon 2>/dev/null \
    || systemctl restart pigeon 2>/dev/null; then
    log "systemd restart ok"
    echo "Pigeon restarted via systemd."
    exit 0
  fi
fi

LAUNCHER="${INSTALL_DIR}/installer/run_pigeon_0_10.sh"
if [[ ! -x "${LAUNCHER}" ]]; then
  LAUNCHER="${INSTALL_DIR}/installer/click_run_pigeon_pi.sh"
fi
if [[ -x "${LAUNCHER}" ]]; then
  log "detached relaunch via ${LAUNCHER}"
  nohup bash "${LAUNCHER}" >/dev/null 2>&1 &
  echo "Pigeon relaunched."
else
  echo "Restart manually: bash ${INSTALL_DIR}/installer/run_pigeon_0_10.sh"
fi
