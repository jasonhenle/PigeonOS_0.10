# Pigeon / PigeonOS

**Pigeon** (now **PigeonOS**) is a full-screen media display and control surface (Apple TV / Roku metadata, TMDb artwork, clocks, status bar, remotes).

This repository is the **canonical source** for the app — fresh installs and in-app updates both pull from here.

## Active development

| Version | Folder | Git branch |
|---------|--------|------------|
| **PigeonOS 0.10 (current)** | [`PigeonOS_0.11_Development/Pigeon/`](PigeonOS_0.11_Development/Pigeon/) | `PigeonOS_0.10` (dev) / `main` (release) |
| 0.9 (archived locally) | [`Archive/PigeonOS_0.9_Development/`](Archive/PigeonOS_0.9_Development/) | `PigeonOS_0.9` / historical `pigeon_0.7.x` |
| 0.8 (archived locally) | [`Archive/Pigeon_0.8.0_Development/`](Archive/Pigeon_0.8.0_Development/) | git history on `main` before 0.9 |
| 0.7 (archived) | [`Archive/Pigeon_0.70_Development/`](Archive/Pigeon_0.70_Development/) | — |

> **Do not** point new builds or installers at `Archive/`. Runtime assets live under `PigeonOS_0.11_Development/Pigeon/pigeonAssets/`. Design sources for 0.10 live under `PigeonOS_0.11_Development/PigeonOS_0.11_GFX/` (local; not shipped in the Pi tarball).

## Quick links

| Goal | Where |
|------|--------|
| **Glossary (views, assets, shortcuts)** | [`PigeonOS_0.11_Development/README.md`](PigeonOS_0.11_Development/README.md) |
| **Install from scratch (Pi, Mac, Linux)** | [`PigeonOS_0.11_Development/Pigeon/GITHUB.md`](PigeonOS_0.11_Development/Pigeon/GITHUB.md) |
| **Raspberry Pi** | [`PigeonOS_0.11_Development/Pigeon/raspberryPi/README_RASPBERRY_PI.md`](PigeonOS_0.11_Development/Pigeon/raspberryPi/README_RASPBERRY_PI.md) |
| **App folder (code + assets)** | [`PigeonOS_0.11_Development/Pigeon/`](PigeonOS_0.11_Development/Pigeon/) |
| **GitHub Releases** (Pi tarballs) | https://github.com/jasonhenle/PigeonOS_0.10/releases |

## One-command install

**Raspberry Pi / Linux:**

```bash
curl -fsSL -o /tmp/pigeon-install.sh \
  "https://raw.githubusercontent.com/jasonhenle/PigeonOS_0.10/main/PigeonOS_0.11_Development/Pigeon/installer/install_from_github.sh"
bash /tmp/pigeon-install.sh
```

**macOS:** same script (downloads the main-branch zip and runs the Mac installer).

**Already installed:** open Pigeon → Settings → **Updates**.

## Branches

| Branch | Purpose |
|--------|---------|
| `PigeonOS_0.10` | Day-to-day PigeonOS 0.10 development |
| `main` | Release line — what Updates / installers pull |
| `experiment` | Legacy 0.8-era working branch (superseded by `PigeonOS_0.10`) |

## What is in git

The tracked tree includes **all runtime code and UI assets** (`pigeonAssets/`, `installer/`, `pigeonSystem/`). It does **not** include your personal settings (`~/.pigeon_0_6/`), local TMDb cache, or the large design GFX trees (`PigeonOS_0.11_GFX/`, `Pigeon_GFX/`, `Archive/`).

When `version.py` changes on `main`, GitHub Actions builds `pigeon_<version>_raspberry_pi.tar.gz` and attaches it to [Releases](https://github.com/jasonhenle/PigeonOS_0.10/releases).
