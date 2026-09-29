"""How other WireSpot processes find the old (tkinter) tray window and talk to it.

No UI toolkit here: the installer and the desktop app's engine use this too,
and they are built without tkinter.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

WM_APP = 0x8000
WM_OPENWINDOW, WM_QUITAPP, WM_UNINSTALL = WM_APP + 5, WM_APP + 6, WM_APP + 7    # from other WireSpot processes
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
# WIRESPOT_INSTANCE lets a test copy run beside your real WireSpot without handing off to it.
WINDOW_CLASS = "WireSpotTrayWindow" + os.environ.get("WIRESPOT_INSTANCE", "")
ICON_ID = 1


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", ctypes.c_wchar * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]
