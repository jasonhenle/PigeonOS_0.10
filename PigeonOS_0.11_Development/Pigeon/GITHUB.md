# Pigeon and GitHub

Repository: **https://github.com/jasonhenle/PigeonOS_0.10**  
Default branch: **`main`**

**GitHub is the canonical source for Pigeon.** The full app (code, `pigeonAssets/`, installers) lives in this repo. Fresh installs and in-app updates both use it. You do **not** need a Mac to download or package Pigeon.

Pigeon splits **app files** (this repo) from **your settings** (`~/.pigeon_0_6/`). Updates never delete settings.

---

## 1. Fresh install (new Pi, new Mac, new machine)

### Recommended — one script (Pi / Linux / Mac)

```bash
curl -fsSL -o /tmp/pigeon-install.sh \
  "https://raw.githubusercontent.com/jasonhenle/PigeonOS_0.10/main/PigeonOS_0.11_Development/Pigeon/installer/install_from_github.sh"
bash /tmp/pigeon-install.sh
```

| Platform | What the script does |
|----------|----------------------|
| **Raspberry Pi / Linux** | Downloads the latest **[GitHub Release](https://github.com/jasonhenle/PigeonOS_0.10/releases)** tarball (`pigeon_*_raspberry_pi.tar.gz`, includes all assets). If no release exists yet, falls back to the main-branch zip (same content as Updates). Runs `installer/install_on_pi.sh`. |
| **macOS** | Downloads main-branch zip and runs `installer/install_pigeon.sh`. |

Optional: `bash /tmp/pigeon-install.sh --dir "$HOME/MyPigeon"` or `--in-place` (Pi, install inside extracted folder).

### Alternative — GitHub Releases (Pi)

1. Open **https://github.com/jasonhenle/PigeonOS_0.10/releases**
2. Download `pigeon_<version>_raspberry_pi.tar.gz`
3. Extract, open `Pigeon_<version>/installer/`, double-click **Install-Pigeon** (see `START-HERE.txt`)

Releases are built automatically when `pigeonSystem/pigeon/version.py` changes on `main`.

### Alternative — ZIP or git (any platform)

**ZIP:** https://github.com/jasonhenle/PigeonOS_0.10/archive/refs/heads/main.zip  
Then go to **`PigeonOS_0.11_Development/Pigeon/`** inside the extracted folder and run the installer for your platform.

**Clone:**

```bash
git clone https://github.com/jasonhenle/PigeonOS_0.10.git
cd PigeonOS_0.10/PigeonOS_0.11_Development/Pigeon
./installer/install_pigeon.sh          # Mac
./installer/install_on_pi.sh           # Pi / Linux
```

The zip and clone contain the **same tracked files** as Updates (including `pigeonAssets/`). Runtime caches (`pigeonCashe/`, downloaded TMDb art in `pigeonTMDB/`) are created locally and are not in git.

### What lives where after install

| Location | Contents |
|----------|----------|
| App folder (e.g. `~/Pigeon_0.7.20`) | Code, launcher, venv, local TMDb cache folders |
| `~/.pigeon_0_6/` | **Settings:** locations, devices, TMDb API key, Apple TV pairing, logs |

Copy **`~/.pigeon_0_6/`** from another machine to move settings (see `installer/setup/README.txt` on Pi).

---

## 2. In-app update (Settings → red **Updates** button)

When GitHub has a **newer** `pigeonSystem/pigeon/version.py` than your installed copy:

1. Open **Settings** (Shift+Tab in developer mode).
2. Tap the red **Updates** button.
3. Confirm **Install update**.

Pigeon will:

- Download the latest app tree from GitHub (zip of `main`).
- Merge into your **current install folder** (including `pigeonAssets/`).
- Refresh Python dependencies.
- **Not** modify `~/.pigeon_0_6/`.
- **Not** replace cached TMDb downloads in `pigeonTMDB/`.

Quit and relaunch when prompted.

**Pi already on a release tarball?** Updates keep your install folder current without re-downloading the tarball. For a clean reinstall, use `install_from_github.sh` or a Release download.

### Optional GitHub token

`jasonhenle/PigeonOS_0.10` is **public** — no token required.

For a private fork: `~/.pigeon_0_6/github_update_token` or `PIGEON_UPDATE_GITHUB_TOKEN`.

---

## 3. Maintainers — version bumps and releases

1. Bump **`pigeonSystem/pigeon/version.py`** (`MAJOR` / `MINOR` / `PATCH`).
2. Push to **`main`**.
3. GitHub Actions (`.github/workflows/pigeon-release.yml`) builds `pigeon_<version>_raspberry_pi.tar.gz` and publishes **Release `v<version>`**.

You can also run **`raspberryPi/package_for_pi.sh`** locally; tarballs in `raspberryPi/dist/` are gitignored because Releases are the published copies.

---

## Environment overrides

| Variable | Purpose |
|----------|---------|
| `PIGEON_UPDATE_GITHUB_USER` | Default `jasonhenle` |
| `PIGEON_UPDATE_GITHUB_REPO` | Default `PigeonOS_0.10` |
| `PIGEON_UPDATE_GITHUB_BRANCH` | Branch for version check and zip download (default `main`) |
| `PIGEON_UPDATE_GITHUB_TOKEN` | Optional GitHub PAT |
| `PIGEON_STATE_DIR` | Settings directory (default `~/.pigeon_0_6`) |
| `PIGEON_INSTALL_DIR` | Target install path for installers |
