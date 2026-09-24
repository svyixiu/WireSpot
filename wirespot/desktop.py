"""WireSpot desktop application: main window + tray in one process.

    WireSpot.exe                 open the window (and the tray)
    WireSpot.exe --background    tray only (used by Start with Windows)
    WireSpot.exe --uninstall     uninstall (Apps & features)

A second launch just brings the running instance forward. The window closes
to the tray; "Quit WireSpot" in the tray exits (VPN/hotspot keep running,
nothing is torn down behind your back).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from . import icon, log, oplock, paths, settings as settings_mod



def _crash_log(kind: str, text: str) -> None:
    try:
        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(paths.LOG_DIR / "crash.log", "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} {kind}\n{log.redact(text)}\n")
    except OSError:
        pass
    log.event("error", f"{kind}: {text}")


def install_crash_handlers(root=None) -> None:
    sys.excepthook = lambda t, v, tb: _crash_log("unhandled", "".join(traceback.format_exception(t, v, tb)))
    threading.excepthook = lambda a: _crash_log(f"thread {a.thread.name if a.thread else '?'}",
                                                "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    if root is not None:
        root.report_callback_exception = lambda t, v, tb: _crash_log("ui callback",
                                                                     "".join(traceback.format_exception(t, v, tb)))


def ensure_icons() -> dict[str, Path]:
    d = paths.PROGRAMDATA_DIR / "icons" / f"v{icon.ICON_VERSION}"
    files = {}
    for st in icon.STYLES:
        f = d / f"{st}.ico"
        if not f.exists():
            icon.write_ico(f, st, sizes=(16, 20, 24, 32, 40, 48, 64))
        files[st] = f
    return files


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.name != "nt":
        return 2
    background = "--background" in argv or "--autostart" in argv
    from .tray import WINDOW_CLASS, WM_OPENWINDOW, WM_UNINSTALL, user32

    existing = user32.FindWindowW(WINDOW_CLASS, None)
    if existing:                                   # already running: bring it forward
        if "--uninstall" in argv:
            user32.PostMessageW(existing, WM_UNINSTALL, 0, 0)
        elif not background:
            user32.PostMessageW(existing, WM_OPENWINDOW, 0, 0)
        return 0
    from .winexec import is_admin

    if not is_admin() and not os.environ.get("WIRESPOT_NO_ELEVATE"):
        params = subprocess.list2cmdline(argv)
        exe = sys.executable
        if not getattr(sys, "frozen", False):
            params = subprocess.list2cmdline([str(Path(sys.argv[0]).resolve())]) + " " + params
        ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(paths.APP_DIR), 1)
        return 0
    instance = oplock.NamedMutex(oplock.TRAY)
    instance.acquire(0)
    install_crash_handlers()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    s, _ = settings_mod.load()
    if s["behavior"].get("debug"):
        log.enable()
    if "--uninstall" in argv:                      # Apps & features, app not running
        from .setup import uninstall_ui

        return uninstall_ui()
    return run(background, "--autostart" in argv)


def run(background: bool, autostarted: bool) -> int:
    import queue as _q
    import tkinter as tk

    from .controller import Controller, icon_state, tooltip
    from .icons import IconSet
    from .motion import Animator
    from .theme import Fonts
    from .tray import WM_OPENWINDOW, WM_QUITAPP, WM_UNINSTALL, NotifyIcon, Toaster, TrayPanel
    from .window import MainWindow

    ctl = Controller(autostarted=autostarted)
    root = tk.Tk()
    install_crash_handlers(root)
    root.withdraw()
    fonts = Fonts(root)
    iconset = IconSet(root)
    anim = Animator(root)
    files = ensure_icons()
    if files.get("live"):
        try:
            root.iconbitmap(default=str(files["live"]))
        except tk.TclError:
            pass
    notify = NotifyIcon(ctl.events, files)
    notify.extra_messages = {WM_OPENWINDOW: ("open",), WM_QUITAPP: ("quit",), WM_UNINSTALL: ("uninstall_prompt",)}
    state = {"quitting": False}

    def open_window(page=None):
        win.show(page)

    toaster = Toaster(root, fonts, iconset, on_click=lambda: open_window(), anim=anim)

    def quit_app():
        if state["quitting"]:
            return
        state["quitting"] = True
        panel.close()
        ctl.stop()
        notify.quit()
        if win.visible:
            win.win.hide(lambda: root.after(50, root.destroy))
        else:
            root.after(200, root.destroy)

    win = MainWindow(root, ctl, fonts, toaster, files.get("live"), iconset, quit_app)
    panel = TrayPanel(root, ctl, fonts, iconset, open_window=open_window, open_page=lambda p: open_window(p),
                      quit_app=quit_app, run_doctor=win.run_doctor, anim=anim)

    def pending_toast(c):
        name = c.get("name") if c.get("name") and c.get("name") != "(no name)" else c.get("device", "A device")
        toaster.toast("New device wants to join", f"{name} · {c.get('ip') or 'no IP yet'} - it has no network until you allow it.",
                      icon="user", actions=[("Allow", "check", lambda: ctl.device_verdict(c["mac"], "approve", name), True),
                                            ("Block", "ban", lambda: ctl.device_verdict(c["mac"], "block", name), False),
                                            ("Details", "phone", lambda: open_window("devices"), False)])

    def publish():
        notify.set(icon_state(ctl.snap), tooltip(ctl.snap))
        panel.render()
        win.on_snap()

    def pump():
        if state["quitting"]:
            return
        try:
            while True:
                ev = ctl.events.get_nowait()
                kind = ev[0]
                if kind == "snap":
                    if len(ev) > 1:
                        ctl.accept_snapshot(ev[1])
                    publish()
                elif kind == "busy":                       # the CLI started/finished an operation
                    ctl.snap = {**ctl.snap, "busy": ctl.busy_label()}
                    publish()
                elif kind == "panel":
                    panel.toggle(ev[1], ev[2])
                elif kind == "open":
                    open_window()
                elif kind == "quit":
                    quit_app()
                elif kind == "uninstall_prompt":
                    win.ask_uninstall()
                elif kind == "toast":
                    toaster.toast(ev[1], ev[2], error=ev[3])
                elif kind == "pending":
                    pending_toast(ev[1])
                elif kind == "notify":
                    title, icon_name = {"joined": ("Device joined", "phone"), "left": ("Device left", "phone"),
                                        "fail": ("Hotspot stopped", "alert"),
                                        "hotspot_off": ("Hotspot turned off", "wifi")}.get(ev[1], ("WireSpot", None))
                    toaster.toast(title, ev[2], error=ev[1] == "fail", icon=icon_name)
                    ctl.refresh_now()
                elif kind == "report":
                    win.show_report(ev[1], ev[2])
                elif kind == "ask":
                    win.ask(ev[1], ev[2], ev[3], ev[4])
                elif kind == "inbox":
                    win.queue_review(ev[1])
                elif kind == "clipboard":
                    root.clipboard_clear()
                    root.clipboard_append(ev[1])
                elif kind == "action":
                    ctl.do(ev[1], ev[2])
                elif kind == "uninstalled":
                    toaster.toast("WireSpot was uninstalled", ev[1], icon="check", seconds=4)
                    root.after(2500, quit_app)
        except _q.Empty:
            pass
        except Exception:
            _crash_log("pump", traceback.format_exc())
        try:
            panel.animate()
            win.animate()
        except Exception:
            _crash_log("animate", traceback.format_exc())
        root.after(45, pump)

    notify.start()
    notify.ready.wait(5)
    ctl.start()
    if not background:
        open_window()
    root.after(45, pump)
    root.mainloop()
    return 0
