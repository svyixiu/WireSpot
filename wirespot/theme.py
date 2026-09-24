"""Shared visual language for the WireSpot window, tray panel, toasts and setup.

Claude palette only (clay family + warm neutrals), monospace type and SVG
icons (icons.py) - the same look as the CLI, without Unicode glyphs.
"""
from __future__ import annotations

import ctypes
import os

# ------------------------------------------------------------------ palette
BG = "#1F1E1D"          # window / panel background
SURFACE = "#262624"     # cards, sidebar
ROW_HOVER = "#2E2D2A"
SELECTED = "#34332F"
BORDER = "#3D3C38"
RULE = "#34332F"
CLAY = "#D97757"
SHIMMER = "#EB9F7F"
CRAIL = "#C15F3C"
CREAM = "#FAF9F5"
LIGHT = "#E8E6DC"
STONE = "#B0AEA5"
DIM = "#86847C"
INPUT = "#191817"

# state -> (icon, label, colour) for the pill in the header. Icons are SVG (icons.py);
# no Unicode glyphs anywhere in the desktop UI.
STATES = {"unknown": ("ring", "checking", STONE), "live": ("dot", "live", CLAY), "vpn": ("dot", "vpn only", SHIMMER),
          "idle": ("ring", "idle", STONE), "busy": ("spinner", "working", SHIMMER),
          "paused": ("pause", "paused", SHIMMER), "error": ("alert", "attention", CRAIL)}
PILLS = {k: (label, color) for k, (_, label, color) in STATES.items()}      # text-only form (tests, tooltips)


class Fonts:
    def __init__(self, root):
        import tkinter.font as tkfont

        fams = set(tkfont.families(root))
        mono = next((f for f in ("Cascadia Mono", "Cascadia Code", "Consolas") if f in fams), "Courier New")
        self.mono = mono
        self.text = (mono, 10)
        self.bold = (mono, 10, "bold")
        self.small = (mono, 9)
        self.title = (mono, 12, "bold")
        self.h1 = (mono, 16, "bold")
        self.big = (mono, 20, "bold")
        self.sym = ("Segoe UI Symbol", 11)
        self.sym_big = ("Segoe UI Symbol", 16)


def dark_titlebar(widget, rounded: bool = True, border: str = BORDER) -> None:
    """Windows 10/11: dark caption, rounded corners, clay-neutral border (ignored elsewhere)."""
    if os.name != "nt":
        return
    try:
        widget.update_idletasks()
        hwnd = int(widget.wm_frame(), 16)
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)            # immersive dark mode
        if rounded:
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)        # round corners
        r, g, b = (int(border[i:i + 2], 16) for i in (1, 3, 5))
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(ctypes.c_uint(r | g << 8 | b << 16)), 4)
        cr, cg, cb = (int(BG[i:i + 2], 16) for i in (1, 3, 5))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(ctypes.c_uint(cr | cg << 8 | cb << 16)), 4)  # caption
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(ctypes.c_uint(0xDCE6E8)), 4)                 # caption text
    except Exception:
        pass


def hover_row(widgets, bg=BG, hover=ROW_HOVER, on_enter=None, on_leave=None, on_click=None):
    """Make a group of widgets behave like one hoverable/clickable row."""
    def enter(_):
        for w in widgets:
            w.configure(bg=hover)
        if on_enter:
            on_enter()

    def leave(_):
        for w in widgets:
            w.configure(bg=bg)
        if on_leave:
            on_leave()

    for w in widgets:
        w.bind("<Enter>", enter)
        w.bind("<Leave>", leave)
        if on_click:
            w.bind("<Button-1>", lambda e: on_click())
