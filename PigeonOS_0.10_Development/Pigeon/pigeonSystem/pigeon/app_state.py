"""Persisted app state (~/.pigeon_0_6/state.json). Shared by UI and image pipeline (no Tk)."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

from pigeon.runtime_paths import pigeon_state_dir


def state_file() -> Path:
    return pigeon_state_dir() / "state.json"


# (mtime_ns, size, raw_text) — read_app_state is called from poll/render hot
# paths (~60 call sites); stat-validated text cache skips the disk read+decode.
_STATE_TEXT_CACHE: tuple[int, int, str] | None = None


def read_app_state() -> dict[str, Any]:
    global _STATE_TEXT_CACHE
    p = state_file()
    try:
        st = p.stat()
    except OSError:
        _STATE_TEXT_CACHE = None
        return {}
    sig = (st.st_mtime_ns, st.st_size)
    cached = _STATE_TEXT_CACHE
    if cached is not None and (cached[0], cached[1]) == sig:
        text = cached[2]
    else:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            _STATE_TEXT_CACHE = None
            return {}
        _STATE_TEXT_CACHE = (sig[0], sig[1], text)
    try:
        # Parse per call so every caller gets private objects (safe to mutate).
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_write_state(cur: dict[str, Any]) -> None:
    global _STATE_TEXT_CACHE
    try:
        p = state_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
        _STATE_TEXT_CACHE = None
    except Exception:
        pass


def write_app_state(**updates: Any) -> None:
    """Merge updates into existing JSON and atomically replace the file."""
    try:
        cur = read_app_state()
        if not cur:
            # Empty merge base but a sizable file on disk means the read failed
            # (corrupt/partial JSON). Keep a recovery copy before overwriting so
            # one bad read cannot silently wipe pairings and settings.
            try:
                p = state_file()
                if p.is_file() and p.stat().st_size > 2:
                    p.replace(p.with_suffix(".bad"))
            except OSError:
                pass
        cur.update(updates)
        _atomic_write_state(cur)
    except Exception:
        pass


def pop_app_state_key_prefix(prefix: str) -> None:
    """Remove top-level keys starting with ``prefix`` (e.g. per-location observed capability keys)."""
    if not prefix:
        return
    try:
        cur = read_app_state()
        rm = [k for k in list(cur.keys()) if isinstance(k, str) and k.startswith(prefix)]
        if not rm:
            return
        for k in rm:
            del cur[k]
        _atomic_write_state(cur)
    except Exception:
        pass


def pop_app_state_keys(*keys: str) -> None:
    """Remove specific top-level keys if present."""
    if not keys:
        return
    try:
        cur = read_app_state()
        changed = False
        for k in keys:
            if k in cur:
                del cur[k]
                changed = True
        if changed:
            _atomic_write_state(cur)
    except Exception:
        pass


def auto_delete_pulled_media() -> bool:
    v = read_app_state().get("auto_delete_pulled_media")
    return bool(v) if isinstance(v, bool) else False


def read_roku_ecp_base_url() -> str:
    """Optional manual Roku ECP base (``http://ip:8060``) in state.json for TVs / Roku players."""
    v = read_app_state().get("roku_ecp_base_url")
    return str(v).strip() if isinstance(v, str) and v.strip() else ""


def write_roku_ecp_base_url(url: str | None) -> None:
    u = str(url or "").strip()
    write_app_state(roku_ecp_base_url=u)


def read_last_apple_tv() -> dict[str, str]:
    v = read_app_state().get("last_apple_tv")
    if not isinstance(v, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("identifier", "address", "name", "label"):
        value = v.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def write_last_apple_tv(
    *,
    identifier: str,
    address: str,
    name: str | None = None,
    label: str | None = None,
) -> None:
    payload = {
        "identifier": identifier.strip(),
        "address": address.strip(),
    }
    if isinstance(name, str) and name.strip():
        payload["name"] = name.strip()
    if isinstance(label, str) and label.strip():
        payload["label"] = label.strip()
    write_app_state(last_apple_tv=payload)


def clear_last_apple_tv() -> None:
    write_app_state(last_apple_tv={})


def filter_discovery_for_streaming(rows: list[dict[str, object]]) -> list[dict[str, str]]:
    """Discovery rows suitable for pyatv now-playing (Apple TV / MRP / Companion)."""
    return [dict(r) for r in rows if row_is_playback_apple_tv(r)]


def filter_discovery_for_receiver(rows: list[dict[str, object]]) -> list[dict[str, str]]:
    """Discovery rows that are AirPlay-only / non-tvOS (typical AVR advertisement)."""
    return [dict(r) for r in rows if row_is_av_receiver_candidate(r)]


_NON_AV_NAME_MARKERS: tuple[str, ...] = (
    "printer",
    "bridge",
    "bulb",
    "light",
    "thermostat",
    "camera",
    "sensor",
    "lock",
    "doorbell",
    "hub",
    "router",
    "nas",
    "server",
    "macbook",
    "mac mini",
    "imac",
    "iphone",
    "ipad",
    "homepod mini",
)


def row_is_av_receiver_candidate(row: dict[str, object]) -> bool:
    """True for likely AV receivers / speakers; excludes tvOS players and obvious non-AV gear."""
    if row_is_playback_apple_tv(row):
        return False
    name = str(row.get("name") or row.get("label") or "").strip().lower()
    if any(marker in name for marker in _NON_AV_NAME_MARKERS):
        return False
    services = str(row.get("services") or "").lower()
    if services and "airplay" not in services and "protocol.airplay" not in services:
        return False
    return True


def row_is_playback_apple_tv(row: dict[str, object]) -> bool:
    """
    True if this saved row should drive pyatv now-playing (tvOS / MRP / Companion).

    False for Denon-style receivers (``denon:`` id), persisted ``looks_like_apple_tv=false``,
    or labels that show AirPlay-only discovery.
    """
    ident = str(row.get("identifier") or "").strip()
    if ident.startswith("denon:"):
        return False
    lk = str(row.get("looks_like_apple_tv") or "").strip().lower()
    if lk in ("true", "1", "yes"):
        return True
    if lk in ("false", "0", "no"):
        return False
    lab = str(row.get("label") or "")
    if "Apple TV / tvOS" in lab:
        return True
    if "AirPlay / other" in lab:
        return False
    return True


def _parse_one_saved_device_row(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    row = {k: str(item.get(k, "")).strip() for k in ("identifier", "address", "name", "label")}
    if not row["identifier"] or not row["address"]:
        return None
    if not row["label"]:
        nm = row["name"] or "Device"
        row["label"] = f"{nm} — {row['address']}"
    lk = item.get("looks_like_apple_tv")
    if isinstance(lk, bool):
        row["looks_like_apple_tv"] = "true" if lk else "false"
    elif isinstance(lk, str) and lk.strip().lower() in ("true", "false"):
        row["looks_like_apple_tv"] = lk.strip().lower()
    return row


def _slot_row_to_state_dict(row: dict[str, str] | None) -> dict[str, Any]:
    if not row:
        return {}
    out: dict[str, Any] = {
        "identifier": str(row.get("identifier", "")).strip(),
        "address": str(row.get("address", "")).strip(),
        "name": str(row.get("name", "")).strip(),
        "label": str(row.get("label", "")).strip(),
    }
    lk = row.get("looks_like_apple_tv")
    if isinstance(lk, str) and lk.strip().lower() in ("true", "false"):
        out["looks_like_apple_tv"] = lk.strip().lower()
    return out


def migrate_device_slots_from_legacy_if_needed() -> None:
    """
    One-time: split legacy ``saved_apple_tv_devices`` into ``saved_streaming_device`` and
    ``saved_av_receiver`` (at most one each), then clear the legacy list.
    """
    cur = read_app_state()
    if cur.get("device_slots_v1_migrated") is True:
        return
    legacy_rows: list[dict[str, str]] = []
    raw_legacy = cur.get("saved_apple_tv_devices")
    if isinstance(raw_legacy, list):
        for item in raw_legacy:
            parsed = _parse_one_saved_device_row(item)
            if parsed is not None:
                legacy_rows.append(parsed)

    stream = _parse_one_saved_device_row(cur.get("saved_streaming_device"))
    avr = _parse_one_saved_device_row(cur.get("saved_av_receiver"))

    for row in legacy_rows:
        if stream is None and row_is_playback_apple_tv(row):
            stream = dict(row)
        elif avr is None and not row_is_playback_apple_tv(row):
            avr = dict(row)
        if stream is not None and avr is not None:
            break

    write_app_state(
        device_slots_v1_migrated=True,
        saved_apple_tv_devices=[],
        saved_streaming_device=_slot_row_to_state_dict(stream),
        saved_av_receiver=_slot_row_to_state_dict(avr),
    )


def read_saved_streaming_device() -> dict[str, str] | None:
    migrate_device_slots_from_legacy_if_needed()
    return _v2_read_primary_streaming()


def write_saved_streaming_device(
    row: dict[str, str] | None,
    *,
    for_location_id: str | None = None,
) -> None:
    migrate_device_slots_from_legacy_if_needed()
    _v2_write_streaming_slot(row, for_location_id=for_location_id)


def read_saved_av_receiver() -> dict[str, str] | None:
    migrate_device_slots_from_legacy_if_needed()
    return _v2_read_primary_av_receiver()


def write_saved_av_receiver(
    row: dict[str, str] | None,
    *,
    for_location_id: str | None = None,
) -> None:
    migrate_device_slots_from_legacy_if_needed()
    _v2_write_av_receiver_slot(row, for_location_id=for_location_id)


def clear_all_persisted_devices_and_targets() -> None:
    """Clear saved device slots, last playback/receiver targets, and legacy receiver lists."""
    migrate_device_slots_from_legacy_if_needed()
    pop_app_state_key_prefix("observed_capability_live_v1.")
    write_app_state(
        saved_streaming_device={},
        saved_av_receiver={},
        saved_apple_tv_devices=[],
        last_apple_tv={},
        last_receiver={},
        saved_receivers=[],
        locations_v2=[],
        current_location_id="",
        feature_delegation_v1={"overrides": {}, "log": {}, "active": {}},
    )


def read_saved_apple_tv_devices() -> list[dict[str, str]]:
    """Deprecated combined list; returns streaming + AVR slots only (for old callers)."""
    migrate_device_slots_from_legacy_if_needed()
    out: list[dict[str, str]] = []
    s = _parse_one_saved_device_row(read_app_state().get("saved_streaming_device"))
    if s:
        out.append(s)
    a = _parse_one_saved_device_row(read_app_state().get("saved_av_receiver"))
    if a:
        out.append(a)
    return out


def write_saved_apple_tv_devices(rows: list[dict[str, str]]) -> None:
    """Writes the two single-device slots from at most two legacy-shaped rows."""
    migrate_device_slots_from_legacy_if_needed()
    stream: dict[str, str] | None = None
    avr: dict[str, str] | None = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        row = {k: str(r.get(k, "")).strip() for k in ("identifier", "address", "name", "label")}
        if not row["identifier"] or not row["address"]:
            continue
        if not row["label"]:
            nm = row["name"] or "Device"
            row["label"] = f"{nm} — {row['address']}"
        lk = r.get("looks_like_apple_tv")
        if isinstance(lk, bool):
            row["looks_like_apple_tv"] = "true" if lk else "false"
        elif isinstance(lk, str) and lk.strip().lower() in ("true", "false"):
            row["looks_like_apple_tv"] = lk.strip().lower()
        if stream is None and row_is_playback_apple_tv(row):
            stream = row
        elif avr is None and not row_is_playback_apple_tv(row):
            avr = row
        if stream is not None and avr is not None:
            break
    write_saved_streaming_device(stream)
    write_saved_av_receiver(avr)


def merge_legacy_saved_receivers_into_av_slot() -> bool:
    """
    One-time: if the AVR slot is empty, copy the first ``saved_receivers`` entry into
    ``saved_av_receiver`` as a synthetic ``denon:host`` row, then clear ``saved_receivers``.
    """
    migrate_device_slots_from_legacy_if_needed()
    legacy = read_saved_receivers()
    if not legacy:
        return False
    if read_saved_av_receiver() is not None:
        write_saved_receivers([])
        return False

    item = legacy[0]
    host = str(item.get("host") or "").strip()
    if not host:
        write_saved_receivers([])
        return False
    nm = str(item.get("name") or "").strip() or "Receiver"
    lb = str(item.get("label") or "").strip() or f"{nm} — {host}"
    write_saved_av_receiver(
        {
            "identifier": f"denon:{host}",
            "address": host,
            "name": nm,
            "label": lb,
            "looks_like_apple_tv": "false",
        }
    )
    write_saved_receivers([])
    return True


def read_last_receiver() -> dict[str, str]:
    v = read_app_state().get("last_receiver")
    if not isinstance(v, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("host", "name", "label", "id"):
        val = v.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


def write_last_receiver(
    *,
    host: str,
    name: str | None = None,
    label: str | None = None,
    device_id: str | None = None,
) -> None:
    payload: dict[str, str] = {"host": host.strip()}
    if isinstance(name, str) and name.strip():
        payload["name"] = name.strip()
    if isinstance(label, str) and label.strip():
        payload["label"] = label.strip()
    if isinstance(device_id, str) and device_id.strip():
        payload["id"] = device_id.strip()
    write_app_state(last_receiver=payload)


def clear_last_receiver() -> None:
    write_app_state(last_receiver={})


def read_saved_receivers() -> list[dict[str, str]]:
    raw = read_app_state().get("saved_receivers")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = {k: str(item.get(k, "")).strip() for k in ("host", "name", "label", "id")}
        if not row["host"]:
            continue
        if not row["label"]:
            nm = row["name"] or "Receiver"
            row["label"] = f"{nm} — {row['host']}"
        out.append(row)
    return out


def write_saved_receivers(rows: list[dict[str, str]]) -> None:
    clean: list[dict[str, str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        row = {k: str(r.get(k, "")).strip() for k in ("host", "name", "label", "id")}
        if not row["host"]:
            continue
        if not row["label"]:
            nm = row["name"] or "Receiver"
            row["label"] = f"{nm} — {row['host']}"
        clean.append(row)
    write_app_state(saved_receivers=clean)


# --- Multi-room locations (v2), Advanced delegation state, slot helpers -----------------

LOCATION_PRESET_ROOM_NAMES: tuple[str, ...] = (
    "Living room",
    "Bedroom",
    "Office",
    "Kitchen",
    "Basement",
    "Theater",
)

_LOCATION_SLOT_KEYS: tuple[str, ...] = (
    "streaming",
    "av_receiver",
    "tv",
    "projector",
    "game",
    "other",
)

_EMPTY_DELEGATION: dict[str, dict[str, Any]] = {"overrides": {}, "log": {}, "active": {}}


def _receiver_from_partial_dict(item: dict[str, Any]) -> dict[str, str] | None:
    adr = str(item.get("address") or "").strip()
    if not adr:
        return None
    host = adr.split("%")[0].strip()
    ident = str(item.get("identifier") or "").strip() or f"denon:{host}"
    nm = str(item.get("name") or "").strip() or "Receiver"
    lb = str(item.get("label") or "").strip() or f"{nm} — {adr}"
    row: dict[str, str] = {
        "identifier": ident,
        "address": adr,
        "name": nm,
        "label": lb,
        "looks_like_apple_tv": "false",
    }
    lk = item.get("looks_like_apple_tv")
    if isinstance(lk, bool):
        row["looks_like_apple_tv"] = "true" if lk else "false"
    elif isinstance(lk, str) and lk.strip().lower() in ("true", "false"):
        row["looks_like_apple_tv"] = lk.strip().lower()
    return row


def _parse_receiver_row_flexible(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    hit = _parse_one_saved_device_row(item)
    if hit is not None:
        return hit
    return _receiver_from_partial_dict(item)


def _normalize_slot_list(raw: Any, sk: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    it: list[Any]
    if isinstance(raw, list):
        it = raw
    elif isinstance(raw, dict):
        it = [raw]
    else:
        it = []
    for x in it:
        if not isinstance(x, dict):
            continue
        if sk == "av_receiver":
            pr = _parse_receiver_row_flexible(x)
        else:
            pr = _parse_one_saved_device_row(x)
        if pr is not None:
            rows.append(pr)
    return rows


def _normalize_wifi_dict(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    ssid = str(raw.get("ssid") or "").strip()
    if not ssid:
        return None
    return {"ssid": ssid, "password": str(raw.get("password") or "")}


def _normalize_location_dict(item: dict[str, Any]) -> dict[str, Any] | None:
    lid = str(item.get("id") or "").strip()
    if not lid:
        return None
    loc: dict[str, Any] = {
        "id": lid,
        "name": str(item.get("name") or "Room").strip() or "Room",
    }
    for sk in _LOCATION_SLOT_KEYS:
        loc[sk] = _normalize_slot_list(item.get(sk), sk)
    wifi = _normalize_wifi_dict(item.get("wifi"))
    if wifi is not None:
        loc["wifi"] = wifi
    return loc


def _loc_serializable(loc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(loc.get("id") or ""),
        "name": str(loc.get("name") or "Room").strip() or "Room",
    }
    for sk in _LOCATION_SLOT_KEYS:
        raw = loc.get(sk, [])
        if isinstance(raw, list):
            out[sk] = [dict(r) for r in raw if isinstance(r, dict)]
        else:
            out[sk] = []
    wifi = _normalize_wifi_dict(loc.get("wifi"))
    if wifi is not None:
        out["wifi"] = dict(wifi)
    return out


def _new_empty_location(loc_id: str, name: str) -> dict[str, Any]:
    loc: dict[str, Any] = {"id": loc_id, "name": str(name).strip() or "Room"}
    for sk in _LOCATION_SLOT_KEYS:
        loc[sk] = []
    return loc


def _v2_copy_locations_from_state(cur: dict[str, Any]) -> list[dict[str, Any]]:
    raw = cur.get("locations_v2")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        parsed = _normalize_location_dict(item)
        if parsed is not None:
            out.append(parsed)
    return out


def _v2_find_loc(locs: list[dict[str, Any]], lid: str) -> dict[str, Any] | None:
    tl = str(lid or "").strip()
    if not tl:
        return None
    for L in locs:
        if str(L.get("id") or "").strip() == tl:
            return L
    return None


def _v2_resolve_effective_location_id(locs: list[dict[str, Any]], cur: dict[str, Any]) -> str:
    lid = str(cur.get("current_location_id") or "").strip()
    ids = {str(L.get("id") or "").strip() for L in locs}
    if lid and lid in ids:
        return lid
    if locs:
        return str(locs[0].get("id") or "").strip()
    return ""


def _v2_first_parsed_row(
    loc: dict[str, Any] | None,
    slot_key: str,
    *,
    use_receiver_parser: bool,
) -> dict[str, str] | None:
    if not loc:
        return None
    rows = loc.get(slot_key)
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    x = rows[0]
    if use_receiver_parser:
        return _parse_receiver_row_flexible(x) or _parse_one_saved_device_row(x)
    return _parse_one_saved_device_row(x)


def _mirror_primary_devices_to_legacy_slots(
    cur: dict[str, Any],
    locs: list[dict[str, Any]],
    eff_location_id: str,
) -> None:
    loc = _v2_find_loc(locs, eff_location_id)
    s0 = _v2_first_parsed_row(loc, "streaming", use_receiver_parser=False)
    a0 = _v2_first_parsed_row(loc, "av_receiver", use_receiver_parser=True)
    cur["saved_streaming_device"] = _slot_row_to_state_dict(s0)
    cur["saved_av_receiver"] = _slot_row_to_state_dict(a0)


def _v2_persist_locations_and_mirror_legacy(locs: list[dict[str, Any]]) -> None:
    cur = read_app_state()
    cur["locations_v2"] = [_loc_serializable(L) for L in locs]
    eff = _v2_resolve_effective_location_id(locs, cur)
    _mirror_primary_devices_to_legacy_slots(cur, locs, eff)
    _atomic_write_state(cur)


def _ensure_locations_v2_migrated() -> None:
    migrate_device_slots_from_legacy_if_needed()
    cur = read_app_state()
    raw = cur.get("locations_v2")
    if isinstance(raw, list) and len(raw) > 0:
        return
    loc_id = uuid.uuid4().hex
    stream = _parse_one_saved_device_row(cur.get("saved_streaming_device"))
    avr_raw = cur.get("saved_av_receiver")
    avr = _parse_one_saved_device_row(avr_raw) or _parse_receiver_row_flexible(avr_raw)
    loc: dict[str, Any] = {
        "id": loc_id,
        "name": "Room",
        "streaming": [stream] if stream else [],
        "av_receiver": [avr] if avr else [],
        "tv": [],
        "projector": [],
        "game": [],
        "other": [],
    }
    cur["locations_v2"] = [_loc_serializable(loc)]
    cur["current_location_id"] = loc_id
    _mirror_primary_devices_to_legacy_slots(cur, [loc], loc_id)
    _atomic_write_state(cur)


def _coerce_slot_row(row: dict[str, str], slot_key: str) -> dict[str, str] | None:
    if slot_key == "av_receiver":
        hit = _parse_receiver_row_flexible(row)
    else:
        hit = _parse_one_saved_device_row(row)
    if hit is not None:
        return hit
    ident = str(row.get("identifier") or "").strip()
    adr = str(row.get("address") or "").strip()
    if ident and adr:
        out = {k: str(row.get(k, "")).strip() for k in ("identifier", "address", "name", "label")}
        out["identifier"] = ident
        out["address"] = adr
        if not out["label"]:
            nm = out["name"] or "Device"
            out["label"] = f"{nm} — {adr}"
        lk = row.get("looks_like_apple_tv")
        if isinstance(lk, bool):
            out["looks_like_apple_tv"] = "true" if lk else "false"
        elif isinstance(lk, str) and lk.strip().lower() in ("true", "false"):
            out["looks_like_apple_tv"] = lk.strip().lower()
        return out
    if slot_key == "av_receiver":
        return _receiver_from_partial_dict(row)
    return None


def _v2_read_primary_streaming() -> dict[str, str] | None:
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    if not locs:
        return _parse_one_saved_device_row(cur.get("saved_streaming_device"))
    lid = _v2_resolve_effective_location_id(locs, cur)
    loc = _v2_find_loc(locs, lid)
    return _v2_first_parsed_row(loc, "streaming", use_receiver_parser=False)


def _v2_read_primary_av_receiver() -> dict[str, str] | None:
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    if not locs:
        return _parse_one_saved_device_row(cur.get("saved_av_receiver"))
    lid = _v2_resolve_effective_location_id(locs, cur)
    loc = _v2_find_loc(locs, lid)
    return _v2_first_parsed_row(loc, "av_receiver", use_receiver_parser=True)


def _v2_write_streaming_slot(row: dict[str, str] | None, *, for_location_id: str | None) -> None:
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    if not locs:
        write_app_state(saved_streaming_device=_slot_row_to_state_dict(row))
        return
    lid = (str(for_location_id).strip() if for_location_id else "") or _v2_resolve_effective_location_id(
        locs, cur
    )
    loc = _v2_find_loc(locs, lid)
    if loc is None:
        return
    if row is None:
        loc["streaming"] = []
    else:
        pr = _coerce_slot_row(row, "streaming")
        if pr is not None:
            loc["streaming"] = [pr]
    _v2_persist_locations_and_mirror_legacy(locs)


def _v2_write_av_receiver_slot(row: dict[str, str] | None, *, for_location_id: str | None) -> None:
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    if not locs:
        write_app_state(saved_av_receiver=_slot_row_to_state_dict(row))
        return
    lid = (str(for_location_id).strip() if for_location_id else "") or _v2_resolve_effective_location_id(
        locs, cur
    )
    loc = _v2_find_loc(locs, lid)
    if loc is None:
        return
    if row is None:
        loc["av_receiver"] = []
    else:
        pr = _coerce_slot_row(row, "av_receiver")
        if pr is not None:
            loc["av_receiver"] = [pr]
    _v2_persist_locations_and_mirror_legacy(locs)


def read_all_locations_v2() -> list[dict[str, Any]]:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    return [copy.deepcopy(L) for L in _v2_copy_locations_from_state(cur)]


def read_current_location_id() -> str:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    return _v2_resolve_effective_location_id(locs, cur)


def read_current_location_name() -> str:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    lid = _v2_resolve_effective_location_id(locs, cur)
    loc = _v2_find_loc(locs, lid)
    if loc is None:
        return "Room"
    return str(loc.get("name") or "Room").strip() or "Room"


def read_location_wifi(*, for_location_id: str | None = None) -> dict[str, str] | None:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    lid = str(for_location_id or "").strip() or _v2_resolve_effective_location_id(locs, cur)
    loc = _v2_find_loc(locs, lid)
    if loc is None:
        return None
    return _normalize_wifi_dict(loc.get("wifi"))


def clear_location_wifi(*, for_location_id: str | None = None) -> None:
    """Remove saved WiFi credentials for a location (logout / forget network)."""
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    lid = str(for_location_id or "").strip() or _v2_resolve_effective_location_id(locs, cur)
    loc = _v2_find_loc(locs, lid)
    if loc is None:
        return
    loc.pop("wifi", None)
    _v2_persist_locations_and_mirror_legacy(locs)


def write_location_wifi(
    ssid: str,
    password: str,
    *,
    for_location_id: str | None = None,
) -> None:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    lid = str(for_location_id or "").strip() or _v2_resolve_effective_location_id(locs, cur)
    loc = _v2_find_loc(locs, lid)
    if loc is None:
        return
    ssid_clean = str(ssid or "").strip()
    if not ssid_clean:
        return
    loc["wifi"] = {"ssid": ssid_clean, "password": str(password or "")}
    _v2_persist_locations_and_mirror_legacy(locs)


def set_current_location_id(lid: str) -> bool:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    tl = str(lid).strip()
    if not any(str(L.get("id") or "").strip() == tl for L in locs):
        return False
    cur["current_location_id"] = tl
    cur["locations_v2"] = [_loc_serializable(L) for L in locs]
    _mirror_primary_devices_to_legacy_slots(cur, locs, tl)
    _atomic_write_state(cur)
    return True


def add_empty_location_v2(name: str) -> None:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    new_id = uuid.uuid4().hex
    locs.append(_new_empty_location(new_id, str(name).strip() or "Room"))
    _v2_persist_locations_and_mirror_legacy(locs)


def delete_location_v2(loc_id: str) -> bool:
    """
    Remove one location and all device rows in its slots. Clears Advanced delegation
    (overrides, log, active) for that location id. Does not remove the last remaining room.
    """
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    tl = str(loc_id or "").strip()
    if not tl or len(locs) <= 1:
        return False
    new_locs = [L for L in locs if str(L.get("id") or "").strip() != tl]
    if len(new_locs) == len(locs):
        return False
    if str(cur.get("current_location_id") or "").strip() == tl:
        cur["current_location_id"] = str(new_locs[0].get("id") or "").strip()
    cur["locations_v2"] = [_loc_serializable(L) for L in new_locs]
    eff = _v2_resolve_effective_location_id(new_locs, cur)
    _mirror_primary_devices_to_legacy_slots(cur, new_locs, eff)
    fd = _load_feature_delegation(cur)
    for fd_key in ("overrides", "log", "active"):
        sub = fd.get(fd_key)
        if isinstance(sub, dict) and tl in sub:
            del sub[tl]
    cur["feature_delegation_v1"] = fd
    _atomic_write_state(cur)
    return True


def rename_location_v2(loc_id: str, new_name: str) -> bool:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    tid = str(loc_id).strip()
    nm = str(new_name).strip() or "Room"
    for L in locs:
        if str(L.get("id") or "").strip() == tid:
            L["name"] = nm
            _v2_persist_locations_and_mirror_legacy(locs)
            return True
    return False


def append_device_to_location_slot(
    slot_key: str,
    row: dict[str, str],
    *,
    for_location_id: str | None,
    new_location_name: str | None,
) -> str:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    sk = str(slot_key or "").strip()
    if sk not in _LOCATION_SLOT_KEYS:
        sk = "other"
    lid_target: str = ""
    if new_location_name is not None and str(new_location_name).strip():
        new_id = uuid.uuid4().hex
        nm = str(new_location_name).strip() or "Room"
        locs.append(_new_empty_location(new_id, nm))
        lid_target = new_id
    elif for_location_id and _v2_find_loc(locs, str(for_location_id).strip()):
        lid_target = str(for_location_id).strip()
    else:
        eff = _v2_resolve_effective_location_id(locs, cur)
        if eff:
            lid_target = eff
        else:
            new_id = uuid.uuid4().hex
            locs.append(_new_empty_location(new_id, "Room"))
            lid_target = new_id
    loc = _v2_find_loc(locs, lid_target)
    if loc is None:
        return ""
    pr = _coerce_slot_row(dict(row), sk)
    if pr is None:
        return lid_target
    slot_list = loc.setdefault(sk, [])
    if not isinstance(slot_list, list):
        slot_list = []
        loc[sk] = slot_list
    slot_list.append(pr)
    _v2_persist_locations_and_mirror_legacy(locs)
    return lid_target


def remove_device_at_slot_index(
    slot_key: str,
    index: int,
    for_location_id: str | None = None,
) -> None:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    sk = str(slot_key or "").strip()
    if sk not in _LOCATION_SLOT_KEYS:
        return
    lid = (
        str(for_location_id).strip()
        if for_location_id
        else _v2_resolve_effective_location_id(locs, cur)
    )
    loc = _v2_find_loc(locs, lid)
    if not loc:
        return
    rows = loc.get(sk)
    if not isinstance(rows, list):
        return
    if 0 <= int(index) < len(rows):
        del rows[int(index)]
    _v2_persist_locations_and_mirror_legacy(locs)


def read_saved_streaming_devices_all() -> list[dict[str, str]]:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    lid = _v2_resolve_effective_location_id(locs, cur)
    loc = _v2_find_loc(locs, lid)
    if not loc:
        return []
    rows = loc.get("streaming")
    if not isinstance(rows, list):
        return []
    return [dict(r) for r in rows if isinstance(r, dict)]


def read_saved_slot_rows_all(slot_key: str) -> list[dict[str, str]]:
    sk = str(slot_key or "").strip()
    if sk not in _LOCATION_SLOT_KEYS:
        return []
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    lid = _v2_resolve_effective_location_id(locs, cur)
    loc = _v2_find_loc(locs, lid)
    if not loc:
        return []
    rows = loc.get(sk)
    if not isinstance(rows, list):
        return []
    return [dict(r) for r in rows if isinstance(r, dict)]


def read_saved_tv() -> list[dict[str, str]]:
    return read_saved_slot_rows_all("tv")


def read_saved_projector() -> list[dict[str, str]]:
    return read_saved_slot_rows_all("projector")


def read_saved_game() -> list[dict[str, str]]:
    return read_saved_slot_rows_all("game")


def read_saved_other() -> list[dict[str, str]]:
    return read_saved_slot_rows_all("other")


def _v2_replace_entire_slot(slot_key: str, row: dict[str, str] | None) -> None:
    migrate_device_slots_from_legacy_if_needed()
    _ensure_locations_v2_migrated()
    cur = read_app_state()
    locs = _v2_copy_locations_from_state(cur)
    sk = str(slot_key or "").strip()
    if sk not in _LOCATION_SLOT_KEYS:
        return
    lid = _v2_resolve_effective_location_id(locs, cur)
    loc = _v2_find_loc(locs, lid)
    if not loc:
        return
    if row is None:
        loc[sk] = []
    else:
        pr = _coerce_slot_row(dict(row), sk)
        loc[sk] = [pr] if pr is not None else []
    _v2_persist_locations_and_mirror_legacy(locs)


def write_saved_tv(row: dict[str, str] | None) -> None:
    _v2_replace_entire_slot("tv", row)


def write_saved_projector(row: dict[str, str] | None) -> None:
    _v2_replace_entire_slot("projector", row)


def write_saved_game(row: dict[str, str] | None) -> None:
    _v2_replace_entire_slot("game", row)


def write_saved_other(row: dict[str, str] | None) -> None:
    _v2_replace_entire_slot("other", row)


def _load_feature_delegation(cur: dict[str, Any]) -> dict[str, Any]:
    r = cur.get("feature_delegation_v1")
    if not isinstance(r, dict):
        return copy.deepcopy(_EMPTY_DELEGATION)
    return {
        "overrides": copy.deepcopy(r["overrides"]) if isinstance(r.get("overrides"), dict) else {},
        "log": copy.deepcopy(r["log"]) if isinstance(r.get("log"), dict) else {},
        "active": copy.deepcopy(r["active"]) if isinstance(r.get("active"), dict) else {},
    }


def read_feature_delegation_overrides(loc_id: str) -> dict[str, list[str]]:
    lid = str(loc_id or "").strip()
    if not lid:
        return {}
    cur = read_app_state()
    fd = _load_feature_delegation(cur)
    ov = fd["overrides"].get(lid)
    if not isinstance(ov, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in ov.items():
        if isinstance(k, str) and isinstance(v, list):
            out[k] = [str(x) for x in v if isinstance(x, str)]
    return out


def write_feature_delegation_overrides(loc_id: str, overrides: dict[str, list[str]]) -> None:
    lid = str(loc_id or "").strip()
    if not lid:
        return
    cur = read_app_state()
    fd = _load_feature_delegation(cur)
    clean: dict[str, list[str]] = {}
    for k, v in overrides.items():
        if isinstance(k, str) and isinstance(v, list):
            clean[k] = [str(x) for x in v if isinstance(x, str)]
    if not isinstance(fd["overrides"], dict):
        fd["overrides"] = {}
    fd["overrides"][lid] = clean
    write_app_state(feature_delegation_v1=fd)


def clear_feature_delegation_overrides(loc_id: str) -> None:
    lid = str(loc_id or "").strip()
    if not lid:
        return
    cur = read_app_state()
    fd = _load_feature_delegation(cur)
    if isinstance(fd["overrides"], dict) and lid in fd["overrides"]:
        del fd["overrides"][lid]
    write_app_state(feature_delegation_v1=fd)


def read_delegation_log(loc_id: str) -> dict[str, list[str]]:
    lid = str(loc_id or "").strip()
    if not lid:
        return {}
    cur = read_app_state()
    fd = _load_feature_delegation(cur)
    lg = fd["log"].get(lid)
    if not isinstance(lg, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in lg.items():
        if isinstance(k, str) and isinstance(v, list):
            out[k] = [str(x) for x in v if isinstance(x, str)]
    return out


def read_delegation_active_indices(loc_id: str) -> dict[str, int]:
    lid = str(loc_id or "").strip()
    if not lid:
        return {}
    cur = read_app_state()
    fd = _load_feature_delegation(cur)
    ac = fd["active"].get(lid)
    if not isinstance(ac, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in ac.items():
        if isinstance(k, str):
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                out[k] = 0
    return out


def append_delegation_log_line(loc_id: str, feature_id: str, line: str) -> None:
    append_delegation_log_lines(loc_id, [(feature_id, line)])


def append_delegation_log_lines(loc_id: str, entries: list[tuple[str, str]]) -> None:
    """Append several (feature_id, line) pairs in one state read/write.

    The poll-failure path logs to every feature at once; batching keeps that to
    a single state.json rewrite instead of one per feature.
    """
    lid = str(loc_id or "").strip()
    if not lid or not entries:
        return
    cur = read_app_state()
    fd = _load_feature_delegation(cur)
    if not isinstance(fd["log"], dict):
        fd["log"] = {}
    sub = fd["log"].setdefault(lid, {})
    if not isinstance(sub, dict):
        sub = {}
        fd["log"][lid] = sub
    wrote = False
    for feature_id, line in entries:
        fid = str(feature_id or "").strip()
        if not fid:
            continue
        bucket = sub.setdefault(fid, [])
        if not isinstance(bucket, list):
            bucket = []
            sub[fid] = bucket
        bucket.append(str(line)[:500])
        if len(bucket) > 80:
            del bucket[:-80]
        wrote = True
    if wrote:
        write_app_state(feature_delegation_v1=fd)


def advance_delegation_active(loc_id: str, feature_id: str, n_slots: int) -> None:
    lid = str(loc_id or "").strip()
    fid = str(feature_id or "").strip()
    n = int(n_slots)
    if not lid or not fid or n < 1:
        return
    cur = read_app_state()
    fd = _load_feature_delegation(cur)
    if not isinstance(fd["active"], dict):
        fd["active"] = {}
    sub = fd["active"].setdefault(lid, {})
    if not isinstance(sub, dict):
        sub = {}
        fd["active"][lid] = sub
    try:
        cur_i = int(sub.get(fid, 0) or 0)
    except (TypeError, ValueError):
        cur_i = 0
    sub[fid] = (cur_i + 1) % n
    write_app_state(feature_delegation_v1=fd)
