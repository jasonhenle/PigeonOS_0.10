#!/bin/bash
# Install Pigeon on Raspberry Pi OS / Debian Linux (apt, venv, optional systemd autostart).
# Prefer:  ./installer/install_pigeon.sh
set -euo pipefail

INSTALLER_DIR="$(cd "$(dirname "$0")" && pwd)"
PIGEON_ROOT="$(cd "${INSTALLER_DIR}/.." && pwd)"
# shellcheck source=common.sh
source "${INSTALLER_DIR}/common.sh"

INSTALL_USER="${PIGEON_USER:-${SUDO_USER:-$(logname 2>/dev/null || echo "${USER:-pi}")}}"
INSTALL_HOME="$(getent passwd "${INSTALL_USER}" 2>/dev/null | cut -d: -f6 || echo "/home/${INSTALL_USER}")"
PIGEON_VERSION="$(pigeon_version_string "${PIGEON_ROOT}")"
INSTALL_DIR="${PIGEON_INSTALL_DIR:-${INSTALL_HOME}/Pigeon_${PIGEON_VERSION}}"
IN_PLACE=0
ENABLE_AUTOSTART=1
MAKE_SHORTCUTS=1
SKIP_APT=0

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
    --no-autostart)
      ENABLE_AUTOSTART=0
      shift
      ;;
    --no-shortcut)
      MAKE_SHORTCUTS=0
      shift
      ;;
    --skip-apt)
      SKIP_APT=1
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

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is for Linux / Raspberry Pi OS only." >&2
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Re-running with sudo for apt packages…" >&2
  exec sudo -E \
    PIGEON_INSTALL_DIR="${INSTALL_DIR}" \
    PIGEON_USER="${INSTALL_USER}" \
    bash "$0" "$@"
fi

VERSION="${PIGEON_VERSION}"
echo "==> Pigeon ${VERSION} installer (Linux / Raspberry Pi)"

pigeon_check_network() {
  if ping -c1 -W4 1.1.1.1 >/dev/null 2>&1 || ping -c1 -W4 deb.debian.org >/dev/null 2>&1; then
    return 0
  fi
  echo "pigeon: ERROR — this Pi does not appear to have internet access." >&2
  echo "pigeon: Connect Wi‑Fi or Ethernet, open a browser to confirm the web works, then retry." >&2
  echo "pigeon: Also check the Pi date/time is correct (wrong clock breaks apt)." >&2
  return 1
}

pigeon_apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  local attempt=""
  for attempt in 1 2 3; do
    echo "==> apt update (attempt ${attempt}/3)…"
    if apt-get update; then
      echo "==> Installing system packages for Pigeon…"
      if apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-tk \
        python3-pip \
        libportaudio2 \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libffi-dev \
        libssl-dev \
        fonts-dejavu-core \
        ca-certificates \
        avahi-daemon \
        libnss-mdns \
        zenity \
        rsync \
        tesseract-ocr; then
        return 0
      fi
    fi
    echo "pigeon: apt failed (attempt ${attempt}/3). Retrying in 10s…" >&2
    sleep 10
  done
  echo "pigeon: ERROR — apt could not download packages (E: failed to fetch)." >&2
  echo "pigeon: Fix internet on the Pi, then in a terminal run:" >&2
  echo "  sudo apt update && sudo apt upgrade" >&2
  echo "pigeon: When that works, run Install-Pigeon again." >&2
  return 1
}

if [[ "${SKIP_APT}" -eq 0 ]]; then
  pigeon_check_network || exit 1
  pigeon_apt_install || exit 1
else
  echo "==> Skipping apt (--skip-apt). Assuming system packages are already installed."
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl enable avahi-daemon >/dev/null 2>&1 || true
  systemctl start avahi-daemon >/dev/null 2>&1 || true
fi

pigeon_install_setup_secrets() {
  local dest_dir="${INSTALL_HOME}/.pigeon_0_6"
  mkdir -p "${dest_dir}"
  chown "${INSTALL_USER}:${INSTALL_USER}" "${dest_dir}"
  for name in tmdb_api_key tmdb_read_token pyatv_credentials; do
    local src="${INSTALL_DIR}/installer/setup/${name}"
    local dst="${dest_dir}/${name}"
    if [[ -f "${src}" && ! -f "${dst}" ]]; then
      install -m 600 -o "${INSTALL_USER}" -g "${INSTALL_USER}" "${src}" "${dst}"
      echo "==> Installed ${name} from setup/ (first-time only)."
    fi
  done
}

# Pi click-install uses --in-place; only copy when explicitly installing elsewhere.
if [[ "${IN_PLACE}" -eq 1 ]]; then
  INSTALL_DIR="${PIGEON_ROOT}"
  echo "==> Installing in place at ${INSTALL_DIR}"
else
  echo "==> Copying Pigeon to ${INSTALL_DIR}"
  mkdir -p "$(dirname "${INSTALL_DIR}")"
  pigeon_rsync_tree "${PIGEON_ROOT}" "${INSTALL_DIR}"
fi

pigeon_prepare_runtime_dirs "${INSTALL_DIR}"
pigeon_install_bundled_fonts "${INSTALL_DIR}" "${INSTALL_HOME}"
chown -R "${INSTALL_USER}:${INSTALL_USER}" "${INSTALL_HOME}/.local/share/fonts/pigeon" 2>/dev/null || true
chmod +x \
  "${INSTALL_DIR}/installer/run_pigeon_0_10.sh" \
  "${INSTALL_DIR}/installer/install_pigeon.sh" \
  "${INSTALL_DIR}/installer/install-pigeon.sh" \
  "${INSTALL_DIR}/installer/run-pigeon.sh" \
  "${INSTALL_DIR}/installer/Install-Pigeon" \
  "${INSTALL_DIR}/installer/Run-Pigeon" \
  "${INSTALL_DIR}/installer/click_install_pi.sh" \
  "${INSTALL_DIR}/installer/click_run_pigeon_pi.sh" \
  "${INSTALL_DIR}/installer/Install-Pigeon.desktop" \
  "${INSTALL_DIR}/installer/Run-Pigeon.desktop" \
  "${INSTALL_DIR}/installer/pi_update_from_github.sh" \
  "${INSTALL_DIR}/installer/pigeon_github_update.sh" \
  "${INSTALL_DIR}/installer/configure_pigeon_restart_sudo.sh" \
  "${INSTALL_DIR}/installer/install_from_github.sh" 2>/dev/null || true

echo "==> Creating Python virtual environment and installing pip packages (may take several minutes)…"
if ! sudo -u "${INSTALL_USER}" bash -c "cd '${INSTALL_DIR}' && ./installer/run_pigeon_0_10.sh --bootstrap-only"; then
  echo "pigeon: pip install failed. Common fixes:" >&2
  echo "  - Ensure the Pi has internet" >&2
  echo "  - Re-run Install-Pigeon" >&2
  echo "  - See ${INSTALL_HOME}/pigeon-install.log" >&2
  exit 1
fi

pigeon_install_setup_secrets

if [[ "${ENABLE_AUTOSTART}" -eq 1 ]] && command -v systemctl >/dev/null 2>&1; then
  echo "==> Installing systemd autostart service…"
  SERVICE_PATH="/etc/systemd/system/pigeon.service"
  sed \
    -e "s|@PIGEON_USER@|${INSTALL_USER}|g" \
    -e "s|@PIGEON_HOME@|${INSTALL_HOME}|g" \
    -e "s|@PIGEON_DIR@|${INSTALL_DIR}|g" \
    -e "s|@PIGEON_VERSION@|${VERSION}|g" \
    "${INSTALLER_DIR}/pigeon.service" > "${SERVICE_PATH}"
  systemctl daemon-reload
  systemctl enable pigeon.service
  pigeon_install_systemd_restart_sudoers "${INSTALL_USER}" || true
fi

if [[ "${MAKE_SHORTCUTS}" -eq 1 ]]; then
  DESKTOP_DIR="${INSTALL_HOME}/Desktop"
  mkdir -p "${DESKTOP_DIR}"
  cat > "${DESKTOP_DIR}/Run-Pigeon" <<EOF
#!/bin/bash
exec bash "${INSTALL_DIR}/installer/click_run_pigeon_pi.sh"
EOF
  chmod +x "${DESKTOP_DIR}/Run-Pigeon"
  chown "${INSTALL_USER}:${INSTALL_USER}" "${DESKTOP_DIR}/Run-Pigeon"
  cat > "${DESKTOP_DIR}/Run-Pigeon.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Run Pigeon
Comment=Launch Pigeon media display
TryExec=bash
Path=${INSTALL_DIR}/installer
Exec=bash run-pigeon.sh
Icon=video-display
Terminal=false
Categories=AudioVideo;
EOF
  chmod +x "${DESKTOP_DIR}/Run-Pigeon.desktop"
  chown "${INSTALL_USER}:${INSTALL_USER}" "${DESKTOP_DIR}/Run-Pigeon.desktop"
  echo "==> Desktop shortcuts: ${DESKTOP_DIR}/Run-Pigeon"
fi

echo ""
echo "Pigeon ${VERSION} is installed at: ${INSTALL_DIR}"
echo ""
echo "Launch from Desktop:"
echo "  Double-click “Run Pigeon” on the Desktop"
echo ""
echo "Or from a folder:"
echo "  cd '${INSTALL_DIR}' && ./installer/run_pigeon_0_10.sh"
echo ""
if [[ "${ENABLE_AUTOSTART}" -eq 1 ]] && command -v systemctl >/dev/null 2>&1; then
  echo "Autostart on boot (after graphical login):"
  echo "  sudo systemctl start pigeon"
  echo "  sudo systemctl status pigeon"
  echo "  journalctl -u pigeon -f"
  echo ""
  echo "In-app Updates restart pigeon.service without a password (sudoers.d/pigeon-update-restart)."
  echo ""
  echo "Disable autostart:"
  echo "  sudo systemctl disable --now pigeon"
  echo ""
fi
echo "State and credentials: ${INSTALL_HOME}/.pigeon_0_6 (user ${INSTALL_USER})"
echo ""
echo "For metadata + TMDb artwork (copy from Mac or add installer/setup/ files before install):"
echo "  ${INSTALL_DIR}/installer/setup/README.txt"
echo ""
