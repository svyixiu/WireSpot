"""WireSpotSetup.exe and the standalone uninstaller - same custom window and design as the app.

The installer is a short wizard:
  1. Welcome     what WireSpot is, what it changes on this PC, and checks
                 (WireGuard installed? an older WireSpot installed or running?)
  2. Options     where the program and your data go; desktop shortcut, Start
                 menu, Start with Windows, open when done; moving profiles from
                 an old portable folder
  3. Installing  each step ticked off as it happens
  4. Done        then Open WireSpot
Only one setup window runs at a time; a second launch brings it forward.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from . import APP_NAME, TAGLINE, VERSION, WEBSITE, TERMS_VERSION, PRIVACY_VERSION, installer, log, oplock, paths
from .chrome import CustomWindow, force_foreground
from .icons import IconSet
from .motion import Animator, ScrollArea
from .panel import Header, ItemRow, PanelView
from .theme import BG, CLAY, CRAIL, CREAM, DIM, LIGHT, RULE, SHIMMER, STONE, Fonts

W, H = 422, 600
SETUP_MUTEX = "Local\\WireSpot.Setup"
TITLE = "WireSpot Setup"


class _View(PanelView):
    def __init__(self, parent, fonts, iconset, anim, on_action):
        super().__init__(parent, None, fonts, iconset, anim, "window", on_action)


def _quiet_streams() -> None:
    """--noconsole builds have no stdout; ui output would fail. Discard it (the activity journal still gets it)."""
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _close_splash() -> None:
    try:
        import pyi_splash  # present only in a PyInstaller build with --splash

        pyi_splash.close()
    except Exception:
        pass


class SetupWindow:
    def __init__(self):
        _quiet_streams()
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.withdraw()
        self.f = Fonts(self.root)
        self.icons = IconSet(self.root)
        self.anim = Animator(self.root)
        self.events: queue.Queue = queue.Queue()
        ico = paths.PROGRAMDATA_DIR / "icons" / "setup.ico"
        try:
            from . import icon as brand

            brand.write_ico(ico, "live", sizes=(16, 32, 48))
        except OSError:
            ico = None
        self.win = CustomWindow(self.root, W, H, TITLE, ico, self.anim, on_close=lambda: self.close())
        self.win.place_default((self.root.winfo_screenwidth() - W) // 2, (self.root.winfo_screenheight() - H) // 2 - 30)
        body = self.win.body
        self.view = _View(body, self.f, self.icons, self.anim, self._click)
        self.header = Header(self.view, body, dots=True, on_close=self.close, on_minimize=self.win.minimize,
                             drag=self.win)
        self.header.pill(None, "", STONE)
        tk.Frame(body, bg=RULE, height=1).pack(fill="x", padx=10)
        self.footer = tk.Frame(body, bg=BG, height=44)
        self.footer.pack(side="bottom", fill="x", padx=16, pady=(6, 12))
        self.footer.pack_propagate(False)
        tk.Frame(body, bg=RULE, height=1).pack(side="bottom", fill="x", padx=10)
        self.agreement_bar = tk.Frame(body, bg=BG)
        self.host = tk.Frame(body, bg=BG)
        self.host.pack(fill="both", expand=True)
        self.page_frame: tk.Frame | None = None
        self.handlers: dict = {}
        self.current = None
        self.steps = None
        self.scroll = None
        self.spin_i = 0
        self.win.top.bind_all("<MouseWheel>", lambda e: self.scroll and self.scroll.wheel(e.delta))

    # ------------------------------------------------------------ pages
    def page(self, direction: int = 1) -> tk.Frame:
        """A new page that slides in (direction 1 = forward, -1 = back). Its content scrolls
        (mouse wheel) if it is taller than the window."""
        old = self.page_frame
        new = tk.Frame(self.host, bg=BG)
        self.page_frame = new
        self.handlers = {}
        self.scroll = ScrollArea(new, self.anim)
        self.scroll.pack(fill="both", expand=True)
        inner = tk.Frame(self.scroll.inner, bg=BG)
        inner.pack(fill="both", expand=True, pady=(0, 10))
        w = max(self.host.winfo_width(), W)
        if old is None:
            new.place(x=0, y=0, relwidth=1, relheight=1)
            return inner
        new.place(x=direction * w, y=0, relwidth=1, relheight=1)

        def step(t):
            new.place_configure(x=int(direction * w * (1 - t)))
            if old.winfo_exists():
                old.place_configure(x=int(-direction * w * 0.3 * t))
        self.anim.run("page", 260, step, done=lambda: old.winfo_exists() and old.destroy())
        return inner

    def hero(self, parent, title: str, sub: str, size: int = 44) -> None:
        box = tk.Frame(parent, bg=BG)
        box.pack(fill="x", pady=(16, 4))
        tk.Label(box, image=self.icons.logo(size, "live"), bg=BG).pack()
        self.title = tk.Label(box, text=title, font=self.f.h1, fg=CREAM, bg=BG)
        self.title.pack(pady=(8, 0))
        self.sub = tk.Label(box, text=sub, font=self.f.small, fg=STONE, bg=BG, wraplength=W - 60, justify="center")
        self.sub.pack(pady=(2, 0))

    def para(self, parent, text: str, fg=LIGHT, pady=(4, 4)) -> tk.Label:
        lb = tk.Label(parent, text=text, font=self.f.small, fg=fg, bg=BG, anchor="w", justify="left",
                      wraplength=W - 60)
        lb.pack(fill="x", padx=22, pady=pady)
        return lb

    def section(self, parent, title: str) -> None:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=22, pady=(10, 4))
        tk.Label(row, text=title.upper(), font=(self.f.mono, 8, "bold"), fg=CLAY, bg=BG).pack(side="left")
        tk.Frame(row, bg=RULE, height=1).pack(side="left", fill="x", expand=True, padx=(8, 0), pady=(2, 0))

    def point(self, parent, icon: str, text: str, color=CLAY, fg=LIGHT) -> None:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=22, pady=2)
        tk.Label(row, image=self.icons.get(icon, 14, color), bg=BG).pack(side="left", anchor="n", padx=(0, 8), pady=1)
        tk.Label(row, text=text, font=self.f.small, fg=fg, bg=BG, anchor="w", justify="left",
                 wraplength=W - 90).pack(side="left", fill="x")

    def option(self, parent, id_, icon, title, desc, on, cb, **kw) -> ItemRow:
        self.handlers[id_] = cb
        e = {"t": "item", "id": id_, "icon": icon, "text": title, "hint": "", "desc": desc, **kw}
        if on is not None:
            e["switch"] = bool(on)
        r = ItemRow(self.view, parent, e)
        r.frame.pack_configure(padx=12)
        return r

    def buttons(self, left=None, right=()) -> None:
        """Footer: an optional secondary button on the left, buttons on the right (last = primary)."""
        for w in self.footer.winfo_children():
            w.destroy()
        if left:
            text, icon, cb = left
            self.view.button(self.footer, text, icon, cb).pack(side="left", pady=8)
        for i, item in enumerate(reversed(list(right))):
            text, icon, cb = item[:3]
            enabled = item[3] if len(item) > 3 else True
            self.view.button(self.footer, text, icon, cb, primary=i == 0, enabled=enabled).pack(
                side="right", padx=(6, 0), pady=8)

    def progress_dots(self, n: int, of: int) -> None:
        box = tk.Frame(self.footer, bg=BG)
        box.place(relx=0.5, rely=0.5, anchor="center")
        for i in range(of):
            tk.Label(box, image=self.icons.get("dot" if i == n else "ring", 9, CLAY if i <= n else DIM),
                     bg=BG).pack(side="left", padx=2)

    def _click(self, id_: str) -> None:
        cb = self.handlers.get(id_)
        if cb:
            cb()

    # ------------------------------------------------------------ progress steps
    def step(self, text: str) -> None:
        if self.steps is None:
            return
        if self.current is not None:
            self._finish_step(True)
        row = tk.Frame(self.steps, bg=BG)
        row.pack(fill="x", pady=2)
        ic = tk.Label(row, image=self.view.spinner(0, CLAY, 14), bg=BG)
        ic.pack(side="left", padx=(0, 8), anchor="n")
        lb = tk.Label(row, text=text, font=self.f.text, fg=LIGHT, bg=BG, anchor="w", justify="left", wraplength=W - 100)
        lb.pack(side="left", fill="x")
        self.current = (ic, lb)

    def _finish_step(self, ok: bool, text: str | None = None) -> None:
        ic, lb = self.current
        ic.configure(image=self.icons.get("check" if ok else "x", 14, CLAY if ok else CRAIL))
        lb.configure(fg=STONE if ok else CRAIL)
        if text:
            lb.configure(text=text)
        self.current = None

    # ------------------------------------------------------------ loop
    def run_task(self, fn) -> None:
        def worker():
            try:
                self.events.put(("done", fn(lambda s: self.events.put(("step", s)))))
            except Exception as e:
                log.event("error", "setup: " + traceback.format_exc())
                self.events.put(("fail", e))
        threading.Thread(target=worker, daemon=True).start()

    def pump(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "step":
                    self.step(ev[1])
                else:
                    getattr(self, "on_" + ev[0])(ev[1])
        except queue.Empty:
            pass
        self.spin_i += 1
        if self.current is not None:
            try:
                self.current[0].configure(image=self.view.spinner(self.spin_i, CLAY, 14))
            except tk.TclError:
                self.current = None
        self.header.tick(self.spin_i)
        self.root.after(45, self.pump)

    def close(self) -> None:
        self.win.hide(lambda: self.root.after(30, self.root.destroy))

    def mainloop(self) -> None:
        _close_splash()
        self.win.show()
        self.root.after(45, self.pump)
        self.root.mainloop()


# ===================================================================== install
class Installer(SetupWindow):
    def __init__(self, payload: Path, near: Path):
        super().__init__()
        from . import autostart, wireguard

        self.payload, self.near = payload, near
        self.accepted = False
        self.result = None
        self.previous = installer.installed_version()
        self.legacy = installer.find_legacy(near)
        vpn = self.legacy / "vpn" if self.legacy else None
        self.n_legacy = len(list(vpn.glob("*.conf"))) if vpn and vpn.is_dir() else 0
        try:
            autostart_on = autostart.is_enabled()
        except Exception:
            autostart_on = False
        self.opts = {"desktop": True, "start_menu": True, "autostart": autostart_on, "open": True,
                     "migrate": bool(self.n_legacy)}
        self.wireguard = wireguard.find_wireguard("")
        self.welcome()

    # 1 ----------------------------------------------------------------
    def welcome(self, direction: int = 1) -> None:
        p = self.page(direction)
        upd = bool(self.previous) and self.previous != VERSION
        self.header.pill("info", "update" if upd else "setup", SHIMMER)
        self.hero(p, f"Welcome to {APP_NAME}", f"v{VERSION} · {TAGLINE}")
        self.para(p, "Share this PC's WireGuard (Proton VPN) connection with devices on a Windows Mobile "
                     "Hotspot. WireSpot keeps their internet traffic on the tunnel.", pady=(8, 2))
        self.section(p, "What it does on this PC")
        self.point(p, "shield", "Runs as administrator to create a WireGuard tunnel and start Mobile Hotspot.")
        self.point(p, "refresh", "Removes its network changes when you disconnect or uninstall.")
        self.point(p, "key", "Your VPN keys stay on this PC. Nothing is uploaded.")
        self.section(p, "Checks")
        if self.wireguard:
            self.point(p, "check", "WireGuard for Windows is installed.")
        else:
            self.point(p, "alert", "WireGuard for Windows is not installed. WireSpot installs fine, but you need "
                                   "it (wireguard.com/install) before going live.", SHIMMER, SHIMMER)
        if self.previous:
            self.point(p, "info", f"WireSpot {self.previous} is installed - this {'updates' if upd else 'reinstalls'} "
                                  "it. Your settings and VPN profiles are kept.")
        else:
            self.point(p, "check", "Nothing to replace - a fresh install.")
        if installer.app_running():
            self.point(p, "info", "WireSpot is running. Setup closes it; your VPN and hotspot keep running.")
        for child in self.agreement_bar.winfo_children():
            child.destroy()
        self.agreement_bar.pack_forget()
        self.agreement_bar.pack(side="bottom", fill="x", before=self.host)
        tk.Frame(self.agreement_bar, bg=RULE, height=1).pack(fill="x", padx=10, pady=(0, 3))
        row = tk.Frame(self.agreement_bar, bg=BG, cursor="hand2")
        row.pack(fill="x", padx=22)
        mark = tk.Label(row, image=self.icons.get("checkbox-on" if self.accepted else "checkbox", 18,
                                                  CLAY if self.accepted else STONE), bg=BG, cursor="hand2")
        self.agreement_checkbox = mark
        mark.pack(side="left", padx=(0, 8))
        label = tk.Label(row, text="I agree to the", font=self.f.small, fg=LIGHT, bg=BG, cursor="hand2")
        label.pack(side="left")
        links = tk.Frame(self.agreement_bar, bg=BG)
        links.pack(fill="x", padx=48)
        for title, path in (("Privacy Policy", "privacy"), ("Terms of Service", "terms")):
            if path == "terms":
                tk.Label(links, text="and the", font=self.f.small, fg=LIGHT, bg=BG).pack(side="left", padx=(5, 0))
            link = tk.Label(links, text=title, font=self.f.small, fg=CLAY, bg=BG, cursor="hand2")
            link.pack(side="left", padx=(0, 0))
            link.bind("<ButtonRelease-1>", lambda e, p=path: webbrowser.open(f"{WEBSITE}/{p}"))
        self.agreement_hint = self.para(self.agreement_bar,
                                        "Agree to both documents to continue." if not self.accepted else "",
                                        fg=DIM, pady=(0, 1))
        def toggle(_event=None):
            self.accepted = not self.accepted
            mark.configure(image=self.icons.get("checkbox-on" if self.accepted else "checkbox", 18,
                                                CLAY if self.accepted else STONE))
            self.agreement_hint.configure(text="" if self.accepted else "Agree to both documents to continue.")
            self._welcome_buttons()
        self.toggle_agreement = toggle
        for widget in (row, mark, label):
            widget.bind("<ButtonRelease-1>", toggle)
        self._welcome_buttons()

    def _welcome_buttons(self) -> None:
        self.buttons(left=("Cancel", "x", self.close),
                     right=[("Next", "chev-right", self.options, self.accepted)])
        self.progress_dots(0, 3)

    # 2 ----------------------------------------------------------------
    def options(self, direction: int = 1) -> None:
        if not self.accepted:
            return
        self.agreement_bar.pack_forget()
        p = self.page(direction)
        self.header.pill("sliders", "options", SHIMMER)
        tk.Frame(p, bg=BG, height=6).pack()
        self.section(p, "Where things go")
        for label, value in (("program", str(paths.install_dir())), ("your data", str(paths.appdata_dir()))):
            row = tk.Frame(p, bg=BG)
            row.pack(fill="x", padx=22, pady=1)
            tk.Label(row, text=f"{label:<10}", font=self.f.small, fg=STONE, bg=BG).pack(side="left", anchor="n")
            tk.Label(row, text=value, font=self.f.small, fg=LIGHT, bg=BG, anchor="w", justify="left",
                     wraplength=W - 150).pack(side="left", fill="x")
        self.para(p, "The data folder holds settings.json, your VPN profiles (vpn\\) and logs. WireSpot's Settings "
                     "page opens it.", fg=DIM, pady=(4, 0))
        self.section(p, "Options")

        def flip(k):
            return lambda: self.opts.__setitem__(k, not self.opts[k])

        self.option(p, "desktop", "window", "Desktop shortcut", "Opens the WireSpot app.", self.opts["desktop"],
                    flip("desktop"))
        self.option(p, "start_menu", "list", "Start menu entry", "Find WireSpot in Start.", self.opts["start_menu"],
                    flip("start_menu"))
        self.option(p, "autostart", "power", "Start with Windows", "In the tray at logon, no UAC prompt.",
                    self.opts["autostart"], flip("autostart"))
        if self.n_legacy:
            s = "s" if self.n_legacy != 1 else ""
            self.option(p, "migrate", "import", f"Bring over {self.n_legacy} VPN profile{s}",
                        f"Moved from {self.legacy.name}\\vpn, not copied.", self.opts["migrate"], flip("migrate"))
        self.option(p, "open", "play", "Open WireSpot when done", "", self.opts["open"], flip("open"))
        verb = "Update" if self.previous else "Install"
        self.buttons(left=("Back", "chev-left", lambda: self.welcome(-1)), right=[(verb, "import", self.install)])
        self.progress_dots(1, 3)

    # 3 ----------------------------------------------------------------
    def install(self, force_close: bool = False) -> None:
        if not self.accepted:
            return
        p = self.page(1)
        self.header.pill("clock", "installing", SHIMMER)
        self.hero(p, f"Installing {APP_NAME}", "This takes a few seconds.")
        self.steps = tk.Frame(p, bg=BG)
        self.steps.pack(fill="both", expand=True, padx=26, pady=(14, 0))
        self.current = None
        self.buttons()
        self.progress_dots(2, 3)
        o = self.opts
        self.run_task(lambda progress: installer.install(
            self.payload, progress=progress, legacy_near=self.near, desktop=o["desktop"], start_menu=o["start_menu"],
            start_with_windows=o["autostart"], migrate_legacy=o["migrate"], force_close=force_close,
            accepted={"terms": TERMS_VERSION, "privacy": PRIVACY_VERSION,
                      "at": datetime.now(timezone.utc).isoformat()}))

    def on_done(self, result) -> None:
        if self.current is not None:
            self._finish_step(True)
        self.result = result
        self.header.pill("check", "installed", CLAY)
        self.title.configure(text=f"{APP_NAME} is installed")
        bits = []
        if self.opts["desktop"]:
            bits.append("Shortcut on your desktop.")
        bits.append("The CLI opens from the app.")
        self.sub.configure(text=" ".join(bits))
        dest = Path(result["dest"])
        if self.opts["open"]:
            self._launch(dest)
            return
        self.buttons(left=("Open WireSpot", "play", lambda: self._launch(dest)), right=[("Done", "check", self.close)])

    def on_fail(self, err) -> None:
        if self.current is not None:
            self._finish_step(False)
        self.header.pill("alert", "failed", CRAIL)
        if isinstance(err, installer.AppStillRunning):
            self.title.configure(text="WireSpot is still running")
            self.sub.configure(text="An older WireSpot did not close by itself. Setup can close it; your VPN and "
                                    "hotspot keep running.", fg=SHIMMER)
            self.buttons(left=("Cancel", "x", self.close),
                         right=[("Close it and continue", "power", lambda: self.install(force_close=True))])
            return
        self.title.configure(text="Setup did not finish")
        self.sub.configure(text=str(err)[:160], fg=CRAIL)
        self.buttons(left=("Close", "x", self.close), right=[("Try again", "refresh", self.install)])

    def _launch(self, dest: Path) -> None:
        try:
            subprocess.Popen([str(dest / "WireSpot.exe")], cwd=str(dest))
        except OSError as e:
            self.on_fail(e)
            return
        self.root.after(900, self.close)             # let the "installed" page register before closing


def _single_instance() -> oplock.NamedMutex | None:
    """None if another setup/uninstall window is already open (it is brought forward instead)."""
    m = oplock.NamedMutex(SETUP_MUTEX)
    if m.acquire(0):
        return m
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.FindWindowW.restype = ctypes.c_void_p
        hwnd = ctypes.windll.user32.FindWindowW(None, TITLE)
        if hwnd:
            ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 9)
            force_foreground(hwnd)
    return None


def main(argv: list[str] | None = None) -> int:
    lock = _single_instance()
    if lock is None:
        _close_splash()
        return 0
    if getattr(sys, "frozen", False):
        payload = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "payload"
        near = Path(sys.executable).resolve().parent
    else:
        payload = Path((argv or sys.argv[1:] or ["dist"])[0]).resolve()
        near = paths.APP_DIR
    app = Installer(payload, near)
    app.mainloop()
    return 0 if app.result else 1


# ===================================================================== uninstall
class Uninstaller(SetupWindow):
    def __init__(self):
        super().__init__()
        self.ok = False
        self.keep = True
        p = self.page()
        self.header.pill("trash", "uninstall", CRAIL)
        self.hero(p, f"Uninstall {APP_NAME}?", f"v{VERSION}")
        self.section(p, "What happens")
        self.point(p, "power", "WireSpot first stops its own VPN tunnel, hotspot and DNS lock. Nothing else on this "
                               "PC is touched.")
        self.point(p, "trash", "Then it removes the program, its shortcuts, Start with Windows and its runtime data.")
        self.section(p, "Your settings and VPN profiles")
        self.choices = tk.Frame(p, bg=BG)
        self.choices.pack(fill="x")
        self._choice_rows()
        self.buttons(left=("Cancel", "x", self.close), right=[("Uninstall", "trash", self._go)])

    def _choice_rows(self) -> None:
        for w in self.choices.winfo_children():
            w.destroy()
        for id_, title, desc, sel in (
                ("keep", "Keep them", f"Stay in {paths.appdata_dir()} for a later reinstall.", self.keep),
                ("all", "Delete everything", "Removes the VPN profiles (private keys) too.", not self.keep)):
            self.option(self.choices, id_, "dot" if sel else "ring", title, desc, None,
                        lambda k=id_ == "keep": (setattr(self, "keep", k), self._choice_rows()),
                        primary=sel, danger=id_ == "all" and sel)

    def _go(self) -> None:
        keep = self.keep
        p = self.page(1)
        self.hero(p, f"Uninstalling {APP_NAME}", "")
        self.steps = tk.Frame(p, bg=BG)
        self.steps.pack(fill="both", expand=True, padx=26, pady=(14, 0))
        self.header.pill("clock", "removing", SHIMMER)
        self.buttons()

        def task(progress):
            from . import settings as settings_mod
            from .backend import Backend
            from .relay import Relay

            relay = Relay(Backend(), decide=lambda q, choices, default=0, cancel=None: choices[default].key)
            relay.gate_factory = None
            s, _ = settings_mod.load()
            return installer.uninstall(keep_data=keep, relay=relay, settings=s, progress=progress)
        self.run_task(task)

    def on_done(self, result) -> None:
        ok, msg = result
        if self.current is not None:
            self._finish_step(ok)
        self.ok = ok
        self.header.pill("check" if ok else "alert", "removed" if ok else "failed", CLAY if ok else CRAIL)
        self.title.configure(text=f"{APP_NAME} was removed" if ok else "Uninstall did not finish")
        self.sub.configure(text=msg[:160], fg=STONE if ok else CRAIL)
        self.buttons(right=[("Close", "check" if ok else "x", self.close)])

    def on_fail(self, err) -> None:
        self.on_done((False, str(err)))


def uninstall_ui() -> int:
    from . import ui

    lock = _single_instance()
    if lock is None:
        return 0
    ui.INTERACTIVE = False
    app = Uninstaller()
    app.mainloop()
    return 0 if app.ok else 1
