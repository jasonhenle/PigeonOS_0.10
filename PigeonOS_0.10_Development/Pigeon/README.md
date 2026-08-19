# PigeonOS 0.10

This folder is the runnable/shareable build for Pigeon (see `pigeonSystem/pigeon/version.py` for the current semver).

## What this build is

Pigeon is a full-screen media display and control surface. It can show playback visuals, TMDB artwork, clocks, overlays, and device status while also sending remote-control commands.

## Core folder map

- `installer/`: install and launch scripts for this build.
  - `installer/run_pigeon_0_10.command`: macOS launch script.
  - `installer/run_pigeon_0_10.sh`: Linux / Pi launch script.
- `pigeonSystem/`: all runnable code and system modules for this build.
  - main app entrypoint: `pigeonSystem/pigeon_0_9.py`
  - package code: `pigeonSystem/pigeon/`
  - widget code: `pigeonSystem/pigeon/widgets/`
- `pigeonAssets/`: static art and UI media.
  - `appLogos`: streaming service logos.
  - `pigeonSplash`: startup splash image sequence.
  - `pigeonUI`: non-purgeable UI assets.
- `pigeonCashe/`: centralized Python bytecode cache output (`PYTHONPYCACHEPREFIX` target).
- `pigeonTMDB/`: TMDB media staging folders (`pigeonTMDB_BD`, `pigeonTMDB_ORIGINAL`, `pigeonTMDB_TT`).
- `testingEnvironments/`: local test scripts in `PigeonOS_0.10_Development/testingEnvironments` (development-level folder).

## Hotkeys

- `Tab` / `Shift+Tab` / `Ctrl+Tab` / `F9`: toggle Settings on/off.
- `Ctrl+Shift+Tab`: open Advanced capability matrix (extension build).
- `Return` / `Enter`: open command bar in Settings/grid; otherwise sends player Select.
- `Esc`: close command bar or quit app.
- `Space`: player play/pause when possible; otherwise backdrop/brightness fallback behavior.
- `1-5`: switch views.
  - `1`: main Pigeon display (cycles full/simple presentation).
  - `2`: mic visualizer preset.
  - `3`: dedicated clock layout.
  - `4`: plain background + raw title debug.
  - `5`: design grid overlay.
- Arrow keys:
  - plain arrows: navigation
  - `Shift` + arrows: volume/skip
  - `Cmd` + arrows: back/home/power (device-dependent)
- `S`: scene toggle while grid overlay is visible.
- `F10`: scene cycle (or scene toggle when grid overlay is not visible).
- Double-click video: toggle scene.
- Right-click video: same as Settings toggle.

## Protocols (plain English)

- **App state protocol:** saved devices, locations, and delegation state are persisted through `pigeonSystem/pigeon/app_state.py`.
- **Image UI protocol:** layout/compositing rules for overlays, logos, and background content are handled in `pigeonSystem/pigeon/image_ui_protocol.py`.
- **Widget protocol:** widget behavior contracts and shell integration live in `pigeonSystem/pigeon/widget_protocol.py` and `pigeonSystem/pigeon/widget_shell.py`.
- **Device delegation protocol:** feature capability fallback/ordering and advanced matrix behavior are driven by `pigeonSystem/device_capability_matrix.py`, `pigeonSystem/settings_advanced_matrix.py`, and app-state delegation logs.
- **TMDB fetch protocol:** query generation, retries, and artwork preparation flow through `pigeonSystem/pigeon/tmdb_poster.py` plus retry logging.
- **Startup protocol:** splash animation starts first, then visualizer startup choreography runs (float on -> flap -> ascend -> soar -> float off).

## UI views

- **View 1 (Pigeon main):** main UI composition with backdrop/title/logo/clock/status behaviors.
- **View 2 (Visualizer):** mic visualizer-focused mode.
- **View 3 (Clock):** dedicated time/date emphasis.
- **View 4 (Debug):** plain layout with raw title debug text.
- **View 5 (Grid):** design grid overlay for alignment/tuning.
- **Settings panel:** device setup, TMDB controls, reset/debug tools, and build version at footer.

## Build notes

- This build version is defined in `pigeonSystem/pigeon/version.py`.
- Changelog lives at `Desktop/Pigeon/pigeonChangelog/CHANGELOG.md`.

## Releasing updates

Develop on branch **`experiment`**. The in-app **Updates** button only sees **`main`** on GitHub.

See **[RELEASE.md](RELEASE.md)** for the full workflow, or run from the git repo root:

```bash
./PigeonOS_0.10_Development/Pigeon/installer/ship_to_main.sh
```

### Two Desktop launchers (Mac)

Run once to create/update:

```bash
./PigeonOS_0.10_Development/Pigeon/installer/setup_desktop_launchers.sh
```

This adds **`Pigeon (experiment).command`** and **`Pigeon (main).command`** on your Desktop — separate folders so each branch can run without switching git.