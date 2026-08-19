# Pigeon on Raspberry Pi

Pigeon runs on **Raspberry Pi OS** (64-bit recommended: Pi 4 / Pi 5 with desktop). Version is in `pigeonSystem/pigeon/version.py`.

**You do not need a Mac.** Install directly from GitHub.

## Fresh install from GitHub (recommended)

On the Pi (Terminal):

```bash
curl -fsSL -o /tmp/pigeon-install.sh \
  "https://raw.githubusercontent.com/jasonhenle/PigeonOS_0.10/main/PigeonOS_0.10_Development/Pigeon/installer/install_from_github.sh"
bash /tmp/pigeon-install.sh
```

This downloads the latest **[GitHub Release](https://github.com/jasonhenle/PigeonOS_0.10/releases)** tarball (full app + `pigeonAssets/`) and runs the installer. If no release exists yet, it uses the main-branch zip instead.

**Desktop alternative:**

1. Open https://github.com/jasonhenle/PigeonOS_0.10/releases
2. Download `pigeon_<version>_raspberry_pi.tar.gz`
3. Extract → open `Pigeon_<version>/installer/` → double-click **Install-Pigeon**  
   (see `installer/START-HERE.txt`)

## What you need on the Pi

- Raspberry Pi OS with **desktop** (Tkinter).
- Network (pip on first run, TMDb, device control).
- **2 GB RAM** or more recommended.

## After install

- **Run Pigeon** from the Desktop shortcut, or:  
  `cd ~/Pigeon_*/ && ./installer/run_pigeon_0_10.sh`
- **Settings** live in `~/.pigeon_0_6/` (copy from Mac for TMDb key + Apple TV pairing — see `installer/setup/README.txt`).
- **In-app updates:** Settings → **Updates** (pulls from GitHub `main`).
- **Manual update:** `bash ~/Pigeon_*/installer/pi_update_from_github.sh`

## Display (800×480)

Fullscreen by default (`PIGEON_PI_FULLSCREEN=1`). UI is 800×480 (matches the Pi panel).

```bash
PIGEON_PI_FULLSCREEN=0 ./installer/run_pigeon_0_10.sh
```

## Autostart

```bash
sudo systemctl start pigeon
sudo systemctl status pigeon
journalctl -u pigeon -f
```

Disable: `sudo systemctl disable --now pigeon`

## Building a tarball locally (optional)

Any Linux machine (including Pi) can run:

```bash
bash raspberryPi/package_for_pi.sh
```

Published tarballs on **GitHub Releases** are built automatically when `version.py` changes on `main` — that is the canonical download for fresh Pi installs.

## Troubleshooting

See `installer/START-HERE.txt` and `~/.pigeon_0_6/pigeon.log`.

**TMDb / metadata:** copy `~/.pigeon_0_6` from a working machine or add `tmdb_api_key` on the Pi.

**OpenCV / display:** `sudo apt install python3-tk libgl1 libglib2.0-0 libgomp1`

**HDMI OCR:** `sudo apt install tesseract-ocr` (Install-Pigeon now includes this). `pytesseract` is the Python wrapper only.

## Files in this folder

| File | Purpose |
|------|---------|
| `package_for_pi.sh` | Build `dist/pigeon_*_raspberry_pi.tar.gz` (also run in CI) |
| `README_RASPBERRY_PI.md` | This guide |

Install scripts: `installer/install_on_pi.sh`, `installer/install_from_github.sh`, `installer/Install-Pigeon`.
