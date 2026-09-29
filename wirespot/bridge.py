"""The WireSpot engine for the desktop app (Tauri + Vue): no window, JSON lines.

The app starts this process and talks to it over stdin/stdout, one JSON object
per line:

    app -> engine   {"id": 7, "method": "do", "params": {"action": "golive"}}
    engine -> app   {"id": 7, "result": ...}      or {"id": 7, "error": "..."}
                    {"event": "snap", "data": {...}}                (pushed)

It runs the same Controller the tkinter window used, so the relay, the guard,
device approval, the sync with the CLI and the activity journal behave exactly
as before. Everything the engine prints goes to its activity log; only this
protocol is written to stdout. Private keys are never sent: profiles, devices
and reviews are converted field by field, never dumped wholesale.

    python -m wirespot.bridge [--autostart]
"""
from __future__ import annotations

import itertools
import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

# The protocol channel: the real stdout, taken before the Controller redirects
# sys.stdout/sys.stderr into its activity log.
_OUT = open(sys.stdout.fileno(), "w", encoding="utf-8", buffering=1, newline="\n", closefd=False)
_IN = open(sys.stdin.fileno(), "r", encoding="utf-8", newline="\n", closefd=False)
_OUT_LOCK = threading.Lock()

from . import (APP_NAME, PRIVACY_VERSION, TAGLINE, TERMS_VERSION, VERSION, WEBSITE, admission, log,  # noqa: E402
               oplock, paths, settings as settings_mod, sync, winexec)
from .controller import Controller, icon_state, tooltip  # noqa: E402
from .inbox import Candidate, downloads_dir  # noqa: E402
from .model import dispatch, home_model, status_rows  # noqa: E402
from .status import normalize_status  # noqa: E402

PROTON_GUIDE = "https://protonvpn.com/support/wireguard-configurations"
DOCTOR_SECTIONS = [("quick", "Quick"), ("wifi", "Wi-Fi"), ("vpn", "VPN"), ("hotspot", "Hotspot"),
                   ("ics", "Sharing"), ("network", "Network"), ("clients", "Devices"), ("full", "Full")]

# snapshot fields the app may see (everything else stays in the engine)
SNAP_KEYS = ("state", "saved_state", "fresh", "busy", "ssid", "band", "security", "protection", "dns_lock",
             "tunnel", "profile", "profile_label", "provider", "provider_protocol", "provider_status",
             "provider_reason", "provider_adapter", "ready_since", "last_error", "paused_until", "autostart",
             "rx", "tx", "handshake", "hotspot_state", "endpoint", "uplink", "exit_ip", "pending", "blocked_n",
             "profiles", "clients")


# returned by a handler that answers later, from its own thread
_DEFERRED = object()


def send(obj: dict) -> None:
    line = json.dumps(obj, ensure_ascii=False, default=_jsonable)
    with _OUT_LOCK:
        _OUT.write(line + "\n")
        _OUT.flush()


def event(name: str, data=None) -> None:
    send({"event": name, "data": data})


def _jsonable(o):
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


# ------------------------------------------------------------------ report lines
_LEVELS = {"●": "ok", "•": "ok", "*": "ok", "+": "ok", "✓": "ok", "▲": "warn", "!": "warn",
           "✖": "error", "×": "error", "✻": "head", "?": "question"}
_BOX = re.compile(r"[─-╿⎿❯]+")


def line_view(line: str) -> dict:
    """A CLI/ui output line -> {level, text}, without glyphs or box drawing."""
    s = line.strip()
    level = "text"
    if s[:1] in _LEVELS and (len(s) == 1 or s[1] == " "):
        level = _LEVELS[s[0]]
        s = s[1:].strip()
    elif s.startswith("──"):
        level = "head"
    elif s.startswith("⎿"):
        level = "dim"
        s = s[1:].strip()
    s = s.replace("→", " to ").replace("✓", "").replace("↓", "in").replace("↑", "out")
    s = _BOX.sub(" ", s).strip()
    return {"level": level, "text": re.sub(r"\s{2,}", "  ", s)}


# ------------------------------------------------------------------ views
def client_view(c) -> dict:
    return {"mac": c.mac, "ip": c.ip, "name": c.display_name if c.hostnames else "", "display": c.display_name,
            "device": c.device, "vendor": c.vendor, "randomized": c.randomized,
            "access": getattr(c, "access", "approved")}


def profile_view(p, default: str) -> dict:
    return {"name": p.path.name, "stem": p.path.stem, "label": p.label, "server": p.server_name or p.path.stem,
            "country": p.country or "", "country_code": p.country_code or "",
            "entry_country_code": p.server.entry_country_code or "", "free": bool(p.server.free),
            "endpoint": p.endpoint or "", "full_tunnel": bool(p.full_tunnel), "allowed_ips": list(p.allowed_ips),
            "features": list(p.features), "default": p.path.name == default}


def snap_view(ctl: Controller) -> dict:
    s = normalize_status(ctl.snap)
    out = {k: s.get(k) for k in SNAP_KEYS if k in s}
    out["icon_state"] = icon_state(s)
    out["tooltip"] = tooltip(s)
    out["rows"] = [list(r) for r in status_rows(s)]
    busy = s.get("busy")
    detail = (ctl.activity.last(1) or [""])[0] if busy else ""
    out["model"] = [e for e in home_model(s, "window", busy=busy, detail=detail)
                    if e.get("t") in ("item", "busy", "section", "pending")]
    out["devices"] = [client_view(c) for c in s.get("clients_full") or []]
    out["settings"] = s.get("settings") or settings_mod.load()[0]
    out["now"] = time.time()
    return out


class Bridge:
    def __init__(self, autostarted: bool):
        self.ctl = Controller(autostarted=autostarted)
        self.asks: dict[int, dict] = {}
        self.reviews: dict[int, Candidate] = {}
        self.ids = itertools.count(1)
        self.report = ("", [])
        self.quitting = False
        self._rid = None
        # updates: what the last check found, and the download in progress
        self._update: dict | None = None
        self._update_thread: threading.Thread | None = None
        self._update_cancel = threading.Event()

    # host interface for model.dispatch
    is_tray = False

    def open_window(self, page=None):
        event("navigate", {"page": page or "home"})

    def open_page(self, page):
        event("navigate", {"page": page})

    def quit_app(self):
        event("quit")

    def run_doctor(self, section: str) -> None:
        self.report = (section, [])
        event("report", {"section": section, "running": True, "lines": []})
        self.ctl.do("doctor", section)

    # ------------------------------------------------------------------ main loop
    def run(self) -> int:
        ctl = self.ctl
        threading.Thread(target=self._read_requests, name="bridge-stdin", daemon=True).start()
        if paths.is_installed():
            from . import updater

            threading.Thread(target=updater.clean_downloads, name="update-cleanup", daemon=True).start()
        ctl.start()
        event("ready", self.hello())
        while not self.quitting:
            try:
                ev = ctl.events.get(timeout=0.5)
            except Exception:
                continue
            try:
                self._handle_event(ev)
            except Exception:
                log.event("error", "bridge: " + traceback.format_exc())
        ctl.stop()
        return 0

    def _read_requests(self) -> None:
        for raw in _IN:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self.ctl.events.put(("__request", msg))
        # the app went away: stop cleanly (VPN and hotspot keep running, like Quit)
        self.ctl.events.put(("__request", {"method": "quit"}))

    def _handle_event(self, ev: tuple) -> None:
        ctl = self.ctl
        kind = ev[0]
        if kind == "__request":
            return self._handle_request(ev[1])
        if kind == "snap":
            if len(ev) > 1:
                ctl.accept_snapshot(ev[1])
            event("snap", snap_view(ctl))
        elif kind == "busy":
            ctl.snap = {**ctl.snap, "busy": ctl.busy_label()}
            event("snap", snap_view(ctl))
        elif kind == "toast":
            event("toast", {"title": ev[1], "body": ev[2], "error": bool(ev[3])})
        elif kind == "pending":
            c = ev[1]
            event("pending", {"mac": c["mac"], "ip": c.get("ip", ""), "name": c.get("name", ""),
                              "device": c.get("device", "")})
        elif kind == "notify":
            event("notify", {"kind": ev[1], "message": ev[2]})
            ctl.refresh_now()
        elif kind == "report":
            section, lines = ev[1], ev[2]
            self.report = (section, list(lines))
            event("report", {"section": section, "running": False, "lines": [line_view(x) for x in lines]})
        elif kind == "measurement":
            if ev[1] == "profile":
                event("measurement", {"kind": "profile", "name": ev[2], "check": ctl.profile_checks.get(ev[2])})
            else:
                event("measurement", {"kind": "speed", "state": ctl.speed_result})
        elif kind == "ask":
            _, question, choices, default, holder = ev
            ask_id = next(self.ids)
            self.asks[ask_id] = {"holder": holder, "choices": choices, "default": default}
            event("ask", {"id": ask_id, "question": question, "default": default,
                          "choices": [{"key": c.key, "label": c.label, "hint": getattr(c, "hint", "") or ""}
                                      for c in choices]})
        elif kind == "inbox":
            self._offer_review(ev[1])
        elif kind == "clipboard":
            event("clipboard", {"text": ev[1]})
        elif kind == "action":
            ctl.do(ev[1], ev[2])
        elif kind == "uninstalled":
            event("uninstalled", {"message": ev[1]})

    # ------------------------------------------------------------------ requests
    def _handle_request(self, msg: dict) -> None:
        rid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
        handler = getattr(self, "rpc_" + method, None)
        self._rid = rid
        try:
            if handler is None:
                raise ValueError(f"unknown method: {method}")
            result = handler(**params)
            if result is _DEFERRED:
                return
            if rid is not None:
                send({"id": rid, "result": result})
        except Exception as e:
            log.event("error", f"bridge {method}: {e}")
            if rid is not None:
                send({"id": rid, "error": f"{type(e).__name__}: {e}" if not isinstance(e, ValueError) else str(e)})

    def hello(self) -> dict:
        icons = {}
        try:
            from .desktop import ensure_icons
            icons = {k: str(v) for k, v in ensure_icons().items()}
        except Exception as e:
            log.event("error", f"icons: {e}")
        return {"app": APP_NAME, "version": VERSION, "tagline": TAGLINE, "website": WEBSITE,
                "terms_version": TERMS_VERSION, "privacy_version": PRIVACY_VERSION,
                "installed": paths.is_installed(), "admin": winexec.is_admin(), "autostarted": self.ctl.autostarted,
                "proton_guide": PROTON_GUIDE, "doctor_sections": DOCTOR_SECTIONS, "icons": icons,
                "paths": {"app": str(paths.APP_DIR), "data": str(paths.BASE), "vpn": str(paths.VPN_DIR),
                          "logs": str(paths.LOG_DIR), "downloads": str(downloads_dir())}}

    def rpc_hello(self):
        return self.hello()

    def _answer_later(self, work) -> object:
        """Answers the current request with ``work()``'s result from its own thread:
        network calls must not hold up the loop that runs the guard."""
        rid = self._rid

        def run():
            try:
                result = work()
                if rid is not None:
                    send({"id": rid, "result": result})
            except Exception as e:
                log.event("error", f"bridge: {e}")
                if rid is not None:
                    send({"id": rid, "error": str(e)})

        threading.Thread(target=run, daemon=True).start()
        return _DEFERRED

    # ------------------------------------------------------------------ updates
    def rpc_update_check(self):
        from . import updater

        def work():
            info = updater.check()
            self._update = info if info["available"] else None
            return updater.public(info)

        return self._answer_later(work)

    def rpc_update_download(self):
        """Starts the download; "update_progress" events follow, then "update_ready"
        (with the checked file) or "update_failed"."""
        from . import updater

        info = self._update
        if info is None:
            raise ValueError("Check for updates first.")
        if self._update_thread is not None and self._update_thread.is_alive():
            raise ValueError("The update is already downloading.")
        self._update_cancel = cancel = threading.Event()

        def work():
            try:
                path = updater.download(
                    info, lambda done, total: event("update_progress", {"downloaded": done, "total": total}), cancel)
                event("update_ready", {"path": str(path), "version": info["latest"]})
            except updater.Cancelled:
                event("update_failed", {"cancelled": True, "error": ""})
            except Exception as e:
                log.event("error", f"update download: {e}")
                event("update_failed", {"cancelled": False, "error": str(e)})

        self._update_thread = threading.Thread(target=work, name="update-download", daemon=True)
        self._update_thread.start()
        return True

    def rpc_update_cancel(self):
        self._update_cancel.set()
        return True

    def rpc_snapshot(self):
        return snap_view(self.ctl)

    def rpc_visible(self, visible: bool):
        self.ctl.window_visible = self.ctl.fast_poll = bool(visible)
        if visible:
            self.ctl.refresh_now()
        return True

    def rpc_action(self, id: str):
        """A home-model action (the same ids the tray panel uses)."""
        dispatch(self.ctl, id, self)
        return True

    def rpc_do(self, action: str, arg=None):
        self.ctl.do(action, arg)
        return True

    def rpc_device(self, mac: str, verdict: str, name: str = ""):
        if verdict not in ("approve", "block", "forget"):
            raise ValueError("verdict must be approve, block or forget")
        self.ctl.device_verdict(mac, verdict, name)
        return True

    def rpc_devices(self):
        store = admission.load()
        here = {c["mac"] for c in snap_view(self.ctl)["devices"]}
        on = (self.ctl.snap.get("settings") or settings_mod.load()[0])["behavior"].get("approve_devices", True)
        remembered = [{"mac": m, "name": i.get("name") or ""} for m, i in store["approved"].items() if m not in here]
        return {"approval": on, "remembered": remembered[:50]}

    def rpc_set_behavior(self, key: str, value):
        self.ctl.set_behavior(key, value)
        return True

    def rpc_toggle(self, name: str):
        fn = {"autostart": self.ctl.toggle_autostart, "approve": self.ctl.toggle_approval,
              "autoconnect": self.ctl.toggle_autoconnect}.get(name)
        if not fn:
            raise ValueError(f"unknown toggle: {name}")
        fn()
        return True

    def rpc_settings(self):
        return settings_mod.load()[0]

    def rpc_save_hotspot(self, ssid: str, password: str, band: str, security: str, restart: bool = False):
        s, _ = settings_mod.load()
        s["hotspot"].update(ssid=ssid.strip(), password=password, band=band, security=security)
        problems = settings_mod.validate_hotspot(s)
        if problems:
            return {"ok": False, "problems": problems}
        settings_mod.save(s)
        log.register_secret(s["hotspot"]["password"])
        self.ctl.refresh_now()
        if restart:
            self.ctl.do("hotspot_on")
        return {"ok": True, "problems": []}

    def rpc_set_protection(self, key: str):
        if key not in settings_mod.PROTECTION:
            raise ValueError("protection must be strict or balanced")
        s, _ = settings_mod.load()
        s["vpn"]["protection"] = key
        settings_mod.save(s)
        self.ctl.refresh_now()
        return True

    def rpc_profiles(self):
        from . import profiles

        s, _ = settings_mod.load()
        default = s["vpn"].get("default_profile", "")
        snap = normalize_status(self.ctl.snap)
        external = bool(s["behavior"].get("profile_less"))
        profs, bad = ([], []) if external else profiles.list_profiles(paths.VPN_DIR)
        return {"mode": "nordvpn" if external else "wireguard", "default": default,
                "profiles": [profile_view(p, default) for p in profs],
                "bad": [{"name": Path(p).name, "why": why} for p, why in bad],
                "checks": self.ctl.profile_checks,
                "provider": {"status": snap.get("provider_status") or "", "reason": snap.get("provider_reason") or "",
                             "protocol": snap.get("provider_protocol") or "", "adapter": snap.get("provider_adapter") or ""}}

    def rpc_set_default_profile(self, name: str):
        self.ctl.set_default_profile(name)
        return True

    def rpc_scan_provider(self):
        self.ctl.provider_scan_at = 0
        self.ctl.refresh_now()
        return True

    # --- importing .conf files: the same review card the window showed
    def _review_view(self, cand: Candidate) -> dict:
        info = self.ctl.inbox.inspect(cand)
        rid = next(self.ids)
        self.reviews[rid] = cand
        view = {"id": rid, "file": cand.path.name, "origin": cand.origin}
        if info.duplicate:
            view["duplicate"] = Path(info.duplicate).name
        elif info.error:
            view["error"] = info.error
        else:
            p = info.profile
            iv, peer = p.config.interface.values, p.config.peers[0].values
            where = (f"Secure Core via {p.server.entry_country_code} to {p.country_code}"
                     if p.server.entry_country_code else p.country or "unknown country")
            view["details"] = [
                ["server", f"{p.server_name or '(unnamed)'} · {where}" + (" · free" if p.server.free else "")],
                ["endpoint", peer.get("endpoint", "-")], ["address", iv.get("address", "-")],
                ["dns", iv.get("dns", "-")],
                ["routes", "full tunnel" if p.full_tunnel else peer.get("allowedips", "")],
                ["server key", peer.get("publickey", "-")[:22] + "…"],
                ["private key", "valid, never displayed"], ["your key", p.public_key[:22] + "…"],
                ["checks", "no scripts · keys valid"]]
        return view

    def _offer_review(self, cand: Candidate) -> None:
        event("review", self._review_view(cand))

    def rpc_import(self, path: str):
        p = Path(path)
        if not p.is_file():
            raise ValueError("That file doesn't exist.")
        return self._review_view(Candidate(p, "import", ""))

    def rpc_review(self, id: int, choice: str | None):
        cand = self.reviews.pop(int(id), None)
        if cand is None:
            raise ValueError("That review is no longer open.")
        if choice in ("move", "copy"):
            ok, msg = self.ctl.import_conf(cand.path, move=choice == "move")
            event("toast", {"title": "Profile imported" if ok else "Not imported", "body": msg, "error": not ok})
            return {"ok": ok, "message": msg}
        if choice == "decline":
            self.ctl.inbox.decline(cand)
            event("toast", {"title": "Declined", "body": f"{cand.path.name} was not imported.", "error": False})
        return {"ok": True, "message": ""}

    # --- decisions the relay asks while going live
    def rpc_answer(self, id: int, key: str):
        ask = self.asks.pop(int(id), None)
        if ask is None:
            return False
        ask["holder"]["answer"] = key
        ask["holder"]["event"].set()
        return True

    # --- checks & activity
    def rpc_doctor(self, section: str = "quick"):
        if section not in dict(DOCTOR_SECTIONS):
            raise ValueError("unknown check")
        self.run_doctor(section)
        return True

    def rpc_checks(self):
        """What the Checks page shows when it opens: the last report and speed test."""
        section, lines = self.report
        return {"report": {"section": section, "running": bool(section) and not lines,
                           "lines": [line_view(x) for x in lines]},
                "speed": self.ctl.speed_result}

    def rpc_save_report(self):
        section, lines = self.report
        if not lines:
            raise ValueError("Run a check first.")
        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = paths.LOG_DIR / f"doctor-{section}-{datetime.now():%Y%m%d-%H%M%S}.txt"
        out.write_text(log.redact("\n".join(lines)) + "\n", encoding="utf-8")
        return str(out)

    def rpc_activity(self, limit: int = 500):
        return [{"ts": ts, "source": src, **line_view(line)} for ts, src, line in sync.read_journal(int(limit))]

    def rpc_open(self, target: str):
        fn = {"logs": self.ctl.open_logs, "vpn": self.ctl.open_vpn_folder, "data": self.ctl.open_data,
              "settings_file": self.ctl.open_settings_file, "cli": self.ctl.open_cli}.get(target)
        if not fn:
            raise ValueError(f"unknown target: {target}")
        fn()
        return True

    def rpc_uninstall(self, keep: bool = True):
        self.ctl.do("uninstall", bool(keep))
        return True

    def rpc_quit(self):
        self.quitting = True
        return True


class SetupBridge:
    """``--setup``: WireSpot.exe started outside its install folder shows its installer first.
    No Controller runs here (an older WireSpot may still be guarding a live hotspot): only
    what the installer screen needs. The program files come from the app (WIRESPOT_APP_EXE)
    and from where it unpacked its engine and CLI (paths.exe)."""

    def __init__(self):
        self.quitting = False

    def run(self) -> int:
        event("ready", self.hello())
        for raw in _IN:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            rid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
            handler = getattr(self, "rpc_" + method, None)
            try:
                if handler is None:
                    raise ValueError(f"unknown method: {method}")
                result = handler(**params)
                if rid is not None:
                    send({"id": rid, "result": result})
            except Exception as e:
                log.event("error", f"setup {method}: {e}")
                if rid is not None:
                    send({"id": rid, "error": str(e)})
            if self.quitting:
                break
        return 0

    def hello(self) -> dict:
        return {"app": APP_NAME, "version": VERSION, "tagline": TAGLINE, "website": WEBSITE, "setup": True,
                "terms_version": TERMS_VERSION, "privacy_version": PRIVACY_VERSION, "admin": winexec.is_admin(),
                "installed": False, "autostarted": False, "proton_guide": PROTON_GUIDE,
                "doctor_sections": DOCTOR_SECTIONS, "icons": {},
                "paths": {"app": str(paths.APP_DIR), "data": str(paths.appdata_dir()), "vpn": "", "logs": "",
                          "downloads": str(downloads_dir())}}

    def rpc_hello(self):
        return self.hello()

    def rpc_setup_info(self):
        from . import autostart, installer, wireguard

        dest = paths.install_dir()
        legacy = installer.find_legacy(paths.APP_DIR)
        return {"install_dir": str(dest), "data_dir": str(paths.appdata_dir()),
                "installed_version": installer.installed_version(dest),
                "old_app_running": installer.app_running(),
                "legacy": str(legacy) if legacy else "",
                "start_with_windows": autostart.is_enabled(),
                "wireguard": bool(wireguard.find_wireguard(""))}

    def rpc_install(self, desktop: bool = True, start_menu: bool = True, start_with_windows: bool | None = None,
                    force_close: bool = False, update: bool = False):
        """``update``: started by an update from the installed copy. Its shortcuts, Start with
        Windows and when the Terms were agreed to all stay as they were."""
        from . import installer, updater

        app_exe = os.environ.get("WIRESPOT_APP_EXE") or str(paths.exe("WireSpot.exe"))
        files = {"WireSpot.exe": Path(app_exe),
                 "WireSpotEngine.exe": paths.exe("WireSpotEngine.exe"),
                 "WireSpotCLI.exe": paths.exe("WireSpotCLI.exe")}
        accepted = {"terms": TERMS_VERSION, "privacy": PRIVACY_VERSION,
                    "at": datetime.now().astimezone().isoformat(timespec="seconds")}
        if update:
            accepted = updater.previous_acceptance() or accepted
        try:
            result = installer.install(files, progress=lambda text: event("setup_progress", {"text": text}),
                                       legacy_near=paths.APP_DIR, shortcuts=not update, desktop=bool(desktop),
                                       start_menu=bool(start_menu),
                                       start_with_windows=None if update else start_with_windows,
                                       force_close=bool(force_close or update), accepted=accepted)
        except installer.AppStillRunning as e:
            return {"ok": False, "still_running": True, "error": str(e)}
        except Exception as e:
            log.event("error", f"install: {traceback.format_exc()}")
            return {"ok": False, "still_running": False, "error": f"{e}"}
        return {"ok": True, **result, "exe": str(Path(result["dest"]) / "WireSpot.exe")}

    def rpc_quit(self):
        self.quitting = True
        return True


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--setup" in argv:
        # nothing may print to the protocol channel (_OUT keeps the real stdout)
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
        try:
            return SetupBridge().run()
        finally:
            os._exit(0)
    sync.SOURCE = "app"
    # One app at a time; the CLI checks this to know the app is guarding the session.
    instance = oplock.NamedMutex(oplock.TRAY)
    instance.acquire(0)
    from .desktop import install_crash_handlers

    install_crash_handlers()
    s, _ = settings_mod.load()
    if s["behavior"].get("debug"):
        log.enable()
    try:
        return Bridge(autostarted="--autostart" in argv).run()
    finally:
        os._exit(0)          # daemon threads (watchers, guard) must not keep the process alive


if __name__ == "__main__":
    sys.exit(main())
