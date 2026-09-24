"""A fully custom window: no Windows caption, no resize border - but a real app.

* borderless (overrideredirect) Tk toplevel with a 1px warm border, rounded
  corners and a dark DWM frame on Windows 11
* a normal taskbar button: click it to minimise / restore, right-click it for
  "Close window" (WS_EX_APPWINDOW + WS_MINIMIZEBOX + WS_SYSMENU, which draw
  nothing on a caption-less window); Alt-Tab and Alt+F4 work too
* fixed size - it cannot be resized or maximised
* dragged by the header with plain Tk motion events (no native modal move
  loop: that re-entered Tk from inside Windows and crashed the app)
* fade + rise on show, fade on hide; brought to the front even when another
  app has the focus
The two mac-style dots live in the panel Header (panel.py).
"""
from __future__ import annotations

import ctypes
import os
import tkinter as tk
from pathlib import Path

from .motion import Animator, ease_out
from .theme import BG, BORDER, dark_titlebar

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_MINIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00080000
WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = 0x00040000, 0x00000080
SW_MINIMIZE, SW_RESTORE = 6, 9
WM_SETICON = 0x0080
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020        # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED

if os.name == "nt":
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32")
    _u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _u32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    _u32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    _u32.SendMessageW.restype = ctypes.c_ssize_t
    _u32.LoadImageW.restype = ctypes.c_void_p
    _u32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    _u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _u32.IsIconic.argtypes = [ctypes.c_void_p]
    _u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    _u32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    _u32.GetForegroundWindow.restype = ctypes.c_void_p
    _u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_uint]
    _u32.DestroyIcon.argtypes = [ctypes.c_void_p]
else:
    _u32 = _k32 = None


def force_foreground(hwnd: int) -> None:
    """Bring a window to the front even if another app owns the foreground
    (Windows refuses a plain SetForegroundWindow from a background process)."""
    if _u32 is None or not hwnd:
        return
    fg = _u32.GetForegroundWindow()
    if fg == hwnd:
        return
    me = _k32.GetCurrentThreadId()
    other = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other) and other != me and _u32.AttachThreadInput(me, other, True)
    try:
        _u32.BringWindowToTop(hwnd)
        _u32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _u32.AttachThreadInput(me, other, False)


class CustomWindow:
    def __init__(self, root: tk.Tk, width: int, height: int, title: str, icon_path: Path | None = None,
                 anim: Animator | None = None, on_focus=None, on_close=None):
        self.root = root
        self.w, self.h = width, height
        self.anim = anim or Animator(root)
        self.icon_path = icon_path
        self.on_focus = on_focus
        top = tk.Toplevel(root)
        top.withdraw()
        top.title(title)
        top.overrideredirect(True)
        top.resizable(False, False)
        top.configure(bg=BORDER)
        self.top = top
        self.body = tk.Frame(top, bg=BG)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)
        self.visible = False
        self.styled = False
        self._styled_hwnd = 0
        self._icons = []
        self._drag = None
        # taskbar "Close window", Alt+F4
        top.protocol("WM_DELETE_WINDOW", on_close or (lambda: self.hide()))
        top.bind("<FocusIn>", lambda e: e.widget is top and self.on_focus and self.on_focus(True))
        top.bind("<FocusOut>", lambda e: e.widget is top and self.on_focus and self.on_focus(False))

    # ------------------------------------------------------------ win32
    @property
    def hwnd(self) -> int:
        try:
            return int(self.top.wm_frame(), 16)
        except (tk.TclError, ValueError):
            return 0

    def _apply_styles(self) -> bool:
        """(Re)assert the app-window styles. Tk rewrites GWL_EXSTYLE from its own cached copy on some
        calls (e.g. ``wm attributes -topmost``), which silently removed the taskbar behaviour, so this
        runs on every show. True if anything had to change."""
        hwnd = self.hwnd
        if _u32 is None or not hwnd:
            return False
        ex = _u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        st = _u32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        want_ex = (ex & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        want_st = st | WS_MINIMIZEBOX | WS_SYSMENU
        if (ex, st) == (want_ex, want_st):
            return False
        _u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, want_ex)
        _u32.SetWindowLongPtrW(hwnd, GWL_STYLE, want_st)
        _u32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
        return True

    def _topmost(self, on: bool) -> None:
        """Topmost via SetWindowPos (Tk's -topmost would reset our window styles)."""
        if _u32 is not None and self.hwnd:
            _u32.SetWindowPos(self.hwnd, ctypes.c_void_p(-1 if on else -2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def _style(self) -> None:
        """Taskbar button that minimises/restores, rounded dark frame, window icon. Needs a mapped window.

        Tk may *recreate* the native wrapper window (a new HWND with Tk's own styles) - it does so on
        the first hide/show after a transparency change - so this follows the HWND: whenever it is new,
        styles, icon and dark frame are applied again. A hide/show makes the taskbar notice."""
        if _u32 is None:
            return
        for _ in range(4):
            self.top.update_idletasks()
            hwnd = self.hwnd
            if not hwnd:
                return
            changed = self._apply_styles()
            if hwnd != self._styled_hwnd:
                self._styled_hwnd = hwnd
                dark_titlebar(self.top, rounded=True)
                self._set_icon(hwnd)
                changed = True
            if not changed:
                break
            self.top.withdraw()
            self.top.deiconify()
        self.styled = True

    def _set_icon(self, hwnd) -> None:
        if not (self.icon_path and Path(self.icon_path).exists()):
            return
        if not self._icons:
            for size in (16, 32):
                h = _u32.LoadImageW(None, str(self.icon_path), 1, size, size, 0x10)
                if h:
                    self._icons.append(h)
        for kind, h in enumerate(self._icons[:2]):
            _u32.SendMessageW(hwnd, WM_SETICON, kind, h)

    def place_default(self, x: int, y: int) -> None:
        self.top.geometry(f"{self.w}x{self.h}+{x}+{y}")

    # ------------------------------------------------------------ show / hide
    def show(self) -> None:
        hwnd = self.hwnd
        if _u32 is not None and hwnd and _u32.IsIconic(hwnd):
            _u32.ShowWindow(hwnd, SW_RESTORE)
        was = self.visible
        self.visible = True
        if not was:
            try:
                self.top.attributes("-alpha", 0.0)
            except tk.TclError:
                pass
            self.top.deiconify()
            self._style()
            geo = self.top.geometry()
            try:
                x, y = (int(v) for v in geo.split("+")[1:3])
            except ValueError:
                x = y = None

            def step(t):
                self.top.attributes("-alpha", t)
                if y is not None and self._drag is None:
                    self.top.geometry(f"+{x}+{int(y + 10 * (1 - t))}")
            self.anim.run(("win", id(self)), 200, step, curve=ease_out)
            # first appearance: on top of whatever has the focus (e.g. the Explorer window setup was run from)
            self._topmost(True)
            self.top.after(700, lambda: self.top.winfo_exists() and self._topmost(False))
        self.top.lift()
        try:
            self.top.focus_force()
        except tk.TclError:
            pass
        force_foreground(self.hwnd)

    def hide(self, done=None) -> None:
        if not self.visible:
            return
        self.visible = False

        def finish():
            self.top.withdraw()
            self.top.attributes("-alpha", 1.0)
            if done:
                done()
        self.anim.run(("win", id(self)), 140, lambda t: self.top.attributes("-alpha", 1 - t), done=finish,
                      curve=lambda t: t)

    def minimize(self) -> None:
        if _u32 is not None and self.hwnd:
            _u32.ShowWindow(self.hwnd, SW_MINIMIZE)

    def is_minimized(self) -> bool:
        return bool(_u32 is not None and self.hwnd and _u32.IsIconic(self.hwnd))

    # ------------------------------------------------------------ drag (plain Tk)
    def drag_start(self, event) -> None:
        self.anim.cancel(("win", id(self)))                    # never fight the show animation
        self.top.attributes("-alpha", 1.0)
        self._drag = (event.x_root - self.top.winfo_x(), event.y_root - self.top.winfo_y())

    def drag_move(self, event) -> None:
        if self._drag is None:
            return
        dx, dy = self._drag
        self.top.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def drag_end(self, event=None) -> None:
        self._drag = None

    def bind_drag(self, *widgets) -> None:
        for w in widgets:
            w.bind("<ButtonPress-1>", self.drag_start, add="+")
            w.bind("<B1-Motion>", self.drag_move, add="+")
            w.bind("<ButtonRelease-1>", self.drag_end, add="+")

    def snapshot_dimmed(self, widget: tk.Misc, factor: float = 0.38):
        """A dimmed picture of ``widget`` (sheet backdrop). Rendered with PrintWindow, so it is
        correct even if another window overlaps this one. None if unavailable."""
        if os.name != "nt":
            return None
        try:
            import zlib

            widget.update_idletasks()
            hwnd = self.hwnd
            ww, wh = self.top.winfo_width(), self.top.winfo_height()
            ox, oy = widget.winfo_rootx() - self.top.winfo_rootx(), widget.winfo_rooty() - self.top.winfo_rooty()
            w, h = widget.winfo_width(), widget.winfo_height()
            if w < 2 or h < 2 or not hwnd:
                return None
            gdi, u32 = ctypes.windll.gdi32, ctypes.windll.user32
            for fn in (u32.GetDC, gdi.CreateCompatibleDC, gdi.CreateCompatibleBitmap, gdi.SelectObject):
                fn.restype = ctypes.c_void_p
            gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
            gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_uint]
            gdi.DeleteObject.argtypes = gdi.DeleteDC.argtypes = [ctypes.c_void_p]
            u32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
            u32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            hdc = u32.GetDC(None)
            mem = gdi.CreateCompatibleDC(hdc)
            bmp = gdi.CreateCompatibleBitmap(hdc, ww, wh)
            gdi.SelectObject(mem, bmp)
            u32.PrintWindow(hwnd, mem, 2)                       # PW_RENDERFULLCONTENT
            hdr = (ctypes.c_uint32 * 10)(40, ww, (-wh) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
            full = ctypes.create_string_buffer(ww * wh * 4)
            gdi.GetDIBits(mem, bmp, 0, wh, full, hdr, 0)
            gdi.DeleteObject(bmp)
            gdi.DeleteDC(mem)
            u32.ReleaseDC(None, hdc)
            fr = full.raw
            raw = b"".join(fr[((oy + r) * ww + ox) * 4:((oy + r) * ww + ox + w) * 4] for r in range(h))
            table = bytes(int(v * factor + 12 * (1 - factor)) for v in range(256))
            rgb = bytearray(w * h * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
            rgb = bytes(rgb).translate(table)
            stride = w * 3
            rows = b"".join(b"\x00" + rgb[i:i + stride] for i in range(0, len(rgb), stride))
            import base64
            import struct

            def chunk(tag, data):
                return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                   + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b""))
            return tk.PhotoImage(master=self.top, data=base64.b64encode(png).decode(), format="png")
        except Exception:
            return None

    def destroy(self) -> None:
        for h in self._icons:
            try:
                _u32.DestroyIcon(h)
            except Exception:
                pass
        try:
            self.top.destroy()
        except tk.TclError:
            pass
