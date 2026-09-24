"""Filesystem layout.

Three places:
  * APP_DIR   - where WireSpot.exe / WireSpotCLI.exe live. Installed builds use
                %LOCALAPPDATA%\\Programs\\WireSpot (marked by install.json).
  * BASE      - your data: settings.json, vpn\\ (profiles), logs\\.
                Installed: %APPDATA%\\WireSpot. Portable (settings.json or
                vpn\\ next to the exe): the exe folder. Source checkout: the
                project root. WIRESPOT_HOME overrides.
  * PROGRAMDATA_DIR - machine-level runtime data (tunnel configs referenced by
                the WireGuard service, ownership state) under
                %ProgramData%\\WireSpot, ACL restricted to SYSTEM/Administrators.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

INSTALL_MARKER = "install.json"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_installed(d: Path | None = None) -> bool:
    return ((d or app_dir()) / INSTALL_MARKER).is_file()


def appdata_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "WireSpot"


def install_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Programs" / "WireSpot"


def base_dir() -> Path:
    override = os.environ.get("WIRESPOT_HOME")
    if override:
        return Path(override).resolve()
    here = app_dir()
    if not getattr(sys, "frozen", False):
        return here                      # source checkout: the project root
    if is_installed(here):
        return appdata_dir()
    if (here / "settings.json").exists() or (here / "vpn").is_dir():
        return here                      # portable folder
    return appdata_dir()


APP_DIR = app_dir()
BASE = base_dir()
VPN_DIR = BASE / "vpn"
SETTINGS_PATH = BASE / "settings.json"
LOG_DIR = BASE / "logs"
HISTORY_PATH = LOG_DIR / "history.txt"

_PROGRAMDATA = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
PROGRAMDATA_DIR = Path(os.environ.get("WIRESPOT_DATA") or _PROGRAMDATA / "WireSpot")
RUNTIME_DIR = PROGRAMDATA_DIR / "runtime"
STATE_PATH = PROGRAMDATA_DIR / "state.json"

# ProtonRelay 0.1.x wrote private-key-bearing configs here with an inherited
# ACL that lets every local user read them. Cleaned up on startup.
LEGACY_RUNTIME_DIR = _PROGRAMDATA / "ProtonRelayCLI" / "runtime"


def exe(name: str) -> Path:
    """Path of a sibling executable (WireSpot.exe / WireSpotCLI.exe)."""
    return APP_DIR / name
