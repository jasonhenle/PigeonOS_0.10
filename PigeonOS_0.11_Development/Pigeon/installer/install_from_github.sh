#!/bin/bash
# Fresh-install Pigeon from GitHub (no Mac required).
#
# Pi / Linux: downloads the official release tarball (or main-branch zip fallback).
# macOS: downloads main-branch zip and runs the Mac installer.
#
# Usage:
#   bash install_from_github.sh
#   bash install_from_github.sh --dir "$HOME/Pigeon_0.7.20"
#   PIGEON_INSTALL_DIR=~/Apps/Pigeon bash install_from_github.sh
set -euo pipefail

REPO="${PIGEON_UPDATE_GITHUB_USER:-jasonhenle}/${PIGEON_UPDATE_GITHUB_REPO:-PigeonOS_0.10}"
BRANCH="${PIGEON_UPDATE_GITHUB_BRANCH:-main}"
APP_PREFIX="PigeonOS_0.11_Development/Pigeon"
INSTALL_DIR="${PIGEON_INSTALL_DIR:-}"
IN_PLACE=0

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
    -h|--help)
      cat <<EOF
Install Pigeon from GitHub (${REPO}, branch ${BRANCH}).

  bash install_from_github.sh [--dir PATH] [--in-place]

Pi/Linux: prefers GitHub Release tarball (pigeon_*_raspberry_pi.tar.gz).
Fallback: main-branch zip (same source as in-app Updates).

macOS: main-branch zip + installer/install_pigeon.sh

Settings (devices, TMDb key, pairing) stay in ~/.pigeon_0_6 — not overwritten.
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

for cmd in curl python3 tar; do
  command -v "${cmd}" >/dev/null 2>&1 || {
    echo "pigeon: ${cmd} is required." >&2
    exit 1
  }
done

WORKDIR="$(mktemp -d /tmp/pigeon-install.XXXXXX)"
trap 'rm -rf "${WORKDIR}"' EXIT

resolve_release_tarball() {
  python3 - <<PY
import json, urllib.request, sys
repo = "${REPO}"
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/releases/latest",
    headers={"User-Agent": "Pigeon/install", "Accept": "application/vnd.github+json"},
)
try:
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.load(resp)
except Exception:
    sys.exit(1)
for asset in data.get("assets") or []:
    name = str(asset.get("name") or "")
    if name.startswith("pigeon_") and name.endswith("_raspberry_pi.tar.gz"):
        url = str(asset.get("browser_download_url") or "").strip()
        if url:
            print(url)
            sys.exit(0)
sys.exit(1)
PY
}

install_from_zip() {
  local zip_url="https://codeload.github.com/${REPO}/zip/refs/heads/${BRANCH}"
  echo "==> Downloading ${zip_url}" >&2
  curl -fsSL -o "${WORKDIR}/pigeon.zip" "${zip_url}"
  python3 - <<'PY' "${WORKDIR}/pigeon.zip" "${WORKDIR}/extract"
import sys, zipfile
from pathlib import Path
zipfile.ZipFile(sys.argv[1]).extractall(Path(sys.argv[2]))
PY
  local app=""
  for d in "${WORKDIR}/extract"/*/"${APP_PREFIX}"; do
    if [[ -f "${d}/pigeonSystem/pigeon_0_9.py" || -f "${d}/pigeonSystem/pigeon_0_8.py" ]]; then
      app="${d}"
      break
    fi
  done
  if [[ -z "${app}" ]]; then
    echo "pigeon: could not find app folder in GitHub zip." >&2
    exit 1
  fi
  echo "${app}"
}

install_pi_linux() {
  local tarball_url=""
  if tarball_url="$(resolve_release_tarball 2>/dev/null)"; then
    echo "==> Downloading release tarball" >&2
    echo "    ${tarball_url}" >&2
    curl -fsSL -o "${WORKDIR}/pigeon.tar.gz" "${tarball_url}"
    tar -xzf "${WORKDIR}/pigeon.tar.gz" -C "${WORKDIR}"
    local app=""
    for d in "${WORKDIR}"/Pigeon_*; do
      if [[ -f "${d}/pigeonSystem/pigeon_0_9.py" || -f "${d}/pigeonSystem/pigeon_0_8.py" ]]; then
        app="${d}"
        break
      fi
    done
    if [[ -n "${app}" ]]; then
      echo "${app}"
      return
    fi
    echo "==> Latest release tarball is not a 0.10 package — falling back to main-branch zip." >&2
  fi

  echo "==> Using main-branch zip (full repo snapshot)." >&2
  install_from_zip
}

OS="$(uname -s)"
if [[ "${OS}" == "Darwin" ]]; then
  APP_SRC="$(install_from_zip)"
  echo "==> Running Mac installer from ${APP_SRC}"
  if [[ -n "${INSTALL_DIR}" ]]; then
    exec bash "${APP_SRC}/installer/install_pigeon.sh" --dir "${INSTALL_DIR}"
  fi
  exec bash "${APP_SRC}/installer/install_pigeon.sh"
fi

if [[ "${OS}" != "Linux" ]]; then
  echo "pigeon: unsupported OS ${OS} — try git clone and manual install." >&2
  exit 1
fi

APP_SRC="$(install_pi_linux)"
echo "==> Installing from ${APP_SRC}"

if [[ "${IN_PLACE}" -eq 1 && -n "${INSTALL_DIR}" ]]; then
  echo "pigeon: --in-place with --dir is ambiguous; use one or the other." >&2
  exit 1
fi

if [[ "${IN_PLACE}" -eq 1 ]]; then
  exec bash "${APP_SRC}/installer/install_on_pi.sh" --in-place
fi

if [[ -n "${INSTALL_DIR}" ]]; then
  exec bash "${APP_SRC}/installer/install_on_pi.sh" --dir "${INSTALL_DIR}"
fi

exec bash "${APP_SRC}/installer/install_on_pi.sh"
