"""Cross-process coordination between the CLI and the tray (named mutexes).

* OPERATION - held while start/stop/reconnect changes Windows networking, so the
  CLI and the tray can never run two network changes at once.
* GUARD - only one process runs the fail-closed guard at a time.
Windows mutexes are owned per thread and are re-entrant for the owning
thread, so nested operations (hotspot start -> start) are fine.
"""
from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager

OPERATION = "Local\\WireSpot.Operation"
GUARD = "Local\\WireSpot.Guard"
TRAY = "Local\\WireSpot.Tray"

WAIT_OBJECT_0, WAIT_ABANDONED, WAIT_TIMEOUT = 0, 0x80, 0x102


class Busy(RuntimeError):
    pass


class NamedMutex:
    def __init__(self, name: str):
        self.name = name
        self.handle = None
        self.depth = 0

    def acquire(self, timeout_ms: int = 0) -> bool:
        if os.name != "nt":
            return True
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        if self.handle is None:
            self.handle = k32.CreateMutexW(None, False, self.name)
            if not self.handle:
                return True          # cannot coordinate - do not block the user
        r = k32.WaitForSingleObject(ctypes.c_void_p(self.handle), timeout_ms)
        if r in (WAIT_OBJECT_0, WAIT_ABANDONED):
            self.depth += 1
            return True
        return False

    def release(self) -> None:
        if os.name != "nt" or not self.handle or self.depth == 0:
            return
        ctypes.windll.kernel32.ReleaseMutex(ctypes.c_void_p(self.handle))
        self.depth -= 1

    def close(self) -> None:
        while self.depth:
            self.release()
        if self.handle and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.handle))
        self.handle = None


def exists(name: str) -> bool:
    """True if another process holds/created the named mutex."""
    if os.name != "nt":
        return False
    k32 = ctypes.windll.kernel32
    k32.OpenMutexW.restype = ctypes.c_void_p
    h = k32.OpenMutexW(0x00100000, False, name)   # SYNCHRONIZE
    if h:
        k32.CloseHandle(ctypes.c_void_p(h))
        return True
    return False


_op = NamedMutex(OPERATION)


@contextmanager
def operation(timeout_ms: int = 1500):
    """Exclusive network-changing operation across CLI + tray."""
    if not _op.acquire(timeout_ms):
        raise Busy("another WireSpot window is changing the network right now")
    try:
        yield
    finally:
        _op.release()


def operation_busy() -> bool:
    """Non-blocking probe (used by the guard to skip a tick)."""
    if _op.acquire(0):
        _op.release()
        return False
    return True
