"""WireSpot window - the tray menu, as an app.

    (x)(-)  [logo] WireSpot v0.2.0            (o) live
    ────────────────────────────────────────────────
    the same panel as the tray menu (panel.py)
    Devices · Profiles · Hotspot · Checks · Activity · Settings  -> pages slide in

Fully custom chrome (chrome.py): no Windows caption, fixed size, two
mac-style dots (close = hide to the tray, minimise), drag anywhere on the
header. Everything moves with eased animation: pages slide, rows fade on
hover, switches slide, sheets rise from the bottom, scrolling glides.
Every symbol is an SVG icon (icons.py).
"""
from __future__ import annotations

import os
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

from . import APP_NAME, VERSION, log, paths, settings as settings_mod, sync
from .chrome import CustomWindow
from .controller import Controller, icon_state
from .inbox import Candidate
from .motion import Animator, ScrollArea, lerp_color
from .panel import WIDTH, BarRow, Header, ItemRow, PanelView, device_icon, dispatch, split_glyph, status_rows
from .theme import (BG, BORDER, CLAY, CRAIL, CREAM, DIM, INPUT, LIGHT, ROW_HOVER, RULE, SELECTED, SHIMMER, STONE,
                    SURFACE, Fonts)

HEIGHT = 780
PAGE_TITLES = {"devices": ("Devices", "Who is on the hotspot, and who may use it."),
               "profiles": ("Profiles", "Proton WireGuard configs. New .conf files in Downloads are offered automatically."),
               "hotspot": ("Hotspot", "What phones see. Applies the next time the hotspot starts."),
               "checks": ("Checks", "Read-only diagnostics. Nothing here changes your network."),
               "activity": ("Activity", "What WireSpot did - in this app and in the CLI. Keys and passwords are never shown."),
               "settings": ("Settings", "")}
ALIASES = {"dashboard": "home", "diagnostics": "checks", "log": "activity"}
DOCTOR_SECTIONS = [("quick", "Quick"), ("wifi", "Wi-Fi"), ("vpn", "VPN"), ("hotspot", "Hotspot"),
                   ("ics", "Sharing"), ("network", "Network"), ("clients", "Devices"), ("full", "Full")]
TABS = [("home", "", "Home")] + [(k, "", t[0]) for k, t in PAGE_TITLES.items()]      # page keys (tests)


class PageView(PanelView):
    """Rows on a page reuse the panel's row widgets; clicks go to the page's handler."""

    def __init__(self, parent, ctl, fonts, iconset, anim, on_action):
        super().__init__(parent, ctl, fonts, iconset, anim, "window", on_action)

    def row(self, parent, id_, icon, text, hint="", **kw) -> ItemRow:
        return ItemRow(self, parent, {"t": "item", "id": id_, "icon": icon, "text": text, "hint": hint, **kw})


class MainWindow:
    is_tray = False

    def __init__(self, root: tk.Tk, ctl: Controller, fonts: Fonts, toaster, icon_path: Path | None = None,
                 iconset=None, quit_app=None):
        from .icons import IconSet

        self.root, self.ctl, self.f, self.toaster = root, ctl, fonts, toaster
        self.icons = iconset or IconSet(root)
        self.anim = Animator(root)
        self._quit = quit_app or (lambda: None)
        self.page = "home"
        self.page_frame: tk.Frame | None = None
        self.page_scroll: ScrollArea | None = None
        self.page_view: PageView | None = None
        self.page_sig = None
        self.visible = False
        self.spin_i = 0
        self.report = ("", [])
        self.report_view = None
        self.activity_view = None
        self.activity_stamp = None
        self.pending_reviews: list[Candidate] = []
        self.hinted_tray = False
        self.modal_open = None
        self.live: dict = {}

        area = self._work_area()
        h = min(HEIGHT, area[3] - area[1] - 40)
        self.win = CustomWindow(root, WIDTH + 2, h, APP_NAME, icon_path, self.anim, on_focus=self._focus,
                                on_close=self.hide)
        self.win.place_default(area[2] - WIDTH - 26, area[1] + max(12, (area[3] - area[1] - h) // 2))
        self.top = self.win.top
        body = self.win.body
        self.home_view = PanelView(body, ctl, fonts, self.icons, self.anim, "window", self._home_action)
        self.header = Header(self.home_view, body, dots=True, on_close=self.hide, on_minimize=self.win.minimize,
                             drag=self.win)
        tk.Frame(body, bg=RULE, height=1).pack(fill="x", padx=10)
        self.host = tk.Frame(body, bg=BG)
        self.host.pack(fill="both", expand=True)
        self.home = tk.Frame(self.host, bg=BG)
        self.home.place(x=0, y=0, relwidth=1, relheight=1)
        self.home_scroll = ScrollArea(self.home, self.anim)
        self.home_scroll.pack(fill="both", expand=True)
        self.home_view.parent = self.home_scroll.inner
        self.home_view.refresh()
        self.header.update(icon_state(ctl.snap))

        self.top.bind_all("<MouseWheel>", self._wheel, add="+")
        self.top.bind("<Escape>", lambda e: self.back() if self.page != "home" and not self.modal_open else None)
        self.top.bind("<Control-w>", lambda e: self.hide())

    @staticmethod
    def _work_area():
        try:
            from .tray import cursor_pos, work_area
            return work_area(*cursor_pos())
        except Exception:
            return 0, 0, 1920, 1040

    def _focus(self, active: bool) -> None:
        self.header.set_active(active)

    def _wheel(self, e):
        w = e.widget
        try:
            if isinstance(w, tk.Text) or w.winfo_toplevel() is not self.top:
                return
        except (tk.TclError, AttributeError):
            return
        area = self.page_scroll if self.page != "home" else self.home_scroll
        if area is not None:
            area.wheel(e.delta)

    # ================================================================= lifecycle
    def show(self, page: str | None = None) -> None:
        self.win.show()
        self.visible = True
        self.ctl.window_visible = True
        self.ctl.fast_poll = True
        page = ALIASES.get(page, page)
        if page and page != self.page:
            self.open_page(page) if page != "home" else self.back()
        if self.pending_reviews:
            self.root.after(350, self._next_review)
        self.on_snap()

    def hide(self) -> None:
        self.win.hide()
        self.visible = False
        self.ctl.window_visible = False
        self.ctl.fast_poll = False
        if not self.hinted_tray:
            self.hinted_tray = True
            self.toaster.toast("WireSpot is still running", "It keeps guarding the hotspot from the tray. "
                               "Use Quit WireSpot to exit.", icon="info")

    def destroy(self) -> None:
        self.win.destroy()

    # host interface for panel.dispatch
    def open_window(self, page=None):
        self.show(page)

    def quit_app(self):
        self._quit()

    def run_doctor(self, section: str) -> None:
        self.report = (section, ["✻ Running…"])
        self._fill_report()
        self.ctl.do("doctor", section)

    def _home_action(self, id_: str) -> None:
        dispatch(self.ctl, id_, self)
        self.home_view.refresh()

    # ================================================================= pages
    def show_page(self, key: str, animate: bool = True) -> None:          # compatibility
        key = ALIASES.get(key, key)
        if key == "home":
            self.back(animate)
        else:
            self.open_page(key, animate)

    def open_page(self, key: str, animate: bool = True) -> None:
        key = ALIASES.get(key, key)
        if key == "home":
            return self.back(animate)
        if not self.visible:
            self.win.show()
            self.visible = self.ctl.window_visible = self.ctl.fast_poll = True
        old = self.page_frame if self.page != "home" else self.home
        same = key == self.page
        self.page = key
        frame = self._build_page(key)
        w = max(self.host.winfo_width(), WIDTH)
        if not animate or same:
            frame.place(x=0, y=0, relwidth=1, relheight=1)
            if old is not None and old is not self.home:
                old.destroy()
            return
        frame.place(x=w, y=0, relwidth=1, relheight=1)

        def step(t):
            frame.place_configure(x=int(w * (1 - t)))
            if old.winfo_exists():
                old.place_configure(x=int(-w * 0.3 * t))

        def done():
            if old is not self.home and old.winfo_exists():
                old.destroy()
        self.anim.run("page", 260, step, done=done)

    def back(self, animate: bool = True) -> None:
        if self.page == "home":
            return
        old = self.page_frame
        self.page = "home"
        self.page_frame = self.page_scroll = self.page_view = None
        self.report_view = self.activity_view = None
        self.home_view.refresh()
        w = max(self.host.winfo_width(), WIDTH)
        if not animate or old is None:
            self.home.place_configure(x=0)
            if old is not None:
                old.destroy()
            return
        self.home.lift()

        def step(t):
            self.home.place_configure(x=int(-w * 0.3 * (1 - t)))
            if old.winfo_exists():
                old.place_configure(x=int(w * t))
                old.lift()
        self.anim.run("page", 240, step, done=lambda: old.winfo_exists() and old.destroy())

    def _build_page(self, key: str) -> tk.Frame:
        frame = tk.Frame(self.host, bg=BG)
        self.page_frame = frame
        self.report_view = self.activity_view = None
        bar = tk.Frame(frame, bg=BG)
        bar.pack(fill="x", padx=6, pady=(6, 0))
        self.page_view = PageView(frame, self.ctl, self.f, self.icons, self.anim, self._page_action)
        back = self.page_view.row(bar, "back", "chev-left", "Back", PAGE_TITLES[key][0].lower())
        back.frame.pack_configure(padx=0)
        scrolling = key not in ("checks", "activity")
        if scrolling:
            self.page_scroll = ScrollArea(frame, self.anim)
            self.page_scroll.pack(fill="both", expand=True)
            inner = tk.Frame(self.page_scroll.inner, bg=BG)
            inner.pack(fill="both", expand=True, padx=16, pady=(6, 16))
        else:
            self.page_scroll = None
            inner = tk.Frame(frame, bg=BG)
            inner.pack(fill="both", expand=True, padx=16, pady=(6, 16))
        title, sub = PAGE_TITLES[key]
        tk.Label(inner, text=title, font=self.f.h1, fg=CREAM, bg=BG, anchor="w").pack(fill="x")
        if sub:
            tk.Label(inner, text=sub, font=self.f.small, fg=STONE, bg=BG, anchor="w", justify="left",
                     wraplength=WIDTH - 40).pack(fill="x", pady=(2, 8))
        self.page_body = inner
        getattr(self, f"_page_{key}")(inner)
        self.page_sig = self._page_signature(key)
        return frame

    def _page_action(self, id_: str) -> None:
        if id_ == "back":
            return self.back()
        handler = self.page_actions.get(id_)
        if handler:
            handler()

    def _page_signature(self, key: str):
        s = self.ctl.snap
        if key == "devices":
            return tuple((c.mac, c.ip, getattr(c, "access", "")) for c in s.get("clients_full") or []), \
                len(__import__("wirespot.admission", fromlist=["load"]).load()["approved"])
        if key == "settings":
            return s.get("autostart"), tuple(sorted((s.get("settings") or {}).get("behavior", {}).items()))
        if key == "profiles":
            return tuple(s.get("profiles") or []), (s.get("settings") or {}).get("vpn", {}).get("default_profile")
        return None

    # ----------------------------------------------------------------- small widgets
    def _section(self, parent, title: str) -> None:
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(12, 4))
        tk.Label(row, text=title.upper(), font=(self.f.mono, 8, "bold"), fg=CLAY, bg=BG).pack(side="left")
        tk.Frame(row, bg=RULE, height=1).pack(side="left", fill="x", expand=True, padx=(8, 0), pady=(2, 0))

    def _kv(self, parent, key: str, value: str, width: int = 9, vfg=LIGHT) -> tk.Label:
        bg = parent["bg"]
        r = tk.Frame(parent, bg=bg)
        r.pack(fill="x", pady=1)
        tk.Label(r, text=f"{key:<{width}}", font=self.f.text, fg=STONE, bg=bg).pack(side="left", anchor="n")
        v = tk.Label(r, text=value, font=self.f.text, fg=vfg, bg=bg, anchor="w", justify="left",
                     wraplength=WIDTH - (200 if bg == SURFACE else 150))
        v.pack(side="left", fill="x")
        return v

    def _card(self, parent, pad=12) -> tk.Frame:
        outer = tk.Frame(parent, bg=BORDER)
        outer.pack(fill="x", pady=(0, 8))
        inner = tk.Frame(outer, bg=SURFACE)
        inner.pack(fill="x", padx=1, pady=1)
        body = tk.Frame(inner, bg=SURFACE)
        body.pack(fill="x", padx=pad, pady=pad - 3)
        return body

    def _chips(self, parent, options, value, on_change) -> tk.Frame:
        f = tk.Frame(parent, bg=BG)
        for key, label in options:
            sel = key == value
            lb = tk.Label(f, text=label, font=self.f.bold if sel else self.f.small, fg=BG if sel else LIGHT,
                          bg=CLAY if sel else SURFACE, padx=10, pady=4, cursor="hand2")
            lb.pack(side="left", padx=(0, 4))
            lb.bind("<ButtonRelease-1>", lambda e, k=key: on_change(k))
            if not sel:
                lb.bind("<Enter>", lambda e, w=lb: self.anim.fade((w,), SURFACE, ROW_HOVER, 100))
                lb.bind("<Leave>", lambda e, w=lb: self.anim.fade((w,), ROW_HOVER, SURFACE, 140))
        return f

    def _entry(self, parent, var: tk.StringVar, show: str = "") -> tk.Entry:
        return tk.Entry(parent, textvariable=var, show=show, font=self.f.text, bg=INPUT, fg=CREAM,
                        insertbackground=CLAY, relief="flat", highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=CLAY)

    def _text_view(self, parent) -> tk.Text:
        wrap = tk.Frame(parent, bg=BORDER)
        wrap.pack(fill="both", expand=True)
        t = tk.Text(wrap, bg=INPUT, fg=LIGHT, font=self.f.small, bd=0, highlightthickness=0, wrap="word",
                    padx=12, pady=10, insertbackground=CLAY, selectbackground=SELECTED, cursor="arrow",
                    spacing1=2, spacing3=2)
        t.pack(fill="both", expand=True, padx=1, pady=1)
        for tag, color in (("dim", STONE), ("head", CREAM), ("time", DIM), ("src", SHIMMER)):
            t.tag_configure(tag, foreground=color)
        t.tag_configure("head", font=(self.f.mono, 9, "bold"))
        t.bind("<MouseWheel>", lambda e: self._smooth_text(t, e.delta) or "break")
        return t

    def _smooth_text(self, t: tk.Text, delta: int) -> None:
        start = t.yview()[0]
        end = min(max(0.0, start - delta / 120 * 0.06), 1.0)
        self.anim.run(("tscroll", id(t)), 220, lambda p: t.yview_moveto(start + (end - start) * p))

    def _insert_line(self, t: tk.Text, line: str, stamp: str = "", source: str = "") -> None:
        if stamp:
            t.insert("end", stamp + " ", "time")
        if source == "cli":
            t.insert("end", "cli ", "src")
        raw = line.strip()
        icon, color, text = split_glyph(line)
        if icon:
            t.image_create("end", image=self.icons.get(icon, 11, color), padx=2)
            t.insert("end", " " + text + "\n")
        elif raw.startswith("──") or raw.startswith("✻"):
            t.insert("end", text + "\n", "head")
        elif raw.startswith(("⎿", "?")) or not raw:
            t.insert("end", ("    " + text if text else "") + "\n", "dim")
        else:
            t.insert("end", text + "\n")

    # ================================================================= live refresh
    def on_snap(self) -> None:
        st = icon_state(self.ctl.snap)
        self.header.update(st)
        if self.modal_open:
            return
        self.home_view.refresh()
        if self.page in ("devices", "settings", "profiles") and self.page_frame is not None:
            sig = self._page_signature(self.page)
            if sig != self.page_sig:
                y = self.page_scroll.canvas.yview()[0] if self.page_scroll else 0
                old = self.page_frame
                frame = self._build_page(self.page)
                frame.place(x=0, y=0, relwidth=1, relheight=1)
                old.destroy()
                if self.page_scroll:
                    self.root.after(30, lambda: self.page_scroll and self.page_scroll.canvas.yview_moveto(y))

    def animate(self) -> None:
        if not self.visible:
            return
        self.spin_i += 1
        self.header.tick(self.spin_i)
        self.home_view.tick(self.spin_i)
        if self.page == "activity" and self.spin_i % 5 == 0:
            self._fill_activity()

    # ================================================================= Devices
    def _page_devices(self, p) -> None:
        from . import admission

        snap = self.ctl.snap
        v = self.page_view
        self.page_actions = {}
        on = snap.get("settings", {}).get("behavior", {}).get("approve_devices", True)
        cl = snap.get("clients_full") or []
        groups = {"pending": [], "approved": [], "blocked": []}
        for c in cl:
            groups.setdefault(getattr(c, "access", "approved"), []).append(c)
        tk.Label(p, text=("Device approval is on: a new device stays on the Wi-Fi without any network until you "
                          "allow it here, in the tray, or with 'allow' in the CLI." if on else
                          "Device approval is off: anyone with the password gets the VPN."),
                 font=self.f.small, fg=LIGHT if on else SHIMMER, bg=BG, anchor="w", justify="left",
                 wraplength=WIDTH - 40).pack(fill="x", pady=(0, 4))

        def card(c, buttons):
            body = self._card(p)
            top = tk.Frame(body, bg=SURFACE)
            top.pack(fill="x")
            tk.Label(top, image=self.icons.get(device_icon(c.device), 16, CLAY if c.access == "approved" else SHIMMER),
                     bg=SURFACE).pack(side="left", padx=(0, 8))
            tk.Label(top, text=c.display_name if c.hostnames else c.device, font=self.f.bold, fg=CREAM,
                     bg=SURFACE).pack(side="left")
            tk.Label(top, text=c.device[:20], font=self.f.small, fg=STONE, bg=SURFACE).pack(side="right")
            self._kv(body, "ip", c.ip or "no IP yet", width=7)
            self._kv(body, "mac", c.mac + ("  (private)" if c.randomized else ""), width=7)
            if c.vendor:
                self._kv(body, "vendor", c.vendor, width=7)
            row = tk.Frame(body, bg=SURFACE)
            row.pack(fill="x", pady=(6, 0))
            for label, icon, verdict, primary in buttons:
                v.button(row, label, icon, lambda m=c.mac, vd=verdict, n=c.display_name if c.hostnames else "":
                         self.ctl.device_verdict(m, vd, n), primary).pack(side="left", padx=(0, 6))

        if groups["pending"]:
            self._section(p, f"Waiting · {len(groups['pending'])}")
            for c in groups["pending"]:
                card(c, [("Allow", "check", "approve", True), ("Block", "ban", "block", False)])
        self._section(p, f"Connected · {len(groups['approved'])}")
        if not groups["approved"]:
            tk.Label(p, text="No devices." if icon_state(snap) == "live" else "The hotspot is off.",
                     font=self.f.text, fg=STONE, bg=BG, anchor="w").pack(fill="x", pady=4)
        for c in groups["approved"]:
            card(c, [("Block", "ban", "block", False)] if on else [])
        if groups["blocked"]:
            self._section(p, f"Blocked · {len(groups['blocked'])}")
            for c in groups["blocked"]:
                card(c, [("Allow", "check", "approve", True), ("Forget", "trash", "forget", False)])
        store = admission.load()
        here = {c.mac for c in cl}
        known = [(m, i) for m, i in store["approved"].items() if m not in here]
        if known and on:
            self._section(p, f"Remembered · {len(known)}")
            for mac, info in known[:30]:
                r = tk.Frame(p, bg=BG)
                r.pack(fill="x", pady=1)
                tk.Label(r, image=self.icons.get("check", 12, CLAY), bg=BG).pack(side="left", padx=(2, 8))
                tk.Label(r, text=info.get("name") or mac, font=self.f.small, fg=LIGHT, bg=BG).pack(side="left")
                v.button(r, "Forget", "trash", lambda m=mac: self.ctl.device_verdict(m, "forget")).pack(side="right")
        if on:
            tk.Label(p, text="How it works: WireSpot points a waiting device's address at nowhere on this PC, so "
                             "nothing reaches it. It can still see the Wi-Fi; a device on a new random MAC shows up "
                             "as a new request.", font=self.f.small, fg=DIM, bg=BG, anchor="w", justify="left",
                     wraplength=WIDTH - 40).pack(fill="x", pady=(12, 0))

    # ================================================================= Profiles
    def _page_profiles(self, p) -> None:
        snap = self.ctl.snap
        v = self.page_view
        self.page_actions = {"import": self._import_dialog, "folder": self.ctl.open_vpn_folder}
        v.row(p, "import", "import", "Import a .conf file…", primary=True).frame.pack_configure(padx=0)
        v.row(p, "folder", "folder", "Open the VPN folder", str(paths.VPN_DIR)[-24:]).frame.pack_configure(padx=0)
        default = snap.get("settings", {}).get("vpn", {}).get("default_profile", "")
        profs = snap.get("profile_objs") or []
        self._section(p, f"{len(profs)} profile{'s' if len(profs) != 1 else ''}")
        for prof in profs:
            body = self._card(p)
            top = tk.Frame(body, bg=SURFACE)
            top.pack(fill="x")
            is_def = prof.path.name == default
            tk.Label(top, image=self.icons.get("dot" if is_def else "ring", 12, CLAY if is_def else DIM),
                     bg=SURFACE).pack(side="left", padx=(0, 6))
            tk.Label(top, text=prof.server_name or prof.path.stem, font=self.f.bold, fg=CREAM, bg=SURFACE).pack(side="left")
            if is_def:
                tk.Label(top, text="default", font=self.f.small, fg=CLAY, bg=SURFACE).pack(side="right")
            where = (f"Secure Core via {prof.server.entry_country_code} to {prof.country_code}"
                     if prof.server.entry_country_code else prof.country or "")
            tk.Label(body, text=where + ("  · free" if prof.server.free else ""), font=self.f.small, fg=STONE,
                     bg=SURFACE, anchor="w").pack(fill="x", pady=(0, 4))
            self._kv(body, "endpoint", prof.endpoint or "-")
            self._kv(body, "routes", "full tunnel" if prof.full_tunnel else ", ".join(prof.allowed_ips))
            if prof.features:
                self._kv(body, "options", " · ".join(prof.features))
            acts = tk.Frame(body, bg=SURFACE)
            acts.pack(fill="x", pady=(6, 0))
            name = prof.path.name
            v.button(acts, "Go live", "play", lambda n=name: (self.ctl.do("golive", n), self.back()), True).pack(
                side="left", padx=(0, 6))
            if not is_def:
                v.button(acts, "Make default", "check", lambda n=name: self.ctl.set_default_profile(n)).pack(side="left")
        for path, why in snap.get("bad_profiles") or []:
            body = self._card(p)
            r = tk.Frame(body, bg=SURFACE)
            r.pack(fill="x")
            tk.Label(r, image=self.icons.get("x", 13, CRAIL), bg=SURFACE).pack(side="left", padx=(0, 6))
            tk.Label(r, text=path.name, font=self.f.bold, fg=CRAIL, bg=SURFACE, anchor="w").pack(side="left")
            tk.Label(body, text=why, font=self.f.small, fg=STONE, bg=SURFACE, anchor="w", justify="left",
                     wraplength=WIDTH - 70).pack(fill="x")

    def _import_dialog(self) -> None:
        from .inbox import downloads_dir

        f = filedialog.askopenfilename(parent=self.top, title="Import a WireGuard .conf",
                                       initialdir=str(downloads_dir()),
                                       filetypes=[("WireGuard config", "*.conf"), ("All files", "*.*")])
        if f:
            self.review(Candidate(Path(f), "import", ""))

    # ================================================================= Hotspot
    def _page_hotspot(self, p) -> None:
        s, _ = settings_mod.load()
        h = s["hotspot"]
        v = self.page_view
        self.page_actions = {"save": self._save_hotspot, "save_restart": lambda: self._save_hotspot(restart=True)}
        self.v_ssid = tk.StringVar(value=h["ssid"])
        self.v_pass = tk.StringVar(value=h["password"])
        self.v_band, self.v_sec = h["band"], h["security"]
        self._section(p, "Network")
        tk.Label(p, text="name (SSID)", font=self.f.small, fg=STONE, bg=BG, anchor="w").pack(fill="x")
        self._entry(p, self.v_ssid).pack(fill="x", ipady=5, pady=(2, 8))
        prow = tk.Frame(p, bg=BG)
        prow.pack(fill="x")
        tk.Label(prow, text="password", font=self.f.small, fg=STONE, bg=BG).pack(side="left")
        eye = tk.Label(prow, image=self.icons.get("eye", 15, CLAY), bg=BG, cursor="hand2")
        eye.pack(side="right")
        pw = self._entry(p, self.v_pass, show="•")
        pw.pack(fill="x", ipady=5, pady=(2, 8))

        def toggle_eye(_):
            hidden = bool(pw.cget("show"))
            pw.configure(show="" if hidden else "•")
            eye.configure(image=self.icons.get("eye-off" if hidden else "eye", 15, CLAY))
        eye.bind("<ButtonRelease-1>", toggle_eye)
        tk.Label(p, text="band", font=self.f.small, fg=STONE, bg=BG, anchor="w").pack(fill="x")
        self.band_holder = tk.Frame(p, bg=BG)
        self.band_holder.pack(fill="x", pady=(2, 8))
        tk.Label(p, text="security", font=self.f.small, fg=STONE, bg=BG, anchor="w").pack(fill="x")
        self.sec_holder = tk.Frame(p, bg=BG)
        self.sec_holder.pack(fill="x", pady=(2, 6))
        self._render_chips()
        tk.Label(p, text="auto lets the driver pick a band it can host next to your uplink. WPA3 needs Windows 11 24H2.",
                 font=self.f.small, fg=DIM, bg=BG, anchor="w", justify="left", wraplength=WIDTH - 40).pack(fill="x")
        self.form_msg = tk.Label(p, text="", font=self.f.small, fg=CRAIL, bg=BG, anchor="w", justify="left",
                                 wraplength=WIDTH - 40)
        self.form_msg.pack(fill="x", pady=(6, 2))
        v.row(p, "save", "check", "Save", primary=True).frame.pack_configure(padx=0)
        if icon_state(self.ctl.snap) == "live":
            v.row(p, "save_restart", "refresh", "Save and restart the hotspot").frame.pack_configure(padx=0)

        self._section(p, "Protection")
        cur = s["vpn"]["protection"]
        for key, title, desc in (
                ("balanced", "Balanced · for the hotspot",
                 "Full VPN routing via two /1 routes. Phones get addresses and DNS; the DNS lock keeps lookups in "
                 "the tunnel and the guard stops the hotspot if the tunnel drops."),
                ("strict", "Strict · kill-switch",
                 "Keeps 0.0.0.0/0. Maximum leak protection for this PC, but it blocks the hotspot's DHCP and DNS - "
                 "phones cannot get an address. For VPN-only use.")):
            sel = key == cur
            outer = tk.Frame(p, bg=CLAY if sel else BORDER, cursor="hand2")
            outer.pack(fill="x", pady=3)
            box = tk.Frame(outer, bg=SELECTED if sel else SURFACE)
            box.pack(fill="x", padx=1, pady=1)
            head = tk.Frame(box, bg=box["bg"])
            head.pack(fill="x", padx=10, pady=(7, 1))
            ic = tk.Label(head, image=self.icons.get("dot" if sel else "ring", 12, CLAY if sel else STONE), bg=box["bg"])
            ic.pack(side="left", padx=(0, 6))
            t = tk.Label(head, text=title, font=self.f.bold, fg=CREAM if sel else LIGHT, bg=box["bg"], anchor="w")
            t.pack(side="left")
            d = tk.Label(box, text=desc, font=self.f.small, fg=STONE, bg=box["bg"], anchor="w", justify="left",
                         wraplength=WIDTH - 60)
            d.pack(fill="x", padx=10, pady=(0, 7))
            for w in (outer, box, head, ic, t, d):
                w.bind("<ButtonRelease-1>", lambda e, k=key: self._set_protection(k))

    def _render_chips(self) -> None:
        for holder in (self.band_holder, self.sec_holder):
            for w in holder.winfo_children():
                w.destroy()
        self._chips(self.band_holder, [("auto", "auto"), ("2.4", "2.4"), ("5", "5 GHz"), ("6", "6 GHz")], self.v_band,
                    lambda k: (setattr(self, "v_band", k), self._render_chips())).pack(anchor="w")
        self._chips(self.sec_holder, [("wpa2", "WPA2"), ("transition", "WPA3/2"), ("wpa3", "WPA3")], self.v_sec,
                    lambda k: (setattr(self, "v_sec", k), self._render_chips())).pack(anchor="w")

    def _save_hotspot(self, restart: bool = False) -> None:
        s, _ = settings_mod.load()
        s["hotspot"].update(ssid=self.v_ssid.get().strip(), password=self.v_pass.get(), band=self.v_band,
                            security=self.v_sec)
        problems = settings_mod.validate_hotspot(s)
        if problems:
            self.form_msg.configure(text="\n".join(problems), fg=CRAIL)
            return
        settings_mod.save(s)
        log.register_secret(s["hotspot"]["password"])
        self.form_msg.configure(text="Saved.", fg=CLAY)
        self.ctl.refresh_now()
        if restart:
            self.ctl.do("hotspot_on")
            self.back()

    def _set_protection(self, key: str) -> None:
        s, _ = settings_mod.load()
        s["vpn"]["protection"] = key
        settings_mod.save(s)
        self.toaster.toast(f"Protection: {key}", "Applies the next time the VPN connects (Reconnect to apply now).",
                           icon="shield")
        self.open_page("hotspot", animate=False)

    # ================================================================= Checks
    def _page_checks(self, p) -> None:
        v = self.page_view
        self.page_actions = {}
        row1 = tk.Frame(p, bg=BG)
        row1.pack(fill="x")
        row2 = tk.Frame(p, bg=BG)
        row2.pack(fill="x", pady=(4, 4))
        for i, (key, label) in enumerate(DOCTOR_SECTIONS):
            v.button(row1 if i < 4 else row2, label, "pulse", lambda k=key: self.run_doctor(k),
                     primary=key == "quick").pack(side="left", padx=(0, 5), pady=2)
        tools = tk.Frame(p, bg=BG)
        tools.pack(fill="x", pady=(2, 8))
        v.button(tools, "Exit IP", "globe", lambda: self.ctl.do("check_ip")).pack(side="left", padx=(0, 5))
        v.button(tools, "Save report", "file", self._save_report).pack(side="left")
        self.report_view = self._text_view(p)
        self._fill_report()

    def show_report(self, section: str, lines: list[str]) -> None:
        self.report = (section, lines)
        self._fill_report()

    def _fill_report(self) -> None:
        t = self.report_view
        if t is None:
            return
        try:
            t.configure(state="normal")
            t.delete("1.0", "end")
            section, lines = self.report
            if not lines:
                t.insert("end", "Pick a check. Quick is a one-screen health checklist; Full runs everything.\n", "dim")
            for line in lines:
                self._insert_line(t, line)
            t.configure(state="disabled")
        except tk.TclError:
            self.report_view = None

    def _save_report(self) -> None:
        section, lines = self.report
        if not lines:
            return
        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = paths.LOG_DIR / f"doctor-{section}-{datetime.now():%Y%m%d-%H%M%S}.txt"
        out.write_text(log.redact("\n".join(lines)) + "\n", encoding="utf-8")
        self.toaster.toast("Report saved", str(out), icon="file")

    # ================================================================= Activity
    def _page_activity(self, p) -> None:
        v = self.page_view
        self.page_actions = {}
        tools = tk.Frame(p, bg=BG)
        tools.pack(fill="x", pady=(0, 8))
        v.button(tools, "Open logs folder", "folder", self.ctl.open_logs).pack(side="left")
        self.activity_view = self._text_view(p)
        self.activity_stamp = None
        self._fill_activity()

    def _fill_activity(self) -> None:
        t = self.activity_view
        if t is None:
            return
        try:
            st = sync.JOURNAL_PATH.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if stamp == self.activity_stamp and stamp is not None:
            return
        self.activity_stamp = stamp
        try:
            at_end = t.yview()[1] >= 0.999
            t.configure(state="normal")
            t.delete("1.0", "end")
            lines = sync.read_journal(500)
            for ts, src, line in lines:
                self._insert_line(t, line, stamp=time.strftime("%H:%M:%S", time.localtime(ts)), source=src)
            if not lines:
                t.insert("end", "Nothing yet.\n", "dim")
            t.configure(state="disabled")
            if at_end or self.activity_stamp is None:
                t.see("end")
        except tk.TclError:
            self.activity_view = None

    # ================================================================= Settings
    def _page_settings(self, p) -> None:
        snap = self.ctl.snap
        b = (snap.get("settings") or settings_mod.load()[0])["behavior"]
        v = self.page_view

        def toggle(key, default):
            return lambda: self.ctl.set_behavior(key, not b.get(key, default))

        self.page_actions = {
            "autostart": self.ctl.toggle_autostart, "autoconnect": toggle("autoconnect", False),
            "guard": toggle("guard", True), "dns_lock": toggle("dns_lock", True),
            "approve": self.ctl.toggle_approval, "watch": toggle("watch_downloads", True),
            "debug": toggle("debug", False), "data": self.ctl.open_data, "vpn": self.ctl.open_vpn_folder,
            "settings_file": self.ctl.open_settings_file, "logs": self.ctl.open_logs, "cli": self.ctl.open_cli,
            "uninstall": self.ask_uninstall}

        def switch(id_, icon, title, desc, on):
            v.row(p, id_, icon, title, switch=bool(on), desc=desc).frame.pack_configure(padx=0)

        self._section(p, "Startup")
        switch("autostart", "power", "Start with Windows", "In the tray at logon, elevated, no UAC prompt.",
               snap.get("autostart"))
        switch("autoconnect", "play", "Go live at startup", "Connect the default profile and start the hotspot.",
               b.get("autoconnect"))
        self._section(p, "Safety")
        switch("approve", "shield-check", "Approve new devices", "New devices get no network until you allow them.",
               b.get("approve_devices", True))
        switch("guard", "shield", "Fail-closed guard", "Stop the hotspot if the tunnel drops.", b.get("guard", True))
        switch("dns_lock", "lock", "DNS lock", "Every lookup goes through the tunnel resolver.", b.get("dns_lock", True))
        self._section(p, "Convenience")
        switch("watch", "import", "Watch Downloads", "Offer new Proton .conf files for import.",
               b.get("watch_downloads", True))
        switch("debug", "file", "Debug logging", "Write logs\\wirespot-DATE.log (secrets redacted).", b.get("debug"))
        self._section(p, "Your files")
        v.row(p, "data", "folder", "Open the data folder", "%APPDATA%" if paths.is_installed() else "").frame.pack_configure(padx=0)
        v.row(p, "vpn", "server", "Open the VPN folder", "profiles").frame.pack_configure(padx=0)
        v.row(p, "settings_file", "file", "Edit settings.json", "notepad").frame.pack_configure(padx=0)
        v.row(p, "logs", "list", "Open the logs folder").frame.pack_configure(padx=0)
        v.row(p, "cli", "terminal", "Open WireSpot CLI").frame.pack_configure(padx=0)
        self._section(p, "About")
        self._kv(p, "version", f"{APP_NAME} {VERSION}")
        self._kv(p, "program", str(paths.APP_DIR))
        self._kv(p, "data", str(paths.BASE))
        if paths.is_installed():
            self._section(p, "Remove")
            v.row(p, "uninstall", "trash", "Uninstall WireSpot…", danger=True).frame.pack_configure(padx=0)
        else:
            tk.Label(p, text="This is a portable copy - delete its folder to remove it.", font=self.f.small, fg=DIM,
                     bg=BG, anchor="w").pack(fill="x", pady=(8, 0))

    # ================================================================= sheets
    def _sheet(self) -> tuple[tk.Frame, tk.Frame]:
        """A panel that rises from the bottom over a darkened page."""
        body = self.win.body
        shade = tk.Label(body, bg="#161514", bd=0)
        self._backdrop = self.win.snapshot_dimmed(body)          # the page, dimmed, behind the sheet
        if self._backdrop is not None:
            shade.configure(image=self._backdrop, anchor="nw")
        shade.place(x=0, y=0, relwidth=1, relheight=1)
        sheet = tk.Frame(body, bg=BORDER)
        inner = tk.Frame(sheet, bg=BG)
        inner.pack(fill="both", expand=True, padx=1, pady=(1, 0))
        tk.Frame(inner, bg=CLAY, height=2).pack(fill="x")
        self.modal_open = sheet
        self._sheet_parts = (shade, sheet)
        self.sheet_view = PageView(inner, self.ctl, self.f, self.icons, self.anim, lambda i: None)
        return sheet, inner

    def _rise(self, sheet: tk.Frame) -> None:
        self.top.update_idletasks()
        H = self.win.body.winfo_height()
        h = min(sheet.winfo_reqheight(), H - 40)
        sheet.place(x=0, y=H, relwidth=1, height=h)
        self.anim.run("sheet", 320, lambda t: sheet.place_configure(y=int(H - h * t)))
        sheet.focus_set()

    def _sink(self, done=None) -> None:
        shade, sheet = self._sheet_parts
        H = self.win.body.winfo_height()
        y0 = sheet.winfo_y()

        def finish():
            sheet.destroy()
            shade.destroy()
            self.modal_open = None
            if done:
                done()
            self.on_snap()

        self.anim.run("sheet", 200, lambda t: sheet.place_configure(y=int(y0 + (H - y0) * t)), done=finish,
                      curve=lambda t: t * t)

    def _sheet_head(self, body, icon, color, title) -> None:
        head = tk.Frame(body, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(head, image=self.icons.get(icon, 20, color), bg=BG).pack(side="left", anchor="n", padx=(0, 8))
        tk.Label(head, text=title, font=self.f.title, fg=CREAM, bg=BG, wraplength=WIDTH - 70, justify="left",
                 anchor="w").pack(side="left", fill="x")

    def _choice(self, body, icon, text, hint, cb, **kw) -> None:
        r = self.sheet_view.row(body, "c", icon, text, hint, **kw)
        r.view = _Direct(self.sheet_view, cb)

    def ask(self, question: str, choices, default: int, holder: dict) -> None:
        """Answer a relay decision in a Claude-style choice sheet."""
        if not self.visible or self.modal_open:
            holder["answer"] = choices[default].key
            holder["event"].set()
            return
        sheet, body = self._sheet()
        answered = {"v": False}

        def answer(key):
            if answered["v"]:
                return
            answered["v"] = True
            holder["answer"] = key
            holder["event"].set()
            self._sink()

        self._sheet_head(body, "info", CLAY, question)
        for i, c in enumerate(choices):
            rec = i == default
            self._choice(body, "chev-right", c.label, "recommended" if rec else "", lambda k=c.key: answer(k), primary=rec)
            if c.hint:
                tk.Label(body, text=c.hint, font=self.f.small, fg=STONE, bg=BG, anchor="w", justify="left",
                         wraplength=WIDTH - 70).pack(fill="x", padx=(44, 12))
        cancel_key = next((c.key for c in choices if c.key in ("stop", "cancel", "vpn-only", "decline")),
                          choices[default].key)
        tk.Label(body, text="click · 1-9 · enter = recommended · esc", font=self.f.small, fg=DIM,
                 bg=BG).pack(pady=(10, 14))
        for i, c in enumerate(choices):
            sheet.bind(str(i + 1), lambda e, k=c.key: answer(k))
        sheet.bind("<Return>", lambda e: answer(choices[default].key))
        sheet.bind("<Escape>", lambda e: answer(cancel_key))
        self._rise(sheet)

    def ask_uninstall(self) -> None:
        if self.modal_open:
            return
        if not self.visible:
            self.show("settings")
        sheet, body = self._sheet()
        self._sheet_head(body, "trash", CRAIL, "Uninstall WireSpot?")
        tk.Label(body, text="WireSpot first stops its own VPN tunnel, hotspot and DNS lock, then removes the app, "
                            "its shortcuts and Start with Windows.", font=self.f.small, fg=STONE, bg=BG, anchor="w",
                 justify="left", wraplength=WIDTH - 50).pack(fill="x", padx=16, pady=(0, 8))

        def go(keep):
            self._sink(lambda: self.ctl.do("uninstall", keep))

        self._choice(body, "folder", "Keep settings and VPN profiles", "recommended", lambda: go(True), primary=True)
        tk.Label(body, text=f"kept in {paths.appdata_dir()}", font=self.f.small, fg=DIM, bg=BG, anchor="w").pack(
            fill="x", padx=(44, 12))
        self._choice(body, "trash", "Delete everything", "", lambda: go(False), danger=True)
        tk.Label(body, text="removes the VPN profiles (private keys) too", font=self.f.small, fg=DIM, bg=BG,
                 anchor="w").pack(fill="x", padx=(44, 12))
        self._choice(body, "x", "Cancel", "", lambda: self._sink())
        tk.Frame(body, bg=BG, height=12).pack()
        sheet.bind("<Escape>", lambda e: self._sink())
        self._rise(sheet)

    def queue_review(self, cand: Candidate) -> None:
        self.pending_reviews.append(cand)
        if self.visible and not self.modal_open:
            self._next_review()
        elif not self.visible:
            self.toaster.toast("New WireGuard config found", f"{cand.path.name} in {cand.origin} - click to review it.",
                               icon="import")

    def _next_review(self) -> None:
        if self.pending_reviews and not self.modal_open:
            self.review(self.pending_reviews.pop(0))

    def review(self, cand: Candidate) -> None:
        info = self.ctl.inbox.inspect(cand)
        if info.duplicate:
            self.toaster.toast("Already imported", f"{cand.path.name} is already in WireSpot as {info.duplicate.name}.",
                               icon="info")
            return self._next_review()
        sheet, body = self._sheet()
        if info.error:
            self._sheet_head(body, "alert", CRAIL, "Not importable")
            tk.Label(body, text=f"{cand.path.name}\n{info.error}", font=self.f.text, fg=LIGHT, bg=BG, justify="left",
                     anchor="w", wraplength=WIDTH - 40).pack(fill="x", padx=16)
            self._choice(body, "x", "Close", "", lambda: self._close_review(cand, None))
            tk.Frame(body, bg=BG, height=12).pack()
            sheet.bind("<Escape>", lambda e: self._close_review(cand, None))
            return self._rise(sheet)
        prof = info.profile
        self._sheet_head(body, "import", CLAY, "New WireGuard profile")
        iv, peer = prof.config.interface.values, prof.config.peers[0].values
        where = (f"Secure Core via {prof.server.entry_country_code} to {prof.country_code}"
                 if prof.server.entry_country_code else prof.country or "unknown country")
        card = tk.Frame(body, bg=SURFACE)
        card.pack(fill="x", padx=16, pady=(4, 8))
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="x", padx=12, pady=8)
        for k, v in [("file", f"{cand.path.name} ({cand.origin})"),
                     ("server", f"{prof.server_name or '(unnamed)'} · {where}" + (" · free" if prof.server.free else "")),
                     ("endpoint", peer.get("endpoint", "-")), ("address", iv.get("address", "-")),
                     ("dns", iv.get("dns", "-")),
                     ("routes", "full tunnel" if prof.full_tunnel else peer.get("allowedips", "")),
                     ("server key", peer.get("publickey", "-")[:22] + "…"),
                     ("private key", "valid, never displayed"), ("your key", prof.public_key[:22] + "…"),
                     ("checks", "no scripts · keys valid")]:
            self._kv(inner, k, v, width=12)
        self._choice(body, "check", "Accept · move into WireSpot", "recommended",
                     lambda: self._close_review(cand, "move"), primary=True)
        self._choice(body, "copy", "Accept · keep a copy", "", lambda: self._close_review(cand, "copy"))
        self._choice(body, "x", "Decline", "", lambda: self._close_review(cand, "decline"), danger=True)
        tk.Label(body, text="moving keeps the private key in one place · esc to decide later", font=self.f.small,
                 fg=DIM, bg=BG).pack(pady=(8, 12))
        sheet.bind("<Escape>", lambda e: self._close_review(cand, None))
        self._rise(sheet)

    def _close_review(self, cand: Candidate, choice) -> None:
        def after():
            if choice in ("move", "copy"):
                ok, msg = self.ctl.import_conf(cand.path, move=choice == "move")
                self.toaster.toast("Profile imported" if ok else "Not imported", msg, error=not ok,
                                   icon="check" if ok else None)
            elif choice == "decline":
                self.ctl.inbox.decline(cand)
                self.toaster.toast("Declined", f"{cand.path.name} was not imported.", icon="x")
            self.root.after(250, self._next_review)
        self._sink(after)


class _Direct:
    """Routes one row's click straight to a callback (sheet choices)."""

    def __init__(self, view, cb):
        self._view, self._cb = view, cb

    def __getattr__(self, name):
        return getattr(self._view, name)

    def click(self, _id):
        self._cb()
