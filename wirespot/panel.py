"""The WireSpot panel - one design shared by the tray menu and the window.

``home_model(snap, mode)`` is a pure function that turns a status snapshot
into a list of entries (header rows, status lines, devices, actions). The
``PanelView`` renders that list with row widgets and, on every new snapshot,
*updates the existing widgets in place*; it only rebuilds when the set of
rows changes (a state change), and then builds the new rows off-screen and
swaps them in one step - so nothing ever blinks.

The tray menu and the window render the same model; the only differences
are "Open WireSpot" (tray only) and where the page tiles lead (the tray
opens the window on that page, the window slides the page in).
"""
from __future__ import annotations

import re
import time
import tkinter as tk

from . import APP_NAME, VERSION, icons as icons_mod, wireguard
from .controller import fmt_duration, icon_state
from .motion import Animator, hover, lerp_color
from .state import State
from .status import normalize_status
from .theme import (BG, BORDER, CLAY, CRAIL, CREAM, DIM, LIGHT, ROW_HOVER, RULE, SELECTED, SHIMMER, STATES, STONE,
                    SURFACE, Fonts)

WIDTH = 400                       # tray menu and window share one width
SPIN_FRAMES = 18
PAGES = [("devices", "phone", "Devices"), ("profiles", "server", "Profiles"), ("hotspot", "wifi", "Hotspot"),
         ("checks", "pulse", "Checks"), ("activity", "list", "Activity"), ("settings", "sliders", "Settings")]


# ===================================================================== text helpers
GLYPHS = {"●": ("dot", CLAY), "▲": ("alert", SHIMMER), "✖": ("x", CRAIL), "×": ("x", CRAIL), "✻": ("bolt", CLAY),
          "?": ("info", CLAY), "•": ("dot", CLAY), "*": ("dot", CLAY), "!": ("alert", SHIMMER), "+": ("dot", CLAY)}
_STRIP = re.compile(r"[─-╿⎿❯•·]+")       # box drawing, ⎿, ❯


def split_glyph(line: str) -> tuple[str | None, str, str]:
    """A CLI/ui output line -> (icon name, colour, plain text). Removes Unicode glyphs."""
    s = line.strip()
    icon, color = None, LIGHT
    if s[:1] in GLYPHS and (len(s) == 1 or s[1] == " "):
        icon, color = GLYPHS[s[0]]
        s = s[1:].strip()
    elif s.startswith("⎿"):
        s = "  " + s[1:].strip()
    s = s.replace("→", " to ").replace("↓", "in").replace("↑", "out").replace("✓", "")
    s = _STRIP.sub(lambda m: " · " if m.group(0) == "·" else " ", s).strip()
    return icon, color, re.sub(r"\s{2,}", "  ", s)


def device_icon(device: str) -> str:
    d = (device or "").lower()
    if any(k in d for k in ("pc", "laptop", "mac", "windows", "raspberry", "deck")):
        return "laptop"
    return "phone"


def status_rows(snap: dict) -> list[tuple[str, str]]:
    """Key/value lines describing the current state (plain text, no glyphs)."""
    snap = normalize_status(snap)
    st = icon_state({**snap, "busy": ""})       # while busy, describe the state underneath
    if st == "unknown":
        return [("status", "Not yet verified"), ("hotspot", snap["ssid"]), ("profile", snap["profile_label"])]
    band = {"auto": "auto", "2.4": "2.4 GHz", "5": "5 GHz", "6": "6 GHz"}.get(snap.get("band"), snap.get("band") or "")
    traffic = f"{wireguard.fmt_bytes(snap.get('rx', 0))} in · {wireguard.fmt_bytes(snap.get('tx', 0))} out"
    label = snap.get("profile_label", "")
    if st == "live":
        n = len(snap.get("clients") or [])
        rows = [("hotspot", f"{snap['ssid']} · {band} · {snap.get('security', '').upper()}"), ("vpn", label),
                ("exit ip", snap.get("exit_ip") or "checking…"),
                ("uptime", fmt_duration(time.time() - snap["ready_since"]) if snap.get("ready_since") else "-"),
                ("traffic", traffic), ("mode", snap.get("protection", "") + (" · DNS locked" if snap.get("dns_lock") else "")),
                ("devices", f"{n} connected" if n else "none yet")]
    elif st == "vpn":
        rows = [("vpn", label), ("exit ip", snap.get("exit_ip") or "checking…"), ("traffic", traffic),
                ("hotspot", f"{snap['ssid']} · off")]
    elif st == "paused":
        rows = [("resumes", f"in {fmt_duration(snap['paused_until'] - time.time())}"), ("profile", label),
                ("hotspot", f"{snap['ssid']} · off")]
    elif st == "error":
        rows = [("problem", (snap.get("last_error") or "the last session ended unexpectedly")[:42]), ("profile", label)]
    else:
        rows = [("profile", label), ("hotspot", f"{snap['ssid']} · {band} · off")]
    if snap.get("uplink") and st in ("live", "vpn", "idle"):
        rows.append(("uplink", snap["uplink"]))
    return rows


# ===================================================================== the model (pure)
def home_model(snap: dict, mode: str = "tray", busy: str | None = None, detail: str = "") -> list[dict]:
    snap = normalize_status(snap)
    st = icon_state({**snap, "busy": ""})       # actions follow the state underneath; busy only disables them
    b = snap.get("settings", {}).get("behavior", {})
    m: list[dict] = []
    add = m.append

    def item(id_, icon, text, hint="", **kw):
        add({"t": "item", "id": id_, "icon": icon, "text": text, "hint": hint, **kw})

    if busy:
        add({"t": "busy", "id": "busy", "text": busy, "detail": detail})
        add({"t": "rule", "id": "r-busy"})
    pending = snap.get("pending") or []
    if pending:
        add({"t": "section", "id": "s-pending", "text": f"Waiting for approval · {len(pending)}"})
        for p in pending[:4]:
            add({"t": "pending", "id": "p:" + p["mac"], **p})
        add({"t": "rule", "id": "r-pending"})
    for k, v in status_rows(snap):
        add({"t": "kv", "id": "kv:" + k, "k": k, "v": v})
    for ip, name, device in (snap.get("clients") or [])[:6]:
        add({"t": "client", "id": f"c:{ip}:{name}", "ip": ip, "name": name, "device": device})
    add({"t": "rule", "id": "r1"})
    if mode == "tray":
        item("open", "window", "Open WireSpot")
        add({"t": "rule", "id": "r-open"})
    wait = bool(busy)
    prof = snap.get("profile")
    if st in ("live", "vpn", "error") and snap.get("tunnel"):
        item("reconnect", "refresh", "Reconnect now", "stop + start", disabled=wait)
        if st == "live":
            item("hotspot_off", "ring", "Stop hotspot, keep VPN", disabled=wait)
        elif st == "vpn":
            item("hotspot_on", "play", "Start the hotspot", "share this VPN", disabled=wait)
        item("pause15", "pause", "Pause 15 minutes", "auto-resume", disabled=wait)
        item("pause60", "pause", "Pause 1 hour", "auto-resume", disabled=wait)
        item("disconnect", "power", "Disconnect", "VPN + hotspot off", danger=True, disabled=wait)
    elif st == "paused":
        item("resume", "play", "Resume now", disabled=wait)
        item("cancel_pause", "x", "Cancel auto-resume", "stay off")
    else:
        first = (snap.get("profile_label", "").split(" · ")[0])[:22]
        item("golive", "play", "Go live", first, primary=True, disabled=wait)
        profs = snap.get("profiles") or []
        if len(profs) > 1:
            item("profiles", "server", "Choose a profile", f"{len(profs)} available")
    add({"t": "rule", "id": "r2"})
    item("check_ip", "globe", "Check exit IP", snap.get("exit_ip") or "")
    item("doctor", "pulse", "Quick doctor", "health check")
    item("copy_pw", "key", "Copy Wi-Fi password", snap.get("ssid", ""))
    item("cli", "terminal", "Open WireSpot CLI")
    add({"t": "rule", "id": "r3"})
    add({"t": "bar", "id": "pages", "items": PAGES,
         "badges": {"devices": len(pending) or len(snap.get("clients") or []) or 0}})
    add({"t": "rule", "id": "r4"})
    item("autostart", "power", "Start with Windows", switch=bool(snap.get("autostart")))
    item("autoconnect", "play", "Go live at startup", switch=bool(b.get("autoconnect")))
    item("approve", "shield-check", "Approve new devices", switch=bool(b.get("approve_devices", True)))
    add({"t": "rule", "id": "r5"})
    item("quit", "logout", "Quit WireSpot", "VPN/hotspot keep running" if st in ("live", "vpn") else "")
    add({"t": "note", "id": "note", "text": "esc closes · double-click the icon opens WireSpot" if mode == "tray"
         else "closing keeps WireSpot running in the tray"})
    return m


# ===================================================================== rows
class Row:
    def __init__(self, view: "PanelView", parent, e: dict):
        self.view, self.e = view, e

    def apply(self, e: dict) -> None:
        self.e = e

    def tick(self, i: int) -> None:
        pass


class RuleRow(Row):
    def __init__(self, view, parent, e):
        super().__init__(view, parent, e)
        tk.Frame(parent, bg=RULE, height=1).pack(fill="x", padx=10, pady=5)


class NoteRow(Row):
    def __init__(self, view, parent, e):
        super().__init__(view, parent, e)
        self.lb = tk.Label(parent, text=e["text"], font=view.f.small, fg=DIM, bg=BG)
        self.lb.pack(fill="x", padx=14, pady=(2, 10))

    def apply(self, e):
        super().apply(e)
        self.lb.configure(text=e["text"])


class SectionRow(Row):
    def __init__(self, view, parent, e):
        super().__init__(view, parent, e)
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=14, pady=(4, 2))
        tk.Label(row, image=view.icon("user", 14, SHIMMER), bg=BG).pack(side="left", padx=(0, 6))
        self.lb = tk.Label(row, text=e["text"], font=(view.f.mono, 9, "bold"), fg=SHIMMER, bg=BG)
        self.lb.pack(side="left")

    def apply(self, e):
        super().apply(e)
        self.lb.configure(text=e["text"])


class KvRow(Row):
    def __init__(self, view, parent, e):
        super().__init__(view, parent, e)
        r = tk.Frame(parent, bg=BG)
        r.pack(fill="x", padx=14)
        tk.Label(r, text=f"{e['k']:<9}", font=view.f.text, fg=STONE, bg=BG).pack(side="left", anchor="n")
        self.v = tk.Label(r, text=e["v"], font=view.f.text, fg=LIGHT, bg=BG, anchor="w", justify="left",
                          wraplength=WIDTH - 120)
        self.v.pack(side="left", fill="x")

    def apply(self, e):
        if e["v"] != self.e.get("v"):
            self.v.configure(text=e["v"])
        super().apply(e)


class ClientRow(Row):
    def __init__(self, view, parent, e):
        super().__init__(view, parent, e)
        r = tk.Frame(parent, bg=BG)
        r.pack(fill="x", padx=(22, 14))
        self.ic = tk.Label(r, image=view.icon(device_icon(e["device"]), 14, STONE), bg=BG)
        self.ic.pack(side="left", padx=(0, 6))
        self.a = tk.Label(r, font=view.f.small, fg=LIGHT, bg=BG)
        self.a.pack(side="left")
        self.b = tk.Label(r, font=view.f.small, fg=STONE, bg=BG)
        self.b.pack(side="right")
        self.apply(e)

    def apply(self, e):
        super().apply(e)
        self.a.configure(text=f"{e['ip'] or '-':<15} {e['name'][:14]}")
        self.b.configure(text=e["device"][:18])


class PendingRow(Row):
    """A device waiting for approval: who it is, plus Allow / Block."""

    def __init__(self, view, parent, e):
        super().__init__(view, parent, e)
        card = tk.Frame(parent, bg=SURFACE)
        card.pack(fill="x", padx=10, pady=2)
        tk.Label(card, image=view.icon(device_icon(e.get("device", "")), 18, SHIMMER), bg=SURFACE).pack(
            side="left", padx=(10, 8), pady=6)
        btns = tk.Frame(card, bg=SURFACE)
        btns.pack(side="right", padx=(4, 8))            # packed first so the text never squeezes the buttons
        txt = tk.Frame(card, bg=SURFACE)
        txt.pack(side="left", fill="x", expand=True, pady=4)
        self.a = tk.Label(txt, font=view.f.bold, fg=CREAM, bg=SURFACE, anchor="w")
        self.a.pack(fill="x")
        self.b = tk.Label(txt, font=view.f.small, fg=STONE, bg=SURFACE, anchor="w")
        self.b.pack(fill="x")
        view.button(btns, "Block", "ban", lambda: view.click("block:" + self.e["mac"]), primary=False).pack(side="right")
        view.button(btns, "Allow", "check", lambda: view.click("allow:" + self.e["mac"]), primary=True).pack(
            side="right", padx=(0, 6))
        self.apply(e)

    def apply(self, e):
        super().apply(e)
        name = e.get("name") if e.get("name") and e.get("name") != "(no name)" else e.get("device", "New device")
        self.a.configure(text=name[:18])
        self.b.configure(text=f"{e.get('ip') or 'no IP yet'}")


class ItemRow(Row):
    """The tray's row: icon · text · hint (or an animated switch), with a fading hover."""

    def __init__(self, view, parent, e):
        super().__init__(view, parent, e)
        self.hover = False
        self.base = SURFACE if e.get("primary") else BG
        self.frame = tk.Frame(parent, bg=self.base, cursor="hand2")
        self.frame.pack(fill="x", padx=6, pady=0)
        self.ic = tk.Label(self.frame, bg=self.base)
        self.ic.pack(side="left", padx=(8, 8), pady=4, anchor="n" if e.get("desc") else "center")
        col = tk.Frame(self.frame, bg=self.base)
        col.pack(side="left", fill="x", expand=True, pady=(4, 4) if e.get("desc") else 0)
        self.tx = tk.Label(col, font=view.f.bold if e.get("primary") else view.f.text, bg=self.base, anchor="w")
        self.tx.pack(fill="x")
        self.desc = None
        if e.get("desc"):
            self.desc = tk.Label(col, text=e["desc"], font=view.f.small, fg=STONE, bg=self.base, anchor="w",
                                 justify="left", wraplength=WIDTH - 130)
            self.desc.pack(fill="x")
        self.col = col
        self.sw = None
        if e.get("switch") is not None:
            self.sw_pos = 1.0 if e["switch"] else 0.0
            self.sw = tk.Label(self.frame, bg=self.base, image=view.switch(self.sw_pos))
            self.sw.pack(side="right", padx=(4, 10))
        self.hi = tk.Label(self.frame, font=view.f.small, fg=DIM, bg=self.base)
        self.hi.pack(side="right", padx=(4, 10) if self.sw is None else (4, 0))
        self.widgets = [w for w in (self.frame, self.ic, col, self.tx, self.desc, self.hi, self.sw) if w is not None]
        hover(self.frame, self.widgets, self._enter, self._leave)
        for w in self.widgets:
            w.bind("<ButtonRelease-1>", self._click)
        self.apply(e)

    def _colors(self):
        e = self.e
        if e.get("disabled"):
            return DIM, DIM
        if e.get("danger"):
            return CRAIL, CRAIL
        return CLAY, (CREAM if (self.hover or e.get("primary")) else LIGHT)

    def apply(self, e):
        old = self.e
        super().apply(e)
        icol, tcol = self._colors()
        if e is old or e.get("icon") != old.get("icon") or e.get("disabled") != old.get("disabled") or not self.ic.cget("image"):
            self.ic.configure(image=self.view.icon(e["icon"], 16, icol))
        self.tx.configure(text=e["text"], fg=tcol)
        self.hi.configure(text=e.get("hint", ""))
        self.frame.configure(cursor="arrow" if e.get("disabled") else "hand2")
        if self.sw is not None and bool(e.get("switch")) != (self.sw_pos > 0.5):
            self._slide(bool(e.get("switch")))

    def _slide(self, on: bool) -> None:
        a, b = self.sw_pos, (1.0 if on else 0.0)

        def step(t):
            self.sw_pos = a + (b - a) * t
            self.sw.configure(image=self.view.switch(self.sw_pos))
        self.view.anim.run(("switch", id(self)), 180, step)

    def _enter(self):
        if self.e.get("disabled"):
            return
        self.hover = True
        self.view.anim.fade(self.widgets, self.frame.cget("bg"), ROW_HOVER, 110, key=("row", id(self)))
        self.tx.configure(fg=self._colors()[1])
        self.hi.configure(fg=STONE)

    def _leave(self):
        self.hover = False
        self.view.anim.fade(self.widgets, self.frame.cget("bg"), self.base, 170, key=("row", id(self)))
        self.tx.configure(fg=self._colors()[1])
        self.hi.configure(fg=DIM)

    def _click(self, ev):
        if self.e.get("disabled"):
            return
        x, y = ev.x_root, ev.y_root
        f = self.frame
        if not (f.winfo_rootx() <= x < f.winfo_rootx() + f.winfo_width() and f.winfo_rooty() <= y < f.winfo_rooty() + f.winfo_height()):
            return                                        # released outside the row: cancelled
        self.view.anim.fade(self.widgets, SELECTED, ROW_HOVER if self.hover else self.base, 220, key=("row", id(self)))
        if self.sw is not None:
            self._slide(not (self.sw_pos > 0.5))
        self.view.root.after(10, lambda: self.view.click(self.e["id"]))


class BarRow(Row):
    """Page tiles: Devices · Profiles · Hotspot · Checks · Activity · Settings."""

    def __init__(self, view, parent, e):
        super().__init__(view, parent, e)
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=8, pady=2)
        self.badges = {}
        n = len(e["items"])
        for i, (key, icon, label) in enumerate(e["items"]):
            bar.columnconfigure(i, weight=1, uniform="tile")
            cell = tk.Frame(bar, bg=BG, cursor="hand2")
            cell.grid(row=0, column=i, sticky="nsew", padx=1)
            ic = tk.Label(cell, image=view.icon(icon, 18, CLAY), bg=BG)
            ic.pack(pady=(6, 2))
            lb = tk.Label(cell, text=label, font=(view.f.mono, 8), fg=STONE, bg=BG)
            lb.pack(pady=(0, 6))
            badge = tk.Label(cell, text="", font=(view.f.mono, 7, "bold"), fg=BG, bg=CLAY, padx=3)
            self.badges[key] = badge
            ws = (cell, ic, lb)
            hover(cell, ws,
                  lambda ws=ws: (view.anim.fade(ws, ws[0].cget("bg"), ROW_HOVER, 110, key=("tile", id(ws[0]))),
                                 ws[2].configure(fg=CREAM)),
                  lambda ws=ws: (view.anim.fade(ws, ws[0].cget("bg"), BG, 170, key=("tile", id(ws[0]))),
                                 ws[2].configure(fg=STONE)))
            for w in ws:
                w.bind("<ButtonRelease-1>", lambda ev, k=key: view.click("page:" + k))
        self.apply(e)

    def apply(self, e):
        super().apply(e)
        for key, badge in self.badges.items():
            n = e.get("badges", {}).get(key, 0)
            if n:
                badge.configure(text=str(n))
                badge.place(relx=0.5, x=7, y=2)
            else:
                badge.place_forget()


class BusyRow(Row):
    def __init__(self, view, parent, e):
        super().__init__(view, parent, e)
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=14, pady=(4, 0))
        self.sp = tk.Label(row, image=view.spinner(0, CLAY), bg=BG)
        self.sp.pack(side="left", padx=(0, 8))
        self.tx = tk.Label(row, font=view.f.bold, fg=CLAY, bg=BG, anchor="w")
        self.tx.pack(side="left", fill="x")
        drow = tk.Frame(parent, bg=BG)
        drow.pack(fill="x", padx=(40, 14), pady=(0, 2))
        self.dic = tk.Label(drow, bg=BG)
        self.dic.pack(side="left", padx=(0, 5))
        self.dt = tk.Label(drow, font=view.f.small, fg=STONE, bg=BG, anchor="w")
        self.dt.pack(side="left", fill="x")
        self.apply(e)

    def apply(self, e):
        super().apply(e)
        self.tx.configure(text=f"{e['text']}…")
        icon, color, text = split_glyph(e.get("detail", ""))
        self.dic.configure(image=self.view.icon(icon, 11, color) if icon else "")
        if not icon:
            self.dic.pack_forget()
        elif not self.dic.winfo_ismapped():
            self.dic.pack(side="left", padx=(0, 5), before=self.dt)
        self.dt.configure(text=text[:46])

    def tick(self, i):
        self.sp.configure(image=self.view.spinner(i, CLAY))
        self.tx.configure(fg=lerp_color(CLAY, SHIMMER, (1 + __import__("math").sin(i / 5)) / 2))


ROWS = {"rule": RuleRow, "note": NoteRow, "section": SectionRow, "kv": KvRow, "client": ClientRow,
        "pending": PendingRow, "item": ItemRow, "bar": BarRow, "busy": BusyRow}


# ===================================================================== header
class Header:
    """Logo · WireSpot · version ............ state pill. Optional mac-style dots on the left."""

    def __init__(self, view: "PanelView", parent, dots: bool = False, on_close=None, on_minimize=None, drag=None):
        self.view = view
        f, bg = view.f, BG
        self.frame = tk.Frame(parent, bg=bg)
        self.frame.pack(fill="x", padx=12, pady=(10, 6))
        self.dots = []
        if dots:
            box = tk.Frame(self.frame, bg=bg)
            box.pack(side="left", padx=(2, 12))
            for kind, color, glyph, cb in (("close", CLAY, "x", on_close), ("min", SHIMMER, "minus", on_minimize)):
                lb = tk.Label(box, bg=bg, cursor="hand2")
                lb.pack(side="left", padx=(0, 7))
                lb.bind("<ButtonRelease-1>", lambda e, cb=cb: cb and cb())
                self.dots.append((lb, color, glyph))
            hover(box, [box] + [lb for lb, _, _ in self.dots], lambda: self._dots(hover=True),
                  lambda: self._dots(hover=False))
            self.active = True
            self._dots(False)
        self.logo = tk.Label(self.frame, image=view.icons.logo(20, "live"), bg=bg)
        self.logo.pack(side="left")
        self.name = tk.Label(self.frame, text=f" {APP_NAME}", font=f.title, fg=CREAM, bg=bg)
        self.name.pack(side="left")
        self.ver = tk.Label(self.frame, text=f"  v{VERSION}", font=f.small, fg=DIM, bg=bg)
        self.ver.pack(side="left", pady=(3, 0))
        self.pill_t = tk.Label(self.frame, font=f.bold, bg=bg)
        self.pill_t.pack(side="right")
        self.pill_i = tk.Label(self.frame, bg=bg)
        self.pill_i.pack(side="right", padx=(0, 5))
        self.state = None
        if drag is not None:                      # a CustomWindow: drag it by the header
            drag.bind_drag(self.frame, self.logo, self.name, self.ver, self.pill_t, self.pill_i)
            for w in (self.frame, self.logo, self.name, self.ver, self.pill_t, self.pill_i):
                w.configure(cursor="fleur")

    def _dots(self, hover: bool) -> None:
        for lb, color, glyph in self.dots:
            fill = color if getattr(self, "active", True) or hover else BORDER
            lb.configure(image=self.view.icons.doc(("dot", fill, glyph if hover else None),
                                                   icons_mod.dot_doc(fill, glyph if hover else None, BG), 13))

    def set_active(self, active: bool) -> None:
        self.active = active
        if self.dots:
            self._dots(False)

    def pill(self, icon: str | None, text: str, color: str) -> None:
        """A custom pill (setup: 'installing', 'installed')."""
        self.state = "custom:" + text
        self.pill_t.configure(text=text, fg=color)
        self.pill_i.configure(image=self.view.icon(icon, 12, color) if icon else "")

    def update(self, st: str) -> None:
        if st == self.state:
            return
        self.state = st
        icon, label, color = STATES[st]
        self.pill_t.configure(text=label, fg=color)
        if icon != "spinner":
            self.pill_i.configure(image=self.view.icon(icon, 12, color))
        logo_state = {"unknown": "idle"}.get(st, st)
        self.logo.configure(image=self.view.icons.logo(20, logo_state if logo_state in ("live", "vpn", "idle", "busy",
                                                                                      "paused", "error") else "idle"))

    def tick(self, i: int) -> None:
        if self.state == "busy":
            self.pill_i.configure(image=self.view.spinner(i, SHIMMER, 12))


# ===================================================================== view
class PanelView:
    """Renders ``home_model`` into ``parent`` and keeps it current without flicker."""

    def __init__(self, parent, ctl, fonts: Fonts, iconset: icons_mod.IconSet, anim: Animator, mode: str,
                 on_action):
        self.parent, self.ctl, self.f, self.icons, self.anim, self.mode = parent, ctl, fonts, iconset, anim, mode
        self.root = parent.winfo_toplevel()
        self.on_action = on_action
        self.body: tk.Frame | None = None
        self.rows: list[Row] = []
        self.sig = None

    # ------------------------------------------------------------ images
    def icon(self, name, size, color):
        if name == "spinner":
            return self.spinner(0, color, size)
        return self.icons.get(name, size, color)

    def spinner(self, i: int, color: str, size: int = 16):
        k = i % SPIN_FRAMES
        return self.icons.doc(("spin", k, color), icons_mod.spinner_doc(k, SPIN_FRAMES, color, SELECTED), size, color)

    def switch(self, p: float):
        p = round(p * 12) / 12
        track = lerp_color(SELECTED, CLAY, p)
        knob = lerp_color(STONE, CREAM, p)
        return self.icons.wide(("switch", p), icons_mod.switch_doc(p, track, knob), 36, 20)

    def button(self, parent, text, icon, command, primary=False, enabled=True) -> tk.Frame:
        bg, hover_bg = ((CLAY, SHIMMER) if primary else (SELECTED, ROW_HOVER)) if enabled else (RULE, RULE)
        fg = (BG if primary else LIGHT) if enabled else DIM
        b = tk.Frame(parent, bg=bg, cursor="hand2" if enabled else "arrow")
        ic = tk.Label(b, image=self.icons.get(icon, 12, fg), bg=bg)
        ic.pack(side="left", padx=(8, 3), pady=3)
        lb = tk.Label(b, text=text, font=(self.f.mono, 9, "bold"), fg=fg, bg=bg)
        lb.pack(side="left", padx=(0, 9), pady=3)
        ws = (b, ic, lb)
        if enabled:
            hover(b, ws, lambda: self.anim.fade(ws, b.cget("bg"), hover_bg, 100, key=("btn", id(b))),
                  lambda: self.anim.fade(ws, b.cget("bg"), bg, 150, key=("btn", id(b))))
            for w in ws:
                w.bind("<ButtonRelease-1>", lambda e: command())
        return b

    # ------------------------------------------------------------ model + render
    def model(self) -> list[dict]:
        busy = self.ctl.busy_label()
        last = self.ctl.activity.last(1) if busy and self.ctl.busy else []
        return home_model(self.ctl.snap, self.mode, busy, last[0] if last else "")

    def build(self, parent, model) -> list[Row]:
        return [ROWS[e["t"]](self, parent, e) for e in model]

    def refresh(self, place=None) -> bool:
        """Update in place; rebuild (off-screen, then swap) only if the row structure changed.
        ``place(new_frame)`` may resize the host before the swap. Returns True on rebuild."""
        m = self.model()
        sig = [(e["t"], e.get("id")) for e in m]
        if sig == self.sig and self.body is not None:
            for row, e in zip(self.rows, m):
                if row.e != e:
                    row.apply(e)
            return False
        new = tk.Frame(self.parent, bg=BG)
        rows = self.build(new, m)
        old = self.body
        if place is not None:
            new.update_idletasks()          # measures the unmapped frame; nothing visible changes
            place(new)
        new.pack(fill="x", before=old) if old is not None else new.pack(fill="x")
        self.body, self.rows, self.sig = new, rows, sig
        if old is not None:
            old.destroy()
        return True

    def tick(self, i: int) -> None:
        for r in self.rows:
            if isinstance(r, BusyRow):
                r.tick(i)

    def click(self, id_: str) -> None:
        self.on_action(id_)


def dispatch(ctl, id_: str, host) -> bool:
    """Run a panel action. ``host`` provides open_window(page), open_page(page), quit_app(), close_menu().
    Returns True if the menu should close (tray)."""
    snap = ctl.snap
    if id_ == "open":
        host.open_window(None)
        return True
    if id_.startswith("page:"):
        host.open_page(id_[5:])
        return host.is_tray
    if id_ == "profiles":
        host.open_page("profiles")
        return host.is_tray
    if id_ == "quit":
        host.quit_app()
        return True
    if id_.startswith("allow:"):
        mac = id_[6:]
        ctl.device_verdict(mac, "approve", _name_for(snap, mac))
        return False
    if id_.startswith("block:"):
        mac = id_[6:]
        ctl.device_verdict(mac, "block", _name_for(snap, mac))
        return False
    simple = {"reconnect": ("reconnect", None), "hotspot_off": ("hotspot_off", None), "hotspot_on": ("hotspot_on", None),
              "pause15": ("pause", 15), "pause60": ("pause", 60), "disconnect": ("disconnect", None),
              "check_ip": ("check_ip", None), "golive": ("golive", snap.get("profile") or None)}
    if id_ in simple:
        ctl.do(*simple[id_])
        return False
    if id_ == "resume":
        ctl.cancel_pause()
        ctl.do("golive", snap.get("profile") or None)
    elif id_ == "cancel_pause":
        ctl.cancel_pause()
    elif id_ == "doctor":
        host.open_page("checks")
        host.run_doctor("quick")
        return host.is_tray
    elif id_ == "copy_pw":
        ctl.copy_password()
    elif id_ == "cli":
        ctl.open_cli()
    elif id_ == "autostart":
        ctl.toggle_autostart()
    elif id_ == "autoconnect":
        ctl.toggle_autoconnect()
    elif id_ == "approve":
        ctl.toggle_approval()
    return False


def _name_for(snap, mac) -> str:
    for p in snap.get("pending") or []:
        if p["mac"] == mac:
            n = p.get("name")
            return n if n and n != "(no name)" else p.get("device", "")
    return ""
