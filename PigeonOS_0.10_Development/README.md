# PigeonOS 0.10 Development

Active development for **PigeonOS 0.10** lives here.

```
PigeonOS_0.10_Development/
  Pigeon/                 Runnable app (Mac + Pi) — code + pigeonAssets
  PigeonOS_0.10_GFX/       Design sources (.ai / exports) — local archive, not shipped
  docs/                   Settings / design notes
  testingEnvironments/    Standalone widget previews
  README.md               This glossary + folder map
```

**Mac:** `Pigeon/installer/run_pigeon_0_10.command` (or `run_pigeon_0_8.command` — same entrypoint for now)  
**Pi:** see `Pigeon/installer/START-HERE.txt`  
**Settings / credentials:** `~/.pigeon_0_6` (shared with earlier installs)

**Desktop shortcuts:** `Pigeon (experiment).command`, `Pigeon (0.10).command`, `Pigeon (main).command`  
(from `Pigeon/installer/setup_desktop_launchers.sh`)

Previous 0.9 tree + GFX: `../Archive/PigeonOS_0.9_Development/` (+ `PigeonOS_0.9_GFX/`)  
Previous 0.8 tree (code + design GFX): `../Archive/Pigeon_0.8.0_Development/`  
Do **not** link new builds to Archive paths (use `PigeonOS_0.10_GFX/` for 0.10 design).

---

# Glossary

Reference for names used in code, UI, and assets. Keep this in sync when adding views or assets.

## Display views (keys `1`–`5`)

| Key | Code name | What you see |
|-----|-----------|--------------|
| **1** | View One / `DisplayView.ONE` | Primary “now playing” / media chrome. Layouts cycle with **Shift+1**. |
| **2** | View Two | Mic / audio visualizer preset. |
| **3** | View Three | Dedicated large clock / saver-oriented layout. |
| **4** | View Four | Plain background + rawTitle / debug text. |
| **5** | View Five | Design grid overlay (19×8) on the composite; cycles sub-modes. |

### View One layouts (`ViewOneLayout`, Shift+1)

| Layout | Meaning |
|--------|---------|
| **pigeonFull** | Backdrop + logos + clock + status bar (full chrome). |
| **pigeonSimple** | Black + large centered title + clock + status bar. |
| **pigeonPoster** | Poster-forward layout when artwork is available. |

### View One content variants (`ViewOneVariant` / `viewOne.01` … `.09`)

Resolved by which TMDb / logo assets are present (see `pigeon/view_one_variants.py`):

| Variant | Role |
|---------|------|
| `.01` | Default TT-only simple layout |
| `.02` | Full composition (backdrop + title treatment + app logo + chrome) |
| `.03`–`.08` | Fallbacks when backdrop / title treatment / logos are missing |
| `.09` / `noContent` | Everything missing — static Pigeon logo at reduced opacity |

View One skins: **`circles`** (default now-playing circles UI) ↔ **`classic`**.

## Major features

| Feature | Where / notes |
|---------|----------------|
| **Main settings** | Tab / Shift+Tab; `MainSettingsWidget` |
| **Updates** | Settings → Updates; pulls `main` from GitHub |
| **Apple TV / Roku metadata** | pyatv / companion; device pairing in settings |
| **TMDb artwork** | Backdrops, posters, title treatments cached under assets / `~/.pigeon_0_6` |
| **Status bar** | Streaming badge, volume, connection chrome |
| **Clock / calendar** | View 1 chrome + view 3 / idle saver |
| **Info cluster** | Title / metadata cluster on playback layouts |
| **Splash** | `pigeonSplash.mp4` (or PNG sequence) at startup |
| **Rotary / Mega hardware** | USB CDC or UNO Q Monitor TCP; protocol `MEGA,TYPE,ID,DATA` |
| **Grid overlay** | Key `5` — 19×8 design grid for layout work |
| **Scene** | Landing / black / TMDb backdrop cycling (F10 while grid visible) |

## Runtime asset names (`Pigeon/pigeonAssets/`)

Shipped with the app. Paths are relative to the `Pigeon/` app root.

| Asset | Role |
|-------|------|
| `TopGradient.png` | Optional full-canvas gradient above backdrop |
| `pigeonTempLogo.png` / logo PNGs | Brand / fallback logos |
| `pigeonSplash.mp4` | Startup splash |
| `P_0.5_statusBar.png` / `_CTI` | Status bar chrome |
| `P_0.5_posterArt_4x6_MEDIUM_*` | Poster frame, mask, placeholder |
| `P_0.5_pigeonLogo_4x6_MEDIUM_pigeonLogo.png` | Mid-size logo |
| `view_circles.svg` / `view_circles_music.svg` | Circles now-playing skins |
| `nowPlayingCircle.png` | Circles element |
| `pigeonNowPlaying_*.png` | Classic now-playing bar / timecode art |
| `Refresh_icon.png` | UI refresh affordance |
| `settings_0.8/*.svg` | Settings chrome + on-screen keyboards |
| `fonts/` | Bundled UI fonts |
| `App logos/` | Streaming service badges (when present) |
| Hex-named `.png`/`.jpg` | TMDb title-key cache files (gitignored; regenerated) |

Design sources (Illustrator, etc.) live in **`PigeonOS_0.10_GFX/`** — not required for install/update.

## Keyboard shortcuts

Canonical list is also in Developer settings via `pigeon/hotkeys.py`.

| Shortcut | Action |
|----------|--------|
| **Tab** / **Shift+Tab** / **F9** | Toggle Settings ↔ off |
| **Ctrl+Shift+Tab** | Advanced capability matrix (extension build) |
| **1–5** | Switch display views (see above) |
| **Shift+1** | Cycle View One layout (full / simple / poster) |
| **Arrows** | TV navigation |
| **Shift+arrows** | Volume / skip |
| **Cmd+arrows** | TV back / home / power (device-dependent) |
| **Space** | Play/pause, or backdrop+logo, or landing pulse |
| **Return** | Select / open command bar (settings or grid) |
| **Esc** | Close command bar or quit |
| **S** | Toggle scene (while grid visible) |
| **F10** | Cycle scene modes (with grid) or toggle scene |
| **Ctrl+Shift+S** | Toggle scene |
| **Double-click video** | Toggle scene |
| **Right-click video** | Same as Shift+Tab |

### Rotary encoder (Mega / UNO Q)

| Physical | Wire / key | App action |
|----------|------------|------------|
| CW | `MEGA,ENCODER,NAV,RIGHT` or `RIGHT` | forward |
| CCW | `MEGA,ENCODER,NAV,LEFT` or `LEFT` | backward |
| Push | `MEGA,BUTTON,NAV,PRESSED` or `PUSH` | activate |

Env: `PIGEON_ROTARY_SERIAL=0` disables; `PIGEON_ADB_SERIAL` selects ADB device; see `hardware/rotary_hid/README.md`.

## Important code modules

| Module | Role |
|--------|------|
| `pigeon_0_8.py` | Main application entry (filename retained in 0.9) |
| `pigeon/version.py` | `0.10.x` version source of truth |
| `pigeon/rotary_serial.py` | USB / UNO Q rotary bridge |
| `pigeon/hardware_protocol.py` | `SOURCE,TYPE,ID,DATA` parse / nav mapping |
| `pigeon/view_one_variants.py` | View One variant resolution |
| `pigeon/widgets/*` | Clock, status, settings, playback, circles, etc. |
| `pigeon/github_update.py` | In-app update from GitHub `main` |

## Environment / paths

| Item | Location |
|------|----------|
| User settings | `~/.pigeon_0_6/` |
| Install root on Pi (typical) | `~/Pigeon_0.7.23/` (legacy folder name on device) |
| Dev app root | `PigeonOS_0.10_Development/Pigeon/` |
| GitHub repo | `jasonhenle/PigeonOS_0.10` |

## Versioning

Bump **PATCH** in `Pigeon/pigeonSystem/pigeon/version.py` for every code change.  
`MINOR=10` is the PigeonOS 0.10 line. Releases publish when `version.py` lands on **`main`**.
