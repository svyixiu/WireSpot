"""The WireSpot panel model, without any UI toolkit.

Pure functions shared by the desktop app (Tauri + Vue, through bridge.py),
the old tkinter panel and the tests: which status lines and actions a
snapshot shows, and what each action does.
"""
from __future__ import annotations

import time

from . import wireguard
from .controller import fmt_duration, icon_state
from .status import normalize_status

PAGES = [("devices", "phone", "Devices"), ("profiles", "server", "Profiles"), ("hotspot", "wifi", "Hotspot"),
         ("checks", "pulse", "Checks"), ("activity", "list", "Activity"), ("settings", "sliders", "Settings")]


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
    external = bool(snap.get("settings", {}).get("behavior", {}).get("profile_less"))
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
    if external:
        rows = [("NordVPN" if k == "profile" else k, v) for k, v in rows]
        if st == "idle":
            rows.insert(0, ("connection", (snap.get("provider_reason") or "Checking NordVPN…")[:55]))
        if snap.get("provider_adapter") and st in ("live", "vpn"):
            rows.append(("protocol", snap.get("provider_protocol") or "Unknown"))
    if snap.get("uplink") and st in ("live", "vpn", "idle"):
        rows.append(("uplink", snap["uplink"]))
    return rows


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
        if len(profs) > 1 and not b.get("profile_less"):
            item("profiles", "server", "Choose a profile", f"{len(profs)} available")
    add({"t": "rule", "id": "r2"})
    item("check_ip", "globe", "Check exit IP", snap.get("exit_ip") or "")
    item("doctor", "pulse", "Quick doctor", "health check")
    item("copy_pw", "key", "Copy Wi-Fi password", snap.get("ssid", ""))
    item("cli", "terminal", "Open WireSpot CLI")
    add({"t": "rule", "id": "r3"})
    pages = [(k, icon, "Supported VPNs" if k == "profiles" and b.get("profile_less") else title)
             for k, icon, title in PAGES]
    add({"t": "bar", "id": "pages", "items": pages,
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
