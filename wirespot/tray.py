"""WireSpot tray: notification-area icon, custom tray menu and toasts.

* The icon is a real Shell_NotifyIcon icon (ctypes, own message thread) that
  changes with the state: live / vpn / idle / busy / paused / error.
* Left or right click opens the custom menu - the same panel as the WireSpot
  window (panel.py), in the Claude palette with SVG icons. It stays open
  until you click elsewhere or press Esc, and updates in place (no blinking).
* Toasts (also custom) announce devices joining / waiting for approval,
  guard fail-closed events and the result of actions - including actions
  taken in the CLI (sync.py).
"""
from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
from ctypes import wintypes
from pathlib import Path

from . import APP_NAME, log
from .controller import Activity, fmt_duration, icon_state, tooltip
from .motion import Animator, ScrollArea, hover
from .theme import (BG, BORDER, CLAY, CRAIL, CREAM, DIM, LIGHT, ROW_HOVER, RULE, SELECTED, SHIMMER, Fonts,
                    dark_titlebar)

__all__ = ["Activity", "NotifyIcon", "Toaster", "TrayPanel", "fmt_duration", "icon_state", "place",
           "signal_existing_tray", "status_rows", "tooltip"]

# ------------------------------------------------------------------ Win32
user32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
shell32 = ctypes.WinDLL("shell32") if os.name == "nt" else None
kernel32 = ctypes.WinDLL("kernel32") if os.name == "nt" else None

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM) if os.name == "nt" else None
WM_APP = 0x8000
WM_TRAY, WM_REFRESH, WM_SHOWPANEL, WM_QUITTRAY = WM_APP + 1, WM_APP + 2, WM_APP + 3, WM_APP + 4
WM_OPENWINDOW, WM_QUITAPP, WM_UNINSTALL = WM_APP + 5, WM_APP + 6, WM_APP + 7    # from other WireSpot processes
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_SHOWTIP = 0x1, 0x2, 0x4, 0x80
# WIRESPOT_INSTANCE lets a test copy run beside your real WireSpot without handing off to it.
WINDOW_CLASS = "WireSpotTrayWindow" + os.environ.get("WIRESPOT_INSTANCE", "")


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


if os.name == "nt":
    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", ctypes.c_wchar * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                    ("szInfo", ctypes.c_wchar * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", ctypes.c_wchar * 64),
                    ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.FindWindowW.restype = wintypes.HWND
    user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def cursor_pos() -> tuple[int, int]:
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def work_area(x: int, y: int) -> tuple[int, int, int, int]:
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    mon = user32.MonitorFromPoint(POINT(x, y), 2)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        r = mi.rcWork
        return r.left, r.top, r.right, r.bottom
    return 0, 0, 1920, 1040


def place(x: int, y: int, w: int, h: int) -> tuple[int, int]:
    """Anchor a popup of size w×h to a tray click at (x, y), inside the work area."""
    left, top, right, bottom = work_area(x, y)
    px = min(max(x - w // 2, left + 8), right - w - 8)
    if y >= bottom - 60:          # taskbar at the bottom (normal)
        py = bottom - h - 8
    elif y <= top + 60:           # taskbar at the top
        py = top + 8
    else:                         # taskbar left/right
        py = min(max(y - h // 2, top + 8), bottom - h - 8)
        px = right - w - 8 if x > (left + right) // 2 else left + 8
    return px, py


class NotifyIcon(threading.Thread):
    """Owns the hidden window + notification-area icon; forwards clicks to a queue."""

    def __init__(self, events: queue.Queue, icon_files: dict[str, Path]):
        super().__init__(daemon=True, name="wirespot-notify")
        self.events = events
        self.icon_files = icon_files
        self.icons: dict[str, int] = {}
        self.hwnd = None
        self.pending = ("idle", APP_NAME)
        self.shown = False
        self.ready = threading.Event()
        self.extra_messages: dict[int, tuple] = {}     # custom window message -> event

    def set(self, state: str, tip: str) -> None:
        new = (state, tip[:127])
        if new == self.pending and self.shown:
            return                      # re-sending an unchanged icon makes Explorer redraw it (a blink)
        self.pending = new
        if self.hwnd:
            self.shown = True
            user32.PostMessageW(self.hwnd, WM_REFRESH, 0, 0)

    def quit(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_QUITTRAY, 0, 0)

    def _data(self, flags: int) -> "NOTIFYICONDATAW":
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY
        state, tip = self.pending
        nid.hIcon = self.icons.get(state) or self.icons.get("idle")
        nid.szTip = tip
        return nid

    def _add(self) -> None:
        nid = self._data(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_TRAY:
                ev = lp & 0xFFFF
                if ev in (WM_LBUTTONUP, WM_RBUTTONUP):
                    self.events.put(("panel", *cursor_pos()))
                elif ev == WM_LBUTTONDBLCLK:
                    self.events.put(("open",))
                return 0
            if msg == WM_REFRESH:
                nid = self._data(NIF_ICON | NIF_TIP | NIF_SHOWTIP)
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
                return 0
            if msg in self.extra_messages:
                self.events.put(self.extra_messages[msg])
                return 0
            if msg == WM_SHOWPANEL:
                self.events.put(("panel", *cursor_pos()))
                return 0
            if msg == self.taskbar_created:            # Explorer restarted
                self._add()
                return 0
            if msg == WM_QUITTRAY:
                nid = self._data(0)
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                user32.PostQuitMessage(0)
                return 0
        except Exception as e:
            log.event("error", f"tray wndproc: {e}")
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def run(self) -> None:
        self._proc = WNDPROC(self._wndproc)                # keep a reference
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = WINDOW_CLASS
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, WINDOW_CLASS, APP_NAME, 0, 0, 0, 0, 0, None, None, hinst, None)
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        size = user32.GetSystemMetrics(49)                  # SM_CXSMICON (DPI aware)
        for state, f in self.icon_files.items():
            self.icons[state] = user32.LoadImageW(None, str(f), 1, size, size, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
        self._add()
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        for h in self.icons.values():
            user32.DestroyIcon(h)


def signal_existing_tray() -> bool:
    """If a tray already runs, ask it to open its panel. True if one was found."""
    hwnd = user32.FindWindowW(WINDOW_CLASS, None)
    if hwnd:
        user32.PostMessageW(hwnd, WM_SHOWPANEL, 0, 0)
        return True
    return False


# ------------------------------------------------------------------ toasts
class Toaster:
    """Custom notifications, bottom-right, in the panel's design (SVG icons, optional buttons)."""

    def __init__(self, root, fonts: Fonts, iconset=None, on_click=None, anim: Animator | None = None):
        self.root, self.f, self.on_click = root, fonts, on_click
        self.icons = iconset
        self.anim = anim or Animator(root)
        self.toasts: list = []

    def toast(self, title: str, message: str = "", error: bool = False, icon: str | None = None,
              actions: list | None = None, seconds: float | None = None) -> None:
        tk = __import__("tkinter")
        message = message[:1].upper() + message[1:] if message else ""
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=BORDER)
        body = tk.Frame(top, bg=BG)
        body.pack(padx=1, pady=1, fill="both", expand=True)
        accent = CRAIL if error else CLAY
        tk.Frame(body, bg=accent, width=3).pack(side="left", fill="y")
        inner = tk.Frame(body, bg=BG)
        inner.pack(side="left", fill="both", expand=True, padx=12, pady=10)
        head = tk.Frame(inner, bg=BG)
        head.pack(fill="x")
        name = icon or ("alert" if error else "bolt")
        if self.icons is not None:
            tk.Label(head, image=self.icons.get(name, 15, accent), bg=BG).pack(side="left", padx=(0, 7))
        tk.Label(head, text=title, font=self.f.bold, fg=CREAM, bg=BG).pack(side="left")
        tk.Label(head, text=time.strftime("%H:%M"), font=self.f.small, fg=DIM, bg=BG).pack(side="right")
        if message:
            tk.Label(inner, text=message, font=self.f.small, fg=LIGHT, bg=BG, justify="left",
                     wraplength=300, anchor="w").pack(fill="x", pady=(4, 0))

        def close():
            if top.winfo_exists():
                top.destroy()

        if actions:
            row = tk.Frame(inner, bg=BG)
            row.pack(fill="x", pady=(8, 0))
            for label, icon_name, cb, primary in actions:
                self._button(row, label, icon_name, lambda cb=cb: (close(), cb()), primary).pack(side="left", padx=(0, 6))
        top.update_idletasks()
        w, h = max(340, top.winfo_reqwidth()), top.winfo_reqheight()
        left, tp, right, bottom = work_area(*cursor_pos())
        self.toasts = [t for t in self.toasts if t.winfo_exists()]
        offset = sum(t.winfo_height() + 8 for t in self.toasts)
        x, y = right - w - 12, bottom - h - 12 - offset
        top.geometry(f"{w}x{h}+{x}+{y + 12}")
        top.attributes("-alpha", 0.0)
        top.deiconify()
        dark_titlebar(top)
        self.toasts.append(top)
        self.anim.run(("toast-in", id(top)), 220,
                      lambda t: (top.attributes("-alpha", t), top.geometry(f"+{x}+{int(y + 12 * (1 - t))}")))
        if not actions:
            for wdg in (top, body, inner, head):
                wdg.bind("<ButtonRelease-1>", lambda e: (close(), self.on_click and self.on_click()))

        def fade():
            if top.winfo_exists():
                self.anim.run(("toast-out", id(top)), 380, lambda t: top.attributes("-alpha", 1 - t), done=close,
                              curve=lambda t: t)

        top.after(int((seconds or (30 if actions else 10 if error else 6.5)) * 1000), fade)

    def _button(self, parent, text, icon_name, command, primary):
        tk = __import__("tkinter")
        bg, hover_bg = (CLAY, SHIMMER) if primary else (SELECTED, ROW_HOVER)
        fg = BG if primary else LIGHT
        b = tk.Frame(parent, bg=bg, cursor="hand2")
        ws = [b]
        if self.icons is not None:
            ic = tk.Label(b, image=self.icons.get(icon_name, 12, fg), bg=bg)
            ic.pack(side="left", padx=(8, 3), pady=3)
            ws.append(ic)
        lb = tk.Label(b, text=text, font=(self.f.mono, 9, "bold"), fg=fg, bg=bg)
        lb.pack(side="left", padx=(0, 9), pady=3)
        ws.append(lb)
        hover(b, ws, lambda: self.anim.fade(ws, b.cget("bg"), hover_bg, 100, key=("tbtn", id(b))),
              lambda: self.anim.fade(ws, b.cget("bg"), bg, 150, key=("tbtn", id(b))))
        for w in ws:
            w.bind("<ButtonRelease-1>", lambda e: command())
        return b


# ------------------------------------------------------------------ tray panel
VK_LBUTTON, VK_RBUTTON, VK_MBUTTON = 0x01, 0x02, 0x04


class TrayPanel:
    """The custom tray menu: the shared panel (panel.py) in a borderless popup.

    It stays open while you use it and closes only when you click somewhere
    else (or press Esc) - it does not close on focus changes or after an
    action. Live values update in place, so it never blinks.
    """

    is_tray = True

    def __init__(self, root, ctl, fonts: Fonts, iconset, open_window, open_page, quit_app, run_doctor,
                 anim: Animator | None = None):
        self.root, self.ctl, self.f, self.icons = root, ctl, fonts, iconset
        self._open_window, self._open_page, self._quit, self._run_doctor = open_window, open_page, quit_app, run_doctor
        self.anim = anim or Animator(root)
        self.top = None
        self.anchor = (0, 0)
        self.closed_at = 0.0
        self.spin_i = 0
        self._buttons_down = False

    @property
    def is_open(self) -> bool:
        return self.top is not None

    # host interface for panel.dispatch
    def open_window(self, page=None):
        self.close()
        self._open_window(page)

    def open_page(self, page):
        self.close()
        self._open_page(page)

    def quit_app(self):
        self.close()
        self._quit()

    def run_doctor(self, section):
        self._run_doctor(section)

    # ------------------------------------------------------------ open / close
    def toggle(self, x: int, y: int) -> None:
        if self.top is not None:
            self.close()
            return
        if time.time() - self.closed_at < 0.4:
            return                     # this click on the tray icon is what just closed the panel
        self.open(x, y)

    def open(self, x: int, y: int) -> None:
        tk = __import__("tkinter")
        from .panel import WIDTH, Header, PanelView

        self.anchor = (x, y)
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=BORDER)
        self.top = top
        outer = tk.Frame(top, bg=BG)
        outer.pack(padx=1, pady=1, fill="both", expand=True)
        self.view = PanelView(outer, self.ctl, self.f, self.icons, self.anim, "tray", self._action)
        self.header = Header(self.view, outer)
        tk.Frame(outer, bg=RULE, height=1).pack(fill="x", padx=10)
        self.scroll = ScrollArea(outer, self.anim)
        self.scroll.pack(fill="both", expand=True)
        self.view.parent = self.scroll.inner
        self.header.update(icon_state(self.ctl.snap))
        top.bind("<Escape>", lambda e: self.close())
        top.bind("<MouseWheel>", lambda e: self.scroll.wheel(e.delta))
        self.ctl.fast_poll = True
        self.view.refresh(place=self._place)
        top.deiconify()
        dark_titlebar(top)
        px, py = self.pos
        top.attributes("-alpha", 0.0)
        self.anim.run("tray-in", 170, lambda t: (top.attributes("-alpha", t),
                                                 top.geometry(f"+{px}+{int(py + 10 * (1 - t))}")))
        top.focus_force()
        try:
            user32.SetForegroundWindow(int(top.wm_frame(), 16))
        except Exception:
            pass
        self._buttons_down = True          # ignore the click that opened us
        self._watch_clicks()
        self.ctl.refresh_now()

    def _place(self, new_frame) -> None:
        """Size + position the popup for ``new_frame`` before it is shown (no flash)."""
        from .panel import WIDTH

        head = self.header.frame.winfo_reqheight() + 18
        left, top_, right, bottom = work_area(*self.anchor)
        max_h = bottom - top_ - 16
        body_h = min(new_frame.winfo_reqheight(), max_h - head)
        h = head + body_h + 2
        self.scroll.canvas.configure(height=body_h)
        self.pos = place(self.anchor[0], self.anchor[1], WIDTH + 2, h)
        self.top.geometry(f"{WIDTH + 2}x{h}+{self.pos[0]}+{self.pos[1]}")

    def close(self) -> None:
        if self.top is None:
            return
        top, self.top = self.top, None
        self.closed_at = time.time()
        self.ctl.fast_poll = self.ctl.window_visible
        self.anim.run("tray-out", 110, lambda t: top.attributes("-alpha", 1 - t),
                      done=lambda: top.destroy(), curve=lambda t: t)

    def _watch_clicks(self) -> None:
        """Close on a mouse press outside the panel (polled; focus changes don't close it)."""
        if self.top is None:
            return
        down = any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_LBUTTON, VK_RBUTTON, VK_MBUTTON))
        if down and not self._buttons_down:
            x, y = cursor_pos()
            t = self.top
            inside = (t.winfo_rootx() <= x < t.winfo_rootx() + t.winfo_width()
                      and t.winfo_rooty() <= y < t.winfo_rooty() + t.winfo_height())
            if not inside:
                self._buttons_down = down
                self.close()
                return
        self._buttons_down = down
        self.root.after(25, self._watch_clicks)

    # ------------------------------------------------------------ content
    def _action(self, id_: str) -> None:
        from .panel import dispatch

        if dispatch(self.ctl, id_, self):
            self.close()
        else:
            self.render()

    def render(self) -> None:
        if self.top is None:
            return
        self.header.update(icon_state(self.ctl.snap))
        self.view.refresh(place=self._resize)

    def _resize(self, new_frame) -> None:
        self._place(new_frame)

    def animate(self) -> None:
        if self.top is None:
            return
        self.spin_i += 1
        self.view.tick(self.spin_i)
        self.header.tick(self.spin_i)


def status_rows(snap: dict) -> list[tuple[str, str]]:
    from .panel import status_rows as rows

    return rows(snap)
