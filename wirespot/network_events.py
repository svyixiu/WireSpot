"""Wake the NordVPN guard on native Windows route/interface changes."""
from __future__ import annotations

import ctypes as C
import socket
import sys
import threading

from . import log


class NetworkEvents:
    def __init__(self, wake: threading.Event):
        self.wake = wake
        self.handles: list[C.c_void_p] = []
        self.callback = None
        self.api = None

    def __enter__(self):
        if sys.platform != "win32":
            return self
        try:
            self.api = C.WinDLL("iphlpapi.dll")
            # Callbacks receive (context, row pointer, MIB_NOTIFICATION_TYPE).
            fn_type = C.WINFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_uint32)
            self.callback = fn_type(lambda *_: self.wake.set())
            for name in ("NotifyRouteChange2", "NotifyIpInterfaceChange"):
                fn = getattr(self.api, name)
                fn.argtypes = [C.c_ushort, fn_type, C.c_void_p, C.c_ubyte,
                               C.POINTER(C.c_void_p)]
                fn.restype = C.c_uint32
                handle = C.c_void_p()
                code = fn(socket.AF_INET, self.callback, None, False, C.byref(handle))
                if code:
                    log.event("error", f"[NordVPN] {name} registration failed: {code}")
                else:
                    self.handles.append(handle)
        except Exception as e:
            log.event("error", f"[NordVPN] Network notification unavailable: {e}")
        return self

    def __exit__(self, *_):
        if self.api:
            self.api.CancelMibChangeNotify2.argtypes = [C.c_void_p]
            self.api.CancelMibChangeNotify2.restype = C.c_uint32
            for handle in self.handles:
                self.api.CancelMibChangeNotify2(handle)
        self.handles.clear()
        self.callback = None
