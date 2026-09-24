"""Keeps the CLI, the desktop app and its tray in step.

The app (window + tray) is one process; the CLI is another. They already
share the ownership record (state.json), settings.json and the cross-process
operation lock. This module adds what was missing:

* bus.json   - which operation is running right now and who started it
               ("the CLI is going live"), plus the result of the last one.
               The other process shows it immediately instead of waiting for
               its next poll, and refuses a conflicting action with a clear
               message instead of a lock timeout.
* pause.json - the "pause for 15 min / 1 h" timer, shared so the CLI shows
               it and an explicit start/stop anywhere cancels it.
* activity.log - one shared, redacted activity journal. The app's Activity
               page shows what the CLI did and vice versa.
* Watcher    - polls a handful of file mtimes (cheap) and calls back the
               moment any of them change, so both sides refresh at once.

Nothing here contains secrets: journal lines pass through log.redact and
the files only hold labels, timestamps and pids.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from . import log, oplock, paths

BUS_PATH = paths.PROGRAMDATA_DIR / "bus.json"
PAUSE_PATH = paths.PROGRAMDATA_DIR / "pause.json"
LEGACY_PAUSE_PATH = paths.PROGRAMDATA_DIR / "tray.json"
JOURNAL_PATH = paths.PROGRAMDATA_DIR / "activity.log"
JOURNAL_MAX = 400_000
JOURNAL_KEEP = 1500
BUS_MUTEX = "Local\\WireSpot.Bus"

SOURCE = "app"          # "app" or "cli" - set by each entry point
SOURCE_LABEL = {"app": "WireSpot app", "cli": "WireSpot CLI"}

OP_LABELS = {"start": "Going live", "stop": "Disconnecting", "hotspot_start": "Starting the hotspot",
             "hotspot_stop": "Stopping the hotspot", "bind": "Re-binding the hotspot", "disconnect_vpn": "Disconnecting the VPN",
             "connect_vpn": "Connecting the VPN"}

_local = threading.local()
_mutex = oplock.NamedMutex(BUS_MUTEX)


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.event("error", f"sync write {path.name}: {e}")


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if pid == os.getpid():
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
    k32.CloseHandle(ctypes.c_void_p(h))
    return bool(ok) and code.value == 259            # STILL_ACTIVE


# ===================================================================== bus
def read_bus() -> dict:
    return _read(BUS_PATH)


def _update_bus(fn: Callable[[dict], None]) -> None:
    got = _mutex.acquire(500)
    try:
        bus = read_bus()
        fn(bus)
        bus["rev"] = int(bus.get("rev", 0)) + 1
        bus["t"] = time.time()
        _write(BUS_PATH, bus)
    finally:
        if got:
            _mutex.release()


def op_begin(name: str) -> None:
    """Called when this process starts a network-changing operation."""
    depth = getattr(_local, "depth", 0)
    _local.depth = depth + 1
    if depth:
        return                                  # nested (hotspot_start -> start)
    label = OP_LABELS.get(name, name.replace("_", " ").capitalize())
    _local.op = label
    _update_bus(lambda b: b.update(op={"who": SOURCE, "pid": os.getpid(), "label": label, "since": time.time()}))
    if name in ("start", "stop", "connect_vpn", "disconnect_vpn"):
        clear_pause(silent=True)                # an explicit start/stop anywhere wins over a pause timer


def op_end(ok: bool, message: str = "") -> None:
    depth = getattr(_local, "depth", 1) - 1
    _local.depth = max(0, depth)
    if depth > 0:
        return
    label = getattr(_local, "op", "")

    def fn(b):
        b["op"] = None
        b["last"] = {"who": SOURCE, "pid": os.getpid(), "label": label, "ok": bool(ok),
                     "message": log.redact(message)[:200], "t": time.time()}
    _update_bus(fn)


def announce(label: str, ok: bool = True, message: str = "") -> None:
    """A state change that was not an operation (guard fail-closed, pause…)."""
    _update_bus(lambda b: b.update(last={"who": SOURCE, "pid": os.getpid(), "label": label, "ok": bool(ok),
                                         "message": log.redact(message)[:200], "t": time.time()}))


def remote_op(bus: dict | None = None) -> dict | None:
    """The operation another live process is running right now, if any."""
    bus = read_bus() if bus is None else bus
    op = bus.get("op")
    if not isinstance(op, dict) or op.get("pid") == os.getpid():
        return None
    if not pid_alive(int(op.get("pid") or 0)) or time.time() - float(op.get("since") or 0) > 900:
        return None
    return op


def describe(op: dict) -> str:
    who = "the CLI" if op.get("who") == "cli" else "the WireSpot app"
    return f"{op.get('label', 'working')} (from {who})"


# ===================================================================== pause
def read_pause() -> dict:
    data = _read(PAUSE_PATH) or _read(LEGACY_PAUSE_PATH)
    until = data.get("pause_until")
    if not isinstance(until, (int, float)) or until <= 0:
        return {}
    return {"pause_until": float(until), "pause_profile": str(data.get("pause_profile") or "")}


def set_pause(until: float, profile: str) -> None:
    _write(PAUSE_PATH, {"pause_until": until, "pause_profile": profile, "who": SOURCE})
    announce(f"Paused until {time.strftime('%H:%M', time.localtime(until))}")


def clear_pause(silent: bool = False) -> bool:
    had = bool(read_pause())
    for p in (PAUSE_PATH, LEGACY_PAUSE_PATH):
        try:
            p.unlink()
        except OSError:
            pass
    if had and not silent:
        announce("Auto-resume cancelled")
    return had


# ===================================================================== journal
def journal(line: str, source: str | None = None) -> None:
    line = log.redact(line.rstrip())
    if not line.strip():
        return
    rec = f"{time.time():.3f}\t{source or SOURCE}\t{line}\n"
    try:
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(rec)
        if JOURNAL_PATH.stat().st_size > JOURNAL_MAX:
            _trim()
    except OSError:
        pass


def _trim() -> None:
    got = _mutex.acquire(500)
    try:
        lines = JOURNAL_PATH.read_text(encoding="utf-8", errors="replace").splitlines(True)[-JOURNAL_KEEP:]
        tmp = JOURNAL_PATH.with_name(f"activity.{os.getpid()}.tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, JOURNAL_PATH)
    except OSError:
        pass
    finally:
        if got:
            _mutex.release()


def read_journal(limit: int = 600) -> list[tuple[float, str, str]]:
    try:
        with open(JOURNAL_PATH, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for raw in data.splitlines()[-limit:]:
        parts = raw.split("\t", 2)
        if len(parts) == 3:
            try:
                out.append((float(parts[0]), parts[1], parts[2]))
            except ValueError:
                pass
    return out


# ===================================================================== watcher
def watched_paths() -> list[Path]:
    return [paths.STATE_PATH, paths.SETTINGS_PATH, BUS_PATH, PAUSE_PATH, paths.VPN_DIR,
            paths.PROGRAMDATA_DIR / "devices.json"]


def _stamp(p: Path):
    try:
        st = p.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


class Watcher(threading.Thread):
    """Calls ``on_change(changed_paths)`` whenever a shared file changes."""

    def __init__(self, on_change: Callable[[list[Path]], None], interval: float = 0.35, files=None):
        super().__init__(daemon=True, name="wirespot-sync")
        self.on_change = on_change
        self.interval = interval
        self.files = list(files) if files is not None else watched_paths()
        self._stop = threading.Event()
        self.stamps = {p: _stamp(p) for p in self.files}

    def stop(self) -> None:
        self._stop.set()

    def poll(self) -> list[Path]:
        changed = []
        for p in self.files:
            s = _stamp(p)
            if s != self.stamps.get(p):
                self.stamps[p] = s
                changed.append(p)
        return changed

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                changed = self.poll()
                if changed:
                    self.on_change(changed)
            except Exception as e:
                log.event("error", f"sync watcher: {e}")
