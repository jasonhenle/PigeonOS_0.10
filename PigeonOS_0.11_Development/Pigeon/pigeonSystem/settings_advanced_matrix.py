# settings_advanced_matrix.py — next to pigeon_0_9.py (Tkinter Advanced delegation editor).
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
import tkinter.scrolledtext as scrolledtext

from device_capability_matrix import FEATURES, active_device_columns, level_for

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]

_MODULE_DIR = Path(__file__).resolve().parent
_REFRESH_ICON_PATH = _MODULE_DIR / "pigeonAssets" / "Refresh_icon.png"
_REFRESH_DISPLAY_PX = 32
_REFRESH_SPIN_STEPS = 36
_REFRESH_SPIN_MS = 1000

# Match pigeon_0_5._paint_boolean_led semantics: green / amber / red
_LED_FULL = "#1fcb5d"
_LED_PARTIAL = "#f0ad4e"
_LED_NONE = "#e74c3c"

_TRACK_BG = "#141419"
_TRACK_FG = "#e8e8e8"
_TILE_BG = "#1f1f26"
_TILE_SIZE = 102
_SLOT_GAP = 10

# Star / try-order numbers are small; the large “traffic light” circle is the main indicator.
_NAME_IN_TILE_FONT = ("Helvetica", 9, "bold")
_LED_TOP_PAD = 6
_LED_RADIUS = 24  # diameter 48px — dominant capability indicator
_NAME_CY_OFFSET = 62  # below the large LED
_BADGE_FONT = ("Helvetica", 12, "bold")
_BADGE_CY_OFFSET = 86
_TILE_OUTLINE_WIDTH = 2
_TILE_OUTLINE_WIDTH_ACTIVE = 4

_FEAT_NAME_FONT = ("Helvetica", 20, "bold")
_ACTIVE_OUTLINE = "#e8c547"
_PRIORITY_STAR = "\u2605"  # ★ — Pigeon auto-selection (best capability), not necessarily slot 1
# Slight warm tint when this slot is the last delegation attempt (outline stays level color, thicker).
_TILE_ACTIVE_FILL = "#2a2830"


def _level_color(level: str) -> str:
    if level == "full":
        return _LED_FULL
    if level == "partial":
        return _LED_PARTIAL
    return _LED_NONE


def _legacy_override_key(label: str, cap_id: str) -> str:
    return f"{cap_id}::{label.strip()}"


def _auto_order_for_feature(
    devs: list[tuple[str, str, str]],
    feature_id: str,
    *,
    level_resolve: Callable[[str, str, str], str] | None = None,
) -> list[tuple[str, str, str]]:
    score = {"full": 0, "partial": 1, "none": 2}

    def _lv(cap: str, fid: str, sk: str) -> str:
        return level_resolve(cap, fid, sk) if level_resolve else level_for(cap, fid)

    ranked = sorted(
        devs,
        key=lambda d: (score.get(_lv(d[1], feature_id, d[2]), 2), d[0].lower()),
    )
    return ranked


def _feature_fully_incompatible(
    feature_id: str,
    devs: list[tuple[str, str, str]],
    *,
    level_resolve: Callable[[str, str, str], str] | None = None,
) -> bool:
    """True when no saved device supports this feature at all (all matrix entries are none)."""
    if not devs:
        return True

    def _lv(cap: str, fid: str, sk: str) -> str:
        return level_resolve(cap, fid, sk) if level_resolve else level_for(cap, fid)

    return all(_lv(cap, feature_id, sk) == "none" for (_, cap, sk) in devs)


def _feature_row_playback_working(
    pipeline_ok: bool,
    incompatible: bool,
    ordered: list[tuple[str, str, str]],
    active_i: int,
    feature_id: str,
    *,
    level_resolve: Callable[[str, str, str], str] | None = None,
) -> bool:
    """True when the player pipeline is healthy and the active delegation device supports this feature."""
    if incompatible or not pipeline_ok or not ordered:
        return False
    if active_i < 0 or active_i >= len(ordered):
        return False
    _lab, cap_id, sk = ordered[active_i]

    def _lv(cap: str, fid: str, s: str) -> str:
        return level_resolve(cap, fid, s) if level_resolve else level_for(cap, fid)

    return _lv(cap_id, feature_id, sk) != "none"


def _run_refresh_spin(
    top: tk.Misc,
    label: tk.Label,
    base_rgba: "Image.Image",
    *,
    busy: dict[str, bool],
    on_done_spin: Callable[[], None] | None = None,
) -> None:
    """Rotate the refresh icon 360° over ``_REFRESH_SPIN_MS`` ms (PIL); ``busy`` guards re-entry."""
    if Image is None or ImageTk is None:
        busy["spin"] = False
        if on_done_spin:
            on_done_spin()
        return
    step = [0]

    def tick() -> None:
        if step[0] > _REFRESH_SPIN_STEPS:
            ph = ImageTk.PhotoImage(base_rgba)
            label.configure(image=ph)
            label.image = ph  # noqa: SLF001 — keep reference
            busy["spin"] = False
            if on_done_spin:
                on_done_spin()
            return
        ang = 360.0 * step[0] / float(_REFRESH_SPIN_STEPS)
        _bicubic = getattr(Image, "Resampling", Image).BICUBIC
        rotated = base_rgba.rotate(-ang, resample=_bicubic, expand=False)
        ph = ImageTk.PhotoImage(rotated)
        label.configure(image=ph)
        label.image = ph  # noqa: SLF001
        step[0] += 1
        delay = max(1, _REFRESH_SPIN_MS // _REFRESH_SPIN_STEPS)
        try:
            top.after(delay, tick)
        except tk.TclError:
            busy["spin"] = False

    tick()


def open_advanced_capability_matrix(
    parent: tk.Misc,
    *,
    tmdb_manual_fetch: Callable[[], None] | None = None,
    tmdb_report_failure: Callable[[], None] | None = None,
    tmdb_read_log_tail: Callable[[int], list[str]] | None = None,
    tmdb_register_widgets: Callable[[tk.Button, tk.Button, tk.Misc], None] | None = None,
    tmdb_unregister_widgets: Callable[[], None] | None = None,
    prepend_hotkey_bindtag: Callable[[tk.Misc], None] | None = None,
    tmdb_quality_stats_read: Callable[[], dict[str, int]] | None = None,
    playback_content_ok: Callable[[], bool] | None = None,
    on_closed: Callable[[], None] | None = None,
    close_skip_once: list[bool] | None = None,
    feature_force_try: Callable[[str], None] | None = None,
) -> None:
    _has_tmdb = (
        tmdb_manual_fetch is not None
        and tmdb_report_failure is not None
        and tmdb_read_log_tail is not None
        and tmdb_register_widgets is not None
        and tmdb_unregister_widgets is not None
    )

    top = tk.Toplevel(parent)
    top.title("Pigeon — Advanced · Feature delegation")
    top.configure(bg="#111")
    try:
        top.transient(parent.winfo_toplevel())
        top.geometry("1100x760")
    except tk.TclError:
        pass

    def _on_top_destroy(event: tk.Event) -> None:
        if getattr(event, "widget", None) is not top:
            return
        if _has_tmdb:
            tmdb_unregister_widgets()
        if close_skip_once is not None and close_skip_once[0]:
            close_skip_once[0] = False
            return
        if on_closed is not None:
            try:
                on_closed()
            except Exception:
                pass

    top.bind("<Destroy>", _on_top_destroy, add="+")

    header = tk.Frame(top, bg="#111")
    header.pack(fill=tk.X, padx=14, pady=(14, 8))
    tk.Label(
        header,
        text="Feature delegation",
        fg="#f5f5f5",
        bg="#111",
        font=("Helvetica", 16, "bold"),
    ).pack(side=tk.LEFT, anchor=tk.W)

    devices = active_device_columns()  # (label, cap_id, stable_key)

    def _add_tmdb_section(container: tk.Misc, *, pady_top: int = 10) -> None:
        if not _has_tmdb:
            return
        assert tmdb_manual_fetch is not None
        assert tmdb_report_failure is not None
        assert tmdb_read_log_tail is not None
        assert tmdb_register_widgets is not None
        section = tk.Frame(container, bg="#111")
        tk.Label(
            section,
            text="TMDb",
            fg="#ccc",
            bg="#111",
            font=("Helvetica", 12, "bold"),
        ).pack(anchor=tk.W)
        # Live counters + success rate moved to Settings → Content (beside Purge Image Media).
        if tmdb_quality_stats_read is not None:
            tk.Label(
                section,
                text=(
                    "Match-quality totals and success rate are on Settings → Content, to the right of "
                    "“Purge Image Media”. While artwork/title is wrong, press ⌘⇧X (macOS) or Ctrl+Shift+X — "
                    "the next successful TMDb populate scores fail/success; each event also appends to "
                    "~/.pigeon_0_6/tmdb_quality_event_reports.log."
                ),
                fg="#888",
                bg="#111",
                font=("Helvetica", 10),
                wraplength=920,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(0, 6))
        btn_row = tk.Frame(section, bg="#111")
        btn_row.pack(anchor=tk.W, pady=(4, 6))
        manual_b = tk.Button(
            btn_row,
            text="Manual Fetch",
            command=tmdb_manual_fetch,
            font=("Helvetica", 11),
            padx=8,
            pady=4,
        )
        manual_b.pack(side=tk.LEFT, padx=(0, 8))
        report_b = tk.Button(
            btn_row,
            text="Report Failure",
            command=tmdb_report_failure,
            font=("Helvetica", 11),
            padx=8,
            pady=4,
        )
        report_b.pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(
            section,
            text="TMDb retry log",
            fg="#888",
            bg="#111",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor=tk.W, pady=(0, 3))
        _mono = ("Menlo", 10) if sys.platform == "darwin" else ("Consolas", 10)
        log_w = scrolledtext.ScrolledText(
            section,
            height=5,
            width=80,
            wrap=tk.WORD,
            bg="#1a1a1e",
            fg="#e8e8e8",
            insertbackground="#e8e8e8",
            highlightthickness=1,
            highlightbackground="#333",
            font=_mono,
        )
        log_w.pack(anchor=tk.W, fill=tk.BOTH, expand=True)
        for _raw in tmdb_read_log_tail(100):
            log_w.insert(tk.END, _raw + "\n")
        tmdb_register_widgets(manual_b, report_b, log_w)
        if prepend_hotkey_bindtag is not None:
            prepend_hotkey_bindtag(section)
            prepend_hotkey_bindtag(log_w)
        section.pack(fill=tk.X, pady=(pady_top, 4))

    btn_bar = tk.Frame(header, bg="#111")
    btn_bar.pack(side=tk.RIGHT)
    tk.Button(
        btn_bar,
        text="Close",
        command=top.destroy,
        font=("Helvetica", 11),
        padx=12,
        pady=4,
    ).pack(side=tk.RIGHT)

    if not devices:
        tk.Label(
            top,
            text="No saved devices for the current location.",
            fg="#e8e8e8",
            bg="#111",
            font=("Helvetica", 12),
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=14, pady=(8, 4))
        tk.Label(
            top,
            text="Use Find device to add devices for this location, then open Advanced again.",
            fg="#888",
            bg="#111",
            font=("Helvetica", 10),
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=14, pady=(0, 12))
        _add_tmdb_section(top, pady_top=4)
        try:
            top.grab_set()
        except tk.TclError:
            pass
        top.focus_set()
        return

    tk.Label(
        top,
        text="Each feature is on its own block. The large circle is the capability traffic light (green / amber / red); "
        "the tile border uses the same color. A thicker border marks the slot Pigeon last attempted for that feature. "
        "Indicators now always map by detection status + compatibility (green/yellow/red). "
        "Small figures under the name: try order 1, 2, 3\u2026 (1 = first to try); \u2605 beside the number on Pigeon\u2019s auto pick. "
        "Manual reorder changes try order, not which device earns the star. Poll outcomes append to the log. "
        "When playback metadata is live, the feature title turns green and underlined if Pigeon considers that capability working "
        "for the active delegation device. Tile colors use the static matrix capped by live player-poll hints when available "
        "(whichever is more conservative). Tap a tile for arrows to reorder. Grey rows have no compatible saved devices.",
        fg="#888",
        bg="#111",
        font=("Helvetica", 10),
        wraplength=1040,
        justify=tk.LEFT,
    ).pack(anchor=tk.W, padx=14, pady=(0, 4))

    tk.Label(
        top,
        text="Legend: large ● / tile border colors   |   green = detected + compatible   |   yellow = detected + unknown/incompatible   |   red = not detected or not compatible   |   \u2605 = auto pick   |   1\u2026n = try order   |   thick border = last attempt   |   green underlined title = live for active device",
        fg="#666",
        bg="#111",
        font=("Helvetica", 9),
        wraplength=1040,
        justify=tk.LEFT,
    ).pack(anchor=tk.W, padx=14, pady=(0, 10))

    loc_id = ""
    overrides: dict[str, list[str]] = {}
    delegation_log: dict[str, list[str]] = {}
    active_indices: dict[str, int] = {}
    try:
        from pigeon.app_state import (
            clear_feature_delegation_overrides,
            read_current_location_id,
            read_delegation_active_indices,
            read_delegation_log,
            read_feature_delegation_overrides,
            write_feature_delegation_overrides,
        )

        loc_id = read_current_location_id()
        overrides = read_feature_delegation_overrides(loc_id)
        delegation_log = read_delegation_log(loc_id)
        active_indices = read_delegation_active_indices(loc_id)
    except Exception:
        loc_id = ""
        overrides = {}
        delegation_log = {}
        active_indices = {}

    def _effective_level(cap: str, fid: str, sk: str) -> str:
        if not loc_id:
            return level_for(cap, fid)
        try:
            from pigeon.observed_capability import effective_level_for

            return effective_level_for(loc_id, cap, fid, sk)
        except Exception:
            return level_for(cap, fid)

    def _has_observed_feature(stable_key: str, feature_id: str) -> bool:
        """True when this feature has live observed data for this device in the active location."""
        if not loc_id:
            return False
        try:
            from pigeon.app_state import read_app_state
        except Exception:
            return False
        try:
            raw = read_app_state()
            blob = raw.get(f"observed_capability_live_v1.{loc_id}")
            if not isinstance(blob, dict):
                return False
            dev_map = blob.get(str(stable_key))
            if not isinstance(dev_map, dict):
                return False
            return isinstance(dev_map.get(str(feature_id)), str)
        except Exception:
            return False

    slots_n = max(1, len(devices))
    track_w = slots_n * _TILE_SIZE + (slots_n - 1) * _SLOT_GAP + 14
    track_h = _TILE_SIZE + 14
    slot_xs = [7 + i * (_TILE_SIZE + _SLOT_GAP) for i in range(slots_n)]

    outer = tk.Frame(top, bg="#111")
    outer.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))

    scroll_holder = tk.Frame(outer, bg="#111")
    scroll_holder.pack(fill=tk.BOTH, expand=True)

    scroll = tk.Canvas(scroll_holder, bg="#111", highlightthickness=0, bd=0)
    vsb = tk.Scrollbar(scroll_holder, orient=tk.VERTICAL, command=scroll.yview, bg="#2a2a2e")
    scroll.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    scroll.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    inner = tk.Frame(scroll, bg="#111")
    inner_id = scroll.create_window((0, 0), window=inner, anchor=tk.NW)

    def _scroll_inner() -> None:
        scroll.update_idletasks()
        bbox = scroll.bbox("all")
        if bbox:
            scroll.configure(scrollregion=bbox)

    def _on_canvas_configure(event: tk.Event) -> None:
        try:
            scroll.itemconfigure(inner_id, width=max(event.width - 8, 400))
        except tk.TclError:
            pass
        top.after_idle(_scroll_inner)

    scroll.bind("<Configure>", _on_canvas_configure)
    inner.bind("<Configure>", lambda _e: _scroll_inner())

    def _wheel_target_should_ignore(widget: tk.Misc) -> bool:
        """Let Listbox/Entry/TMDb ScrolledText keep wheel; delegation logs use canvas + buttons."""
        try:
            w: tk.Misc | None = widget
            while w is not None:
                cls = w.winfo_class()
                if cls in ("Listbox", "Entry", "TEntry", "TCombobox"):
                    return True
                # ScrolledText outer widget is a Frame; inner Text is a child — treat as TMDb log.
                if cls == "Text":
                    p = getattr(w, "master", None)
                    if isinstance(p, tk.Misc) and p.winfo_class() == "Frame":
                        try:
                            for ch in p.winfo_children():
                                if isinstance(ch, tk.Misc) and ch.winfo_class() == "Scrollbar":
                                    return True
                        except tk.TclError:
                            pass
                    return False
                m = getattr(w, "master", None)
                w = m if isinstance(m, tk.Misc) else None
        except tk.TclError:
            pass
        return False

    def _scroll_advanced_canvas(event: tk.Event) -> None:
        try:
            if sys.platform == "darwin":
                d = int(getattr(event, "delta", 0) or 0)
                if d == 0:
                    return
                steps = max(1, abs(d) // 120) if abs(d) >= 120 else 1
                scroll.yview_scroll(-steps if d > 0 else steps, "units")
            else:
                num = int(getattr(event, "num", 0) or 0)
                if num == 4:
                    scroll.yview_scroll(-3, "units")
                elif num == 5:
                    scroll.yview_scroll(3, "units")
        except tk.TclError:
            pass

    def _on_advanced_mousewheel(event: tk.Event) -> str | None:
        try:
            if _wheel_target_should_ignore(event.widget):
                return None
            _scroll_advanced_canvas(event)
        except tk.TclError:
            pass
        return "break"

    def _delegation_log_wheel(event: tk.Event) -> str:
        """Scroll main Advanced canvas; do not scroll the small debug Text (use ▲ ▼)."""
        _scroll_advanced_canvas(event)
        return "break"

    # Toplevel binding runs in bindtags before the root bind_all handler, so this
    # window captures the wheel instead of scrolling main Settings.
    top.bind("<MouseWheel>", _on_advanced_mousewheel)
    top.bind("<Button-4>", _on_advanced_mousewheel)
    top.bind("<Button-5>", _on_advanced_mousewheel)

    try:
        _title_fam, _title_sz = _FEAT_NAME_FONT[0], int(_FEAT_NAME_FONT[1])
        _title_wt = str(_FEAT_NAME_FONT[2]).lower() if len(_FEAT_NAME_FONT) > 2 else "normal"
    except (TypeError, ValueError, IndexError):
        _title_fam, _title_sz, _title_wt = "Helvetica", 20, "bold"
    _title_working_font = tkfont.Font(
        top, family=_title_fam, size=_title_sz, weight=_title_wt, underline=True
    )

    title_refreshers: list[Callable[[], None]] = []

    def build_row(feature_label: str, feature_id: str) -> None:
        devs_now = list(devices)
        # Static matrix only: live poll hints must not grey out the whole row (e.g. Roku / non-pyatv
        # players where polls fail and would otherwise imply "no compatible devices").
        incompatible = _feature_fully_incompatible(feature_id, devs_now)

        auto = _auto_order_for_feature(devs_now, feature_id)
        key_to_dev = {sk: (lab, cap, sk) for lab, cap, sk in devs_now}

        def _resolve_override_key(k_raw: str) -> tuple[str, str, str] | None:
            k = str(k_raw or "").strip()
            if not k:
                return None
            hit = key_to_dev.get(k)
            if hit:
                return hit
            for lab, cap, sk in devs_now:
                if _legacy_override_key(lab, cap) == k:
                    return (lab, cap, sk)
            return None

        ordered: list[tuple[str, str, str]] = []
        if isinstance(overrides.get(feature_id), list):
            for k in overrides.get(feature_id) or []:
                d = _resolve_override_key(str(k))
                if d and d not in ordered:
                    ordered.append(d)
        for d in auto:
            if d not in ordered:
                ordered.append(d)
        ordered = ordered[: len(devs_now)]

        block_bg = "#161618" if incompatible else "#111"
        block = tk.Frame(inner, bg=block_bg)
        block.pack(fill=tk.X, pady=(12, 16))

        title_fg = "#4a4a52" if incompatible else "#ddd"
        title_text = feature_label + ("  (no compatible devices)" if incompatible else "")
        title_row = tk.Frame(block, bg=block_bg)
        title_row.pack(fill=tk.X, anchor=tk.W, padx=(2, 0), pady=(0, 6))
        title_lbl = tk.Label(
            title_row,
            text=title_text,
            fg=title_fg,
            bg=block_bg,
            font=_FEAT_NAME_FONT,
            anchor=tk.W,
            justify=tk.LEFT,
        )
        title_lbl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, anchor=tk.W)

        if feature_force_try is not None:
            if refresh_base_img is not None:
                busy_spin = {"spin": False}
                ref_src = refresh_base_img.copy()
                ph_r = ImageTk.PhotoImage(ref_src)
                ref_lbl = tk.Label(
                    title_row,
                    image=ph_r,
                    bg=block_bg,
                    cursor="hand2",
                    bd=0,
                    highlightthickness=0,
                )
                ref_lbl.image = ph_r  # noqa: SLF001

                def _on_ref_click(
                    _event: tk.Event,
                    *,
                    _fid: str = feature_id,
                    _lbl: tk.Label = ref_lbl,
                    _base=ref_src,
                    _busy: dict[str, bool] = busy_spin,
                ) -> None:
                    if _busy["spin"]:
                        return
                    _busy["spin"] = True
                    try:
                        feature_force_try(_fid)
                    except Exception:
                        pass
                    _run_refresh_spin(top, _lbl, _base, busy=_busy, on_done_spin=None)

                ref_lbl.bind("<Button-1>", _on_ref_click)
                ref_lbl.pack(side=tk.RIGHT, padx=(12, 6), anchor=tk.E)
            else:
                tk.Button(
                    title_row,
                    text="\u21bb",
                    font=("Helvetica", 16, "bold"),
                    fg="#b0b0b8",
                    bg=block_bg,
                    activebackground=block_bg,
                    activeforeground="#fff",
                    bd=0,
                    highlightthickness=0,
                    cursor="hand2",
                    padx=6,
                    pady=0,
                    command=lambda fid=feature_id: feature_force_try(fid),
                ).pack(side=tk.RIGHT, padx=(12, 6), anchor=tk.E)

        def refresh_feature_title() -> None:
            try:
                live_ok = bool(playback_content_ok()) if playback_content_ok else False
            except Exception:
                live_ok = False
            try:
                from pigeon.app_state import read_delegation_active_indices as _read_active_i

                ai_map = _read_active_i(loc_id) if loc_id else {}
            except Exception:
                ai_map = active_indices
            ai = int(ai_map.get(feature_id, 0) or 0)
            if ai < 0 or ai >= len(ordered):
                ai = 0
            works = _feature_row_playback_working(
                live_ok, incompatible, ordered, ai, feature_id, level_resolve=_effective_level
            )
            try:
                if works:
                    title_lbl.configure(fg=_LED_FULL, font=_title_working_font)
                else:
                    title_lbl.configure(fg=title_fg, font=_FEAT_NAME_FONT)
            except tk.TclError:
                pass
            try:
                render_tiles()
            except Exception:
                pass

        title_refreshers.append(refresh_feature_title)
        top.after_idle(refresh_feature_title)

        body = tk.Frame(block, bg=block_bg)
        body.pack(fill=tk.X, pady=(2, 0))

        track_bg = "#0d0d10" if incompatible else _TRACK_BG
        cv = tk.Canvas(
            body,
            width=track_w,
            height=track_h,
            bg=track_bg,
            highlightthickness=0,
            bd=0,
        )
        cv.pack(side=tk.LEFT, anchor=tk.N)

        log_fr = tk.Frame(body, bg="#1a1a1f" if not incompatible else "#141416")
        log_fr.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(14, 0))
        log_lines = delegation_log.get(feature_id, [])[-14:]
        log_body = "\n".join(log_lines) if log_lines else "—"
        log_fg = "#4a5058" if incompatible else "#9ab"
        log_row = tk.Frame(log_fr, bg=log_fr.cget("bg"))
        log_row.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        log_tx = tk.Text(
            log_row,
            height=8,
            width=44,
            bg="#141416" if incompatible else "#1a1a1f",
            fg=log_fg,
            font=("Menlo", 9),
            highlightthickness=1,
            highlightbackground="#333",
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        log_tx.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_tx.configure(state=tk.NORMAL)
        log_tx.insert(tk.END, log_body)
        log_tx.configure(state=tk.DISABLED)

        _nav_bg = "#2a2a32"
        _nav_fg = "#e0e0e0"
        log_nav = tk.Frame(log_row, bg=log_fr.cget("bg"))
        log_nav.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))

        def _log_scroll_units(delta: int) -> None:
            try:
                log_tx.yview_scroll(delta, "units")
            except tk.TclError:
                pass

        tk.Label(
            log_nav,
            text="Log",
            fg="#666",
            bg=log_fr.cget("bg"),
            font=("Helvetica", 8),
        ).pack(pady=(0, 4))
        tk.Button(
            log_nav,
            text="\u25b2",
            width=2,
            font=("Helvetica", 10, "bold"),
            bg=_nav_bg,
            fg=_nav_fg,
            activebackground="#3a3a44",
            activeforeground=_nav_fg,
            highlightthickness=0,
            command=lambda: _log_scroll_units(-3),
        ).pack(pady=(0, 2))
        tk.Button(
            log_nav,
            text="\u25bc",
            width=2,
            font=("Helvetica", 10, "bold"),
            bg=_nav_bg,
            fg=_nav_fg,
            activebackground="#3a3a44",
            activeforeground=_nav_fg,
            highlightthickness=0,
            command=lambda: _log_scroll_units(3),
        ).pack()

        log_tx.bind("<MouseWheel>", _delegation_log_wheel)
        log_tx.bind("<Button-4>", _delegation_log_wheel)
        log_tx.bind("<Button-5>", _delegation_log_wheel)

        sel_tile: dict[str, int | None] = {"i": None}

        auto_pick_sk = str(auto[0][2]) if auto else ""

        slot_y = 6

        def save_override() -> None:
            if not loc_id or incompatible:
                return
            ov = dict(overrides)
            ov[feature_id] = [t[2] for t in ordered]
            overrides.clear()
            overrides.update(ov)
            try:
                write_feature_delegation_overrides(loc_id, overrides)
            except Exception:
                pass

        def render_tiles() -> None:
            cv.delete("tile")
            cv.delete("arrow_ui")

            try:
                from pigeon.app_state import read_delegation_active_indices as _rai

                _amap = _rai(loc_id) if loc_id else {}
                ai_live = int(_amap.get(feature_id, 0) or 0)
            except Exception:
                ai_live = int(active_indices.get(feature_id, 0) or 0)
            if ai_live < 0 or ai_live >= len(ordered):
                ai_live = 0

            for i, (dlabel, cap_id, sk) in enumerate(ordered):
                sx = slot_xs[i]
                sk_s = str(sk)
                lvl = _effective_level(cap_id, feature_id, sk_s)
                has_obs = _has_observed_feature(sk_s, feature_id)
                # Requested semantics:
                # - green: detected + compatible
                # - yellow: detected + unknown/incompatible
                # - red: not detected or not compatible
                if has_obs:
                    fill = _LED_FULL if lvl == "full" else _LED_PARTIAL
                else:
                    fill = _LED_NONE
                if incompatible:
                    fill = "#3a3a40"
                is_active_attempt = (i == ai_live) and not incompatible
                if incompatible:
                    tile_bg = "#25252c"
                    box_outline = "#45454c"
                    ow = 1
                else:
                    if is_active_attempt:
                        tile_bg = _TILE_ACTIVE_FILL
                        ow = _TILE_OUTLINE_WIDTH_ACTIVE
                    else:
                        tile_bg = _TILE_BG
                        ow = _TILE_OUTLINE_WIDTH
                    # Box outline matches traffic-light color (full / partial / none).
                    box_outline = fill

                cv.create_rectangle(
                    sx,
                    slot_y,
                    sx + _TILE_SIZE,
                    slot_y + _TILE_SIZE,
                    fill=tile_bg,
                    outline=box_outline,
                    width=ow,
                    tags=("tile", f"tile{i}"),
                )

                cx = sx + _TILE_SIZE // 2
                led_cy = slot_y + _LED_TOP_PAD + _LED_RADIUS
                cv.create_oval(
                    cx - _LED_RADIUS,
                    led_cy - _LED_RADIUS,
                    cx + _LED_RADIUS,
                    led_cy + _LED_RADIUS,
                    fill=fill,
                    outline=fill,
                    width=2,
                    tags=("tile", f"tile{i}"),
                )
                txt = dlabel.strip() or cap_id
                if len(txt) > 12:
                    txt = txt[:11] + "\u2026"
                name_cy = slot_y + _NAME_CY_OFFSET
                name_fg = "#6a6a72" if incompatible else _TRACK_FG
                cv.create_text(
                    cx,
                    name_cy,
                    text=txt,
                    fill=name_fg,
                    font=_NAME_IN_TILE_FONT,
                    tags=("tile", f"tile{i}"),
                )

                ind_cy = slot_y + _BADGE_CY_OFFSET
                num_fill = "#6a6a72" if incompatible else "#a8a8b0"
                star_fill = "#5a5a60" if incompatible else _ACTIVE_OUTLINE
                # Small ★ + try-order under the name (large cue is the LED above).
                if auto_pick_sk and sk_s == auto_pick_sk:
                    cv.create_text(
                        cx - 14,
                        ind_cy,
                        text=_PRIORITY_STAR,
                        anchor=tk.CENTER,
                        fill=star_fill,
                        font=_BADGE_FONT,
                        tags=("tile", f"tile{i}"),
                    )
                    cv.create_text(
                        cx + 14,
                        ind_cy,
                        text=str(i + 1),
                        anchor=tk.CENTER,
                        fill=num_fill,
                        font=_BADGE_FONT,
                        tags=("tile", f"tile{i}"),
                    )
                else:
                    cv.create_text(
                        cx,
                        ind_cy,
                        text=str(i + 1),
                        anchor=tk.CENTER,
                        fill=num_fill,
                        font=_BADGE_FONT,
                        tags=("tile", f"tile{i}"),
                    )

            if incompatible:
                return

            si = sel_tile["i"]
            if si is not None and 0 <= si < len(ordered):

                def move_delta(delta: int) -> None:
                    j = int(sel_tile["i"] if sel_tile["i"] is not None else -1)
                    if j < 0:
                        return
                    nj = j + delta
                    if nj < 0 or nj >= len(ordered):
                        return
                    ordered[j], ordered[nj] = ordered[nj], ordered[j]
                    sel_tile["i"] = nj
                    save_override()
                    render_tiles()

                ax = slot_xs[si]
                if si > 0:
                    bf = tk.Frame(cv, bg=track_bg)
                    tk.Button(
                        bf,
                        text="\u2190",
                        font=("Helvetica", 11, "bold"),
                        width=2,
                        bg="#2a2a32",
                        fg="#000000",
                        activeforeground="#000000",
                        activebackground="#3a3a44",
                        highlightthickness=0,
                        command=lambda: move_delta(-1),
                    ).pack()
                    cv.create_window(ax - 2, slot_y + 2, anchor=tk.NW, window=bf, tags=("arrow_ui",))
                if si < len(ordered) - 1:
                    bf2 = tk.Frame(cv, bg=track_bg)
                    tk.Button(
                        bf2,
                        text="\u2192",
                        font=("Helvetica", 11, "bold"),
                        width=2,
                        bg="#2a2a32",
                        fg="#000000",
                        activeforeground="#000000",
                        activebackground="#3a3a44",
                        highlightthickness=0,
                        command=lambda: move_delta(1),
                    ).pack()
                    cv.create_window(ax + _TILE_SIZE - 28, slot_y + 2, anchor=tk.NW, window=bf2, tags=("arrow_ui",))

        def on_press(event: tk.Event) -> None:
            if incompatible:
                return
            item = cv.find_withtag("current")
            if not item:
                return
            tags = cv.gettags(item[0])
            idx = None
            for t in tags:
                if t.startswith("tile") and t[4:].isdigit():
                    idx = int(t[4:])
                    break
            if idx is None or idx < 0 or idx >= len(ordered):
                return
            if sel_tile["i"] == idx:
                sel_tile["i"] = None
            else:
                sel_tile["i"] = idx
            render_tiles()

        cv.tag_bind("tile", "<ButtonPress-1>", on_press)
        render_tiles()

    refresh_base_img = None
    if Image is not None and ImageTk is not None and _REFRESH_ICON_PATH.is_file():
        try:
            _lanczos = getattr(Image, "Resampling", Image).LANCZOS
            _im = Image.open(_REFRESH_ICON_PATH).convert("RGBA")
            refresh_base_img = _im.resize(
                (_REFRESH_DISPLAY_PX, _REFRESH_DISPLAY_PX),
                _lanczos,
            )
        except Exception:
            refresh_base_img = None

    for flabel, fid in FEATURES:
        build_row(flabel, fid)

    def _tick_advanced_titles() -> None:
        try:
            if not int(top.winfo_exists()):
                return
        except tk.TclError:
            return
        for _fn in title_refreshers:
            try:
                _fn()
            except tk.TclError:
                pass
        try:
            top.after(750, _tick_advanced_titles)
        except tk.TclError:
            pass

    if title_refreshers:
        top.after(750, _tick_advanced_titles)

    _add_tmdb_section(outer, pady_top=8)

    footer = tk.Frame(top, bg="#111")
    footer.pack(fill=tk.X, padx=14, pady=(0, 14))

    def reopen_advanced() -> None:
        open_advanced_capability_matrix(
            parent,
            tmdb_manual_fetch=tmdb_manual_fetch,
            tmdb_report_failure=tmdb_report_failure,
            tmdb_read_log_tail=tmdb_read_log_tail,
            tmdb_register_widgets=tmdb_register_widgets,
            tmdb_unregister_widgets=tmdb_unregister_widgets,
            prepend_hotkey_bindtag=prepend_hotkey_bindtag,
            playback_content_ok=playback_content_ok,
            on_closed=on_closed,
            close_skip_once=close_skip_once,
            feature_force_try=feature_force_try,
        )

    def on_restore_auto() -> None:
        nonlocal overrides
        if loc_id:
            try:
                clear_feature_delegation_overrides(loc_id)
            except Exception:
                pass
        overrides = {}
        if close_skip_once is not None:
            close_skip_once[0] = True
        try:
            top.destroy()
        except tk.TclError:
            return
        parent.after(1, reopen_advanced)

    tk.Button(
        footer,
        text="Restore Auto-Selections",
        command=on_restore_auto,
        font=("Helvetica", 11),
        padx=12,
        pady=6,
    ).pack(side=tk.RIGHT)

    try:
        top.grab_set()
    except tk.TclError:
        pass

    top.focus_set()
