"""Portable settings.json: defaults, migration, validation, atomic save.

Existing v0.1.x files load unchanged; new keys are filled from defaults and
loosely-typed legacy values ("band": 5, "5GHz", "true") are normalised.
Unknown keys are preserved so hand-edited files are never silently trimmed.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from . import paths

SCHEMA = 2

BANDS = ("auto", "2.4", "5", "6")
SECURITY = ("wpa2", "transition", "wpa3")
PROTECTION = ("strict", "balanced")
SOURCES = ("vpn",)

DEFAULT_SETTINGS: dict = {
    "schema": SCHEMA,
    "hotspot": {
        "ssid": "WireSpot",
        "password": "",
        "security": "wpa2",
        "band": "auto",
        # Always "vpn": Mobile Hotspot is started from the WireGuard connection
        # profile so Windows NATs clients into the tunnel. ("wifi" - share Wi-Fi
        # then rebind classic ICS - was removed: it fights icssvc and briefly
        # shares unprotected internet.)
        "source": "vpn",
    },
    "vpn": {
        "default_profile": "",
        # New installs default to the hotspot-compatible mode; existing
        # settings.json files keep whatever they had.
        "protection": "balanced",
        "wireguard_path": "",
    },
    "behavior": {
        "auto_start_hotspot": True,
        # Kept for v0.1 compatibility but no longer optional: a hotspot that is
        # not bound to the VPN would share unprotected internet, so WireSpot
        # always binds (or rolls the hotspot back).
        "auto_bind_ics": True,
        # When a stage fails, offer the recommended recovery interactively.
        # false = stop at the first failure without asking.
        "offer_fallbacks": True,
        # Watch the Downloads folder for new WireGuard .conf files.
        "watch_downloads": True,
        # Background check while READY: stop the hotspot if sharing drifts
        # off the VPN adapter or the tunnel drops (fail closed).
        "guard": True,
        # Balanced mode: force all host DNS (incl. the ICS DNS proxy the
        # hotspot clients use) to the tunnel DNS with an NRPT rule.
        "dns_lock": True,
        # New hotspot devices get no network until you approve them.
        "approve_devices": True,
        # Tray: go live automatically when it starts with Windows.
        "autoconnect": False,
        "debug": False,
        # Share an externally managed NordVPN connection instead of a .conf.
        "profile_less": False,
    },
}

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}


def normalize_band(value) -> str:
    s = str(value).strip().lower().replace(" ", "").replace("ghz", "")
    if s in {"2", "24", "2.4", "2,4"}:
        return "2.4"
    if s in {"5", "5.0"}:
        return "5"
    if s in {"6", "6.0", "6e"}:
        return "6"
    if s in {"auto", "any", "0", ""}:
        return "auto"
    return str(value)


def _to_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return default


def migrate(data) -> tuple[dict, list[str]]:
    """Merge a loaded JSON object onto defaults. Returns (settings, notes)."""
    notes: list[str] = []
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    if not isinstance(data, dict):
        if data is not None:
            notes.append("settings.json was not a JSON object; defaults used.")
        return merged, notes

    for section, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(section), dict):
            merged[section].update(value)
        elif section in ("hotspot", "vpn", "behavior"):
            notes.append(f"{section} was not an object; defaults used.")
        else:
            merged[section] = value

    h, v, b = merged["hotspot"], merged["vpn"], merged["behavior"]
    for key in ("ssid", "password"):
        if not isinstance(h.get(key), str):
            h[key] = "" if h.get(key) is None else str(h[key])

    band = normalize_band(h.get("band", "auto"))
    if band != h.get("band"):
        notes.append(f"hotspot.band {h.get('band')!r} normalised to {band!r}.")
    h["band"] = band if band in BANDS else "auto"
    if band not in BANDS:
        notes.append(f"hotspot.band {band!r} is invalid; using 'auto'.")

    sec = str(h.get("security", "wpa2")).lower()
    h["security"] = sec if sec in SECURITY else "wpa2"
    if sec not in SECURITY:
        notes.append(f"hotspot.security {sec!r} is invalid; using 'wpa2'.")

    src = str(h.get("source", "vpn")).lower()
    if src not in SOURCES:
        notes.append(f"hotspot.source {src!r} is no longer supported (it could share unprotected Wi-Fi); using 'vpn'.")
    h["source"] = "vpn"

    prot = str(v.get("protection", "balanced")).lower()
    v["protection"] = prot if prot in PROTECTION else "balanced"
    if prot not in PROTECTION:
        notes.append(f"vpn.protection {prot!r} is invalid; using 'balanced'.")
    for key in ("default_profile", "wireguard_path"):
        if not isinstance(v.get(key), str):
            v[key] = ""

    for key, default in DEFAULT_SETTINGS["behavior"].items():
        b[key] = _to_bool(b.get(key, default), default)

    if merged.get("schema") != SCHEMA:
        merged["schema"] = SCHEMA
    return merged, notes


def validate_hotspot(settings: dict) -> list[str]:
    """Return a list of human-readable problems (empty == valid)."""
    problems = []
    h = settings["hotspot"]
    ssid, pwd = h.get("ssid", ""), h.get("password", "")
    if not ssid or len(ssid.encode("utf-8")) > 32:
        problems.append("SSID must be 1-32 bytes.")
    if not 8 <= len(pwd) <= 63:
        problems.append("Hotspot password must be 8-63 characters (set password <value>).")
    try:
        pwd.encode("ascii")
        ssid.encode("ascii")
    except UnicodeEncodeError:
        problems.append("Use ASCII characters for the hotspot SSID/password (WPA2-PSK requirement).")
    if any(ord(ch) < 32 for ch in pwd + ssid):
        problems.append("SSID/password must not contain control characters.")
    if h.get("security") not in SECURITY:
        problems.append("security must be wpa2, transition, or wpa3.")
    if h.get("band") not in BANDS:
        problems.append("band must be auto, 2.4, 5, or 6.")
    return problems


def load(path: Path | None = None) -> tuple[dict, list[str]]:
    path = path or paths.SETTINGS_PATH
    if not path.exists():
        return copy.deepcopy(DEFAULT_SETTINGS), []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:  # corrupt file: keep a backup, do not overwrite silently
        backup = path.with_suffix(".json.bad")
        try:
            path.replace(backup)
        except OSError:
            pass
        return copy.deepcopy(DEFAULT_SETTINGS), [f"settings.json was unreadable ({e}); moved to {backup.name}, defaults used."]
    return migrate(data)


def save(settings: dict, path: Path | None = None) -> None:
    path = path or paths.SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def redacted(settings: dict) -> dict:
    out = copy.deepcopy(settings)
    pwd = out.get("hotspot", {}).get("password", "")
    if pwd:
        out["hotspot"]["password"] = "*" * len(pwd)
    return out
