"""The desktop app's brain, shared by the main window and the tray panel.

Runs relay operations on worker threads (never on the Tk thread), polls a
status snapshot, runs the pause/auto-resume timer, watches Downloads for
.conf files, and talks to the UI only through ``events`` (a queue the Tk
thread pumps). Decisions the relay needs are asked in a dialog when the
window is open, and answered with the recommended choice otherwise.
"""
from __future__ import annotations

import collections
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import admission, autostart, clients as clients_mod, doctor, log, network_tests, paths, profiles
from . import settings as settings_mod, sync, ui, wireguard
from .backend import Backend
from .inbox import Candidate, Inbox
from .relay import Relay
from .state import State
from .status import normalize_status
from copy import deepcopy



class Activity:
    """Collects everything ui prints (the desktop app has no console)."""

    def __init__(self):
        self.lines: collections.deque[str] = collections.deque(maxlen=1000)
        self._buf = ""
        self.lock = threading.Lock()
        self.serial = 0

    def write(self, s: str) -> int:
        with self.lock:
            self._buf += s.replace("\r", "\n")
            *done, self._buf = self._buf.split("\n")
            for line in done:
                if line.strip():
                    self.lines.append(time.strftime("%H:%M:%S ") + ui._ANSI.sub("", line))
                    self.serial += 1
        return len(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def last(self, n: int = 1) -> list[str]:
        with self.lock:
            return [x[9:] for x in list(self.lines)[-n:]]

    def all(self) -> list[str]:
        with self.lock:
            return list(self.lines)

    def mark(self) -> int:
        with self.lock:
            return self.serial

    def since(self, mark: int) -> list[str]:
        with self.lock:
            n = self.serial - mark
            return [x[9:] for x in list(self.lines)[-n:]] if n > 0 else []


def fmt_duration(secs: float) -> str:
    s = int(max(0, secs))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def icon_state(snap: dict) -> str:
    """Pure: which state the icon/pill shows for a status snapshot."""
    if snap.get("busy"):
        return "busy"
    if snap.get("paused_until"):
        return "paused"
    st = snap.get("state")
    if st in ("UNKNOWN", "STARTUP"):
        return "unknown"
    if st in (State.VPN_CONNECTING.value, State.HOTSPOT_STARTING.value, State.STOPPING.value):
        return "busy"
    if st == State.READY.value:
        return "live"
    if st == State.ERROR.value:
        return "error"
    if st in (State.VPN_CONNECTED.value, State.HOTSPOT_ACTIVE.value, State.SHARING_CONFIGURING.value):
        return "vpn"
    return "idle"


def tooltip(snap: dict) -> str:
    snap = normalize_status(snap)
    st = icon_state(snap)
    if st == "live":
        n = len(snap.get("clients") or [])
        waiting = len(snap.get("pending") or [])
        return (f"WireSpot · live · {snap.get('ssid')} · {n} device{'s' if n != 1 else ''}"
                + (f" · {waiting} waiting for approval" if waiting else ""))
    if st == "paused":
        return f"WireSpot · paused · resumes in {fmt_duration(snap['paused_until'] - time.time())}"
    return "WireSpot · " + {"vpn": "VPN connected, hotspot off", "idle": "disconnected",
                            "unknown": "checking network status", "busy": snap.get("busy") or "working", "error": "needs attention"}[st]


ACTION_LABELS = {"golive": "Going live", "reconnect": "Reconnecting", "disconnect": "Disconnecting",
                 "pause": "Pausing", "hotspot_on": "Starting the hotspot", "hotspot_off": "Stopping the hotspot",
                 "check_ip": "Checking exit IP", "doctor": "Running diagnostics", "uninstall": "Uninstalling WireSpot",
                 "profile_probe": "Checking profile", "speed_test": "Measuring connection speed"}


class Controller:
    def __init__(self, autostarted: bool = False):
        self.events: queue.Queue = queue.Queue()
        self.activity = Activity()
        sys.stdout = sys.stderr = self.activity
        ui.INTERACTIVE = False
        ui.FORCE_TIER = "fancy"
        sync.SOURCE = "app"
        ui.tee = sync.journal
        self.autostarted = autostarted
        self.backend = Backend()
        self.relay = Relay(self.backend, decide=self.decide)
        self.relay.notify = lambda kind, msg: self.events.put(("notify", kind, msg))
        configured, _ = settings_mod.load()
        self.snap = normalize_status({"saved_state": self.relay.m.state.value,
                                      "profile": self.relay.record.profile,
                                      "band": self.relay.record.band,
                                      "protection": self.relay.record.protection}, configured=configured)
        self.busy: str | None = None
        self.exit_ip = ("", 0.0)
        self.profile_checks: dict[str, dict] = {}
        self.speed_result: dict | None = None
        self.remote = None               # operation the CLI is running right now (sync bus)
        self.bus_rev = -1
        self.toasted_pending: set[str] = set()
        self.watcher = sync.Watcher(self._on_shared_change)
        self.running = True
        self.window_visible = False      # set by the main window
        self.fast_poll = False           # panel/window open -> poll every 3 s
        self.wg = None
        self._pollq: queue.Queue = queue.Queue()
        self._autostart = (False, 0.0)
        self.inbox = Inbox()
        self.inbox.on_new = lambda c: self.events.put(("inbox", c))

    @property
    def tray_state(self) -> dict:
        return sync.read_pause()

    def busy_label(self) -> str | None:
        return self.busy or (sync.describe(self.remote) if self.remote else None)

    def accept_snapshot(self, snapshot):
        """Publish on the Tk thread, between callbacks, with current action state."""
        if self.running:
            self.snap = normalize_status({**snapshot, "busy": self.busy_label()})
            for c in self.snap.get("pending") or []:
                if c["mac"] not in self.toasted_pending:
                    self.toasted_pending.add(c["mac"])
                    self.events.put(("pending", c))
            self._reconcile_watchers()

    def _reconcile_watchers(self) -> None:
        """Keep a guard + approval gate armed whenever a session is live, whoever started it.
        Only one process runs them at a time (named mutexes); this one takes over if the CLI exits."""
        if not self.snap.get("fresh"):
            return
        st = self.snap.get("state")
        r = self.relay
        if self.busy:
            return
        if st == State.READY.value and r.guard is None and r.gate is None:
            threading.Thread(target=self._arm, daemon=True).start()
        elif st in (State.DISCONNECTED.value, State.ERROR.value) and (r.guard or r.gate):
            r.stop_guard()

    def _arm(self) -> None:
        r = self.relay
        if r.lock.acquire(blocking=False):
            try:
                r.reload()
                if r.m.state == State.READY and r.guard is None and r.gate is None:
                    r.start_guard()
            finally:
                r.lock.release()

    def _on_shared_change(self, changed) -> None:
        """Sync watcher (background thread): the CLI or another window changed shared state."""
        names = {p.name for p in changed}
        if "bus.json" in names:
            bus = sync.read_bus()
            remote = sync.remote_op(bus)
            last = bus.get("last") or {}
            if (remote or None) != (self.remote or None):
                self.remote = remote
                self.events.put(("busy",))
            fresh = bus.get("rev", 0) != self.bus_rev and self.bus_rev != -1
            if last and last.get("pid") != os.getpid() and fresh:
                who = sync.SOURCE_LABEL.get(last.get("who"), "WireSpot")
                ok = last.get("ok", True)
                self.events.put(("toast", f"{who}: {last.get('label', 'done')}" + ("" if ok else " - failed"),
                                 last.get("message", ""), not ok))
            self.bus_rev = bus.get("rev", 0)
        self.refresh_now()

    # -------------------------------------------------------------- decisions
    def decide(self, question, choices, default=0, cancel=None):
        key = choices[default].key
        if self.window_visible:
            holder = {"event": threading.Event(), "answer": None}
            self.events.put(("ask", question, choices, default, holder))
            holder["event"].wait(600)
            key = holder["answer"] or cancel or choices[default].key
        label = next((c.label for c in choices if c.key == key), key)
        ui.info(f"? {question} → {label}")
        return key

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.bus_rev = sync.read_bus().get("rev", 0)
        self.remote = sync.remote_op()
        self.watcher.start()
        threading.Thread(target=self._startup, daemon=True).start()
        threading.Thread(target=self._poll_loop, daemon=True).start()
        s, _ = settings_mod.load()
        if s["behavior"].get("watch_downloads", True):
            self.inbox.start()

    def stop(self) -> None:
        self.running = False
        self.watcher.stop()
        self.inbox.stop()
        self.relay.stop_guard()
        if ui.tee is sync.journal:
            ui.tee = None

    def _startup(self) -> None:
        try:
            profiles.harden_dir(paths.RUNTIME_DIR)
            svcs, _ = self.backend.wg_services()
            running = {x.name for x in svcs if x.ours and x.running}
            with self.relay.lock:
                if not self.running:
                    return
                rec = self.relay.record
                s, _ = settings_mod.load()
                if self.relay.m.state != State.DISCONNECTED and rec.tunnel_name and rec.tunnel_name not in running:
                    self.relay.m.force(State.ERROR)
                elif self.relay.m.state == State.READY:
                    self.relay.start_guard(s)       # guard + approval; downgrades to 'vpn only' if the hotspot is off
            if self.autostarted and s["behavior"].get("autoconnect") and self.relay.m.state == State.DISCONNECTED:
                for _ in range(30):              # wait for a network after logon
                    if not self.running:
                        return
                    if self.backend.uplink():
                        break
                    time.sleep(2)
                self.events.put(("action", "golive", None))
        except Exception as e:
            log.event("error", f"startup: {e}")
        self.refresh_now()

    # -------------------------------------------------------------- polling
    def refresh_now(self) -> None:
        self._pollq.put(1)

    def _poll_loop(self) -> None:
        last = 0.0
        while self.running:
            due = 3 if self.fast_poll else 15
            try:
                forced = False
                while not self._pollq.empty():
                    self._pollq.get_nowait()
                    forced = True
                if forced or time.time() - last >= due:
                    snapshot = self.snapshot()
                    if not self.running:
                        break
                    last = time.time()
                    self.events.put(("snap", snapshot))
                    self._check_pause()
            except Exception as e:
                log.event("error", f"poll: {e}")
                if self.running:
                    snapshot = normalize_status({**self.snap, "state": "UNKNOWN", "fresh": False,
                                                  "busy": self.busy_label()})
                    self.events.put(("snap", snapshot))
                last = time.time()
            time.sleep(0.5)

    def _autostart_enabled(self) -> bool:
        val, t = self._autostart
        if time.time() - t > 10:
            val = autostart.is_enabled()
            self._autostart = (val, time.time())
        return val

    def snapshot(self) -> dict:
        r = self.relay
        # Copy ownership atomically; never reload underneath a relay operation.
        if not r.lock.acquire(blocking=False):
            return normalize_status({**self.snap, "busy": self.busy_label()})
        try:
            r.reload()
            rec = deepcopy(r.record)
        finally:
            r.lock.release()
        s, _ = settings_mod.load()
        snap = {
            "state": rec.state, "saved_state": rec.state, "fresh": True, "busy": self.busy_label(), "ssid": s["hotspot"]["ssid"],
            "band": rec.band or s["hotspot"]["band"], "security": s["hotspot"]["security"],
            "protection": rec.protection or s["vpn"]["protection"], "dns_lock": rec.dns_lock,
            "tunnel": rec.tunnel_name, "profile": rec.profile or s["vpn"].get("default_profile", ""),
            "ready_since": rec.ready_since, "last_error": rec.last_error,
            "paused_until": self.tray_state.get("pause_until") or 0,
            "autostart": self._autostart_enabled(), "settings": s,
            "clients": [], "clients_full": [], "rx": 0, "tx": 0, "handshake": None, "hotspot_state": "",
            "endpoint": "", "uplink": self.snap.get("uplink", ""),
        }
        profs, bad = profiles.list_profiles(paths.VPN_DIR)
        snap["profile_objs"] = profs
        snap["bad_profiles"] = bad
        snap["profiles"] = [(p.path.name, p.label) for p in profs]
        prof = next((p for p in profs if p.path.name == snap["profile"]), profs[0] if profs else None)
        snap["profile_label"] = prof.label if prof else "(no profile)"
        if rec.tunnel_name:
            if self.wg is None:
                self.wg = wireguard.find_wg(wireguard.find_wireguard(s["vpn"].get("wireguard_path", "")))
            if self.wg:
                st, _ = wireguard.show(self.wg, rec.tunnel_name)
                if st and st.peers:
                    p = st.peers[0]
                    snap["rx"], snap["tx"], snap["endpoint"] = p.rx, p.tx, p.endpoint
                    snap["handshake"] = p.handshake_age()
            if rec.state in (State.READY.value, State.VPN_CONNECTED.value, State.HOTSPOT_ACTIVE.value, State.SHARING_CONFIGURING.value):
                data = self.backend.guard_tick(rec.tunnel_name, rec.hotspot_guid)
                if not data.get("ok"):
                    log.event("error", "status verification failed: " + str(data.get("error", "unavailable")))
                    snap.update(state="UNKNOWN", fresh=False)
                elif data.get("tunnel_state") != "Running":
                    snap.update(state=State.ERROR.value, last_error="VPN tunnel is no longer running")
                elif rec.state == State.READY.value and data.get("hotspot_state") != "On":
                    snap["state"] = State.VPN_CONNECTED.value if data.get("hotspot_state") == "Off" else "UNKNOWN"
                snap["hotspot_state"] = data.get("hotspot_state", "")
                cl = clients_mod.merge(data)
                self._apply_admission(snap, cl, s)
        elif rec.state not in (State.DISCONNECTED.value, State.ERROR.value):
            snap.update(state="UNKNOWN", fresh=False)
        if time.time() - self.snap.get("uplink_t", 0) > 60:
            ups = self.backend.uplink()
            from .netid import uplink_kind

            if ups:
                kind, name = uplink_kind(ups[0]), ups[0]["name"]
                snap["uplink"] = kind if name.lower() == kind.lower() else f"{kind} · {name}"
            else:
                snap["uplink"] = "none - not connected"
            snap["uplink_t"] = time.time()
        else:
            snap["uplink_t"] = self.snap.get("uplink_t", 0)
        if snap["state"] in (State.READY.value, State.VPN_CONNECTED.value) and time.time() - self.exit_ip[1] > 180:
            self.exit_ip = (self.backend.public_ip(), time.time())
        snap["exit_ip"] = self.exit_ip[0] if snap["state"] in (State.READY.value, State.VPN_CONNECTED.value) else ""
        return normalize_status(snap)

    @staticmethod
    def _apply_admission(snap: dict, cl: list, s: dict) -> None:
        """Split hotspot clients into approved / waiting / blocked (device approval)."""
        store = admission.load()
        on = s["behavior"].get("approve_devices", True)
        seen = {c.mac for c in cl}
        for mac, p in list(store["pending"].items()) + list(store["blocked"].items()):
            # a held device drops out of the ARP cache; keep showing it while it is recent
            if mac not in seen and p.get("ips") and time.time() - float(p.get("since", 0)) < 1800:
                cl.append(clients_mod.Client(mac=mac, ip=p["ips"][-1]))
                seen.add(mac)
        for c in cl:
            c.access = admission.status_of(c.mac, store) if on else "approved"
            if c.access != "approved" and not c.ip:
                ips = (store["pending"].get(c.mac) or store["blocked"].get(c.mac) or {}).get("ips") or []
                c.ip = ips[-1] if ips else ""
        snap["clients_full"] = cl
        snap["clients"] = [(c.ip, c.display_name, c.device) for c in cl if c.access == "approved"]
        snap["pending"] = [{"mac": c.mac, "ip": c.ip, "name": c.display_name, "device": c.device}
                           for c in cl if c.access == "pending"]
        snap["blocked_n"] = sum(1 for c in cl if c.access == "blocked")

    def _check_pause(self) -> None:
        p = sync.read_pause()
        until = p.get("pause_until")
        if until and time.time() >= until:
            sync.clear_pause(silent=True)
            self.relay.reload()
            if self.relay.m.state == State.DISCONNECTED:
                self.events.put(("action", "golive", p.get("pause_profile") or None))

    # -------------------------------------------------------------- actions
    INSTANT = ("copy_password", "open_cli", "open_logs", "open_vpn_folder", "open_data", "open_settings_file",
               "toggle_autostart", "toggle_autoconnect", "cancel_pause")

    def do(self, action: str, arg=None) -> None:
        """Call from the Tk thread."""
        if action in self.INSTANT:
            return getattr(self, action)()
        if action == "speed_test" and (not self.snap.get("fresh") or
                                       self.snap.get("state") not in (State.READY.value, State.VPN_CONNECTED.value)):
            self.events.put(("toast", "Connect the VPN first",
                             "Speed test measures the laptop's current connection while VPN is connected.", True))
            return
        if self.busy:
            self.events.put(("toast", "Busy", f"Still working on: {self.busy}", True))
            return
        remote = sync.remote_op()
        if remote and action not in ("check_ip", "doctor"):
            self.events.put(("toast", "Busy", f"{sync.describe(remote)}. Try again when it finishes.", True))
            return
        self.busy = ACTION_LABELS.get(action, action)
        self.snap = normalize_status({**self.snap, "busy": self.busy})
        self.events.put(("snap",))
        threading.Thread(target=self._worker, args=(action, arg), daemon=True).start()

    def _worker(self, action: str, arg) -> None:
        mark = self.activity.mark()
        ok, title, msg, err = True, "", "", False
        try:
            s, _ = settings_mod.load()
            r = self.relay
            if action == "golive":
                ok = r.start(s, arg or None)
                title = "WireSpot is live" if ok else "Could not go live"
            elif action == "reconnect":
                token = r.record.profile or None
                r.stop(s)
                ok = r.start(s, token)
                title = "Reconnected" if ok else "Reconnect failed"
            elif action == "disconnect":
                sync.clear_pause(silent=True)
                ok = r.stop(s)
                title = "Disconnected" if ok else "Disconnect incomplete"
                msg = "VPN and hotspot are off." if ok else ""
            elif action == "pause":
                minutes = int(arg)
                profile = r.record.profile or s["vpn"].get("default_profile", "")
                ok = r.stop(s)
                if ok:
                    sync.set_pause(time.time() + minutes * 60, profile)
                title = f"Paused for {minutes} min" if ok else "Pause failed"
                msg = "WireSpot reconnects automatically." if ok else ""
            elif action == "hotspot_on":
                ok = r.hotspot_start(s)
                title = "Hotspot on" if ok else "Hotspot failed"
            elif action == "hotspot_off":
                ok = r.hotspot_stop(s)
                title = "Hotspot off, VPN still connected" if ok else "Could not stop the hotspot"
            elif action == "check_ip":
                ip = self.backend.public_ip()
                self.exit_ip = (ip, time.time())
                title, msg, ok = "Exit IP", ip or "unavailable (no internet?)", bool(ip)
            elif action == "profile_probe":
                name = str(arg or "")
                if not name or Path(name).name != name or not name.lower().endswith(".conf"):
                    raise ValueError("Choose a profile from the list")
                profile = profiles.load_profile(paths.VPN_DIR / name)
                active = name == self.snap.get("profile") and self.snap.get("state") in (
                    State.READY.value, State.VPN_CONNECTED.value)
                check = network_tests.profile_health(profile.endpoint, active=active,
                                                       handshake_age=self.snap.get("handshake") if active else None)
                self.profile_checks[name] = check
                self.events.put(("measurement", "profile", name))
                title, msg, ok = check["title"], check["detail"], check["level"] != "bad"
            elif action == "speed_test":
                self.speed_result = {"phase": "Starting", "result": None}
                self.events.put(("measurement", "speed", self.speed_result))

                def progress(phase):
                    self.speed_result = {"phase": phase, "result": None}
                    self.events.put(("measurement", "speed", self.speed_result))

                result = network_tests.speed_test(progress)
                self.speed_result = {"phase": "Complete", "result": result}
                self.events.put(("measurement", "speed", self.speed_result))
                title, msg = "Speed test complete", (
                    f"{result['download_mbps']} Mbps down · {result['upload_mbps']} Mbps up · "
                    f"{result['latency_ms']:.0f} ms latency")
            elif action == "uninstall":
                from . import installer

                ok, msg = installer.uninstall(keep_data=bool(arg), relay=r, settings=s)
                title = "WireSpot was uninstalled" if ok else "Uninstall incomplete"
                if ok:
                    self.events.put(("uninstalled", msg))
                    return
            elif action == "doctor":
                section = arg or "quick"
                ui.capture_begin()
                try:
                    doctor.run(self.backend, s, [section], relay=r)
                finally:
                    lines = ui.capture_end()
                self.events.put(("report", section, lines))
                return
        except Exception as e:
            ok, title, msg = False, "Unexpected error", f"{type(e).__name__}: {e}"
            if action == "speed_test":
                self.speed_result = {"phase": "Unavailable", "error": str(e), "result": None}
                self.events.put(("measurement", "speed", self.speed_result))
            log.event("error", f"action {action}: {e}")
        finally:
            self.busy = None
            self.refresh_now()
        if not ok and not msg:
            errors = [x for x in self.activity.since(mark) if x.lstrip().startswith(("✖", "×"))]
            msg = errors[-1].lstrip("✖× ").strip() if errors else "See Diagnostics or Activity for details."
            err = True
        if ok and not msg and action in ("golive", "reconnect", "hotspot_on"):
            msg = f"{self.snap.get('ssid')} is broadcasting through the VPN."
        self.events.put(("toast", title, msg, err or not ok))

    def copy_password(self) -> None:
        s, _ = settings_mod.load()
        self.events.put(("clipboard", s["hotspot"]["password"]))
        self.events.put(("toast", "Copied", f"Wi-Fi password for {s['hotspot']['ssid']} is on the clipboard.", False))

    def open_cli(self) -> None:
        if getattr(sys, "frozen", False):
            cmd = [str(paths.exe("WireSpotCLI.exe"))]
        else:
            py = Path(sys.executable).with_name("python.exe")
            cmd = [str(py if py.exists() else sys.executable), str(paths.APP_DIR / "app.py")]
        try:
            subprocess.Popen(cmd, cwd=str(paths.APP_DIR), creationflags=subprocess.CREATE_NEW_CONSOLE)
        except OSError as e:
            self.events.put(("toast", "Could not open the CLI", str(e), True))

    def _open(self, target: Path, what: str) -> None:
        try:
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))
        except OSError as e:
            self.events.put(("toast", f"Could not open the {what}", str(e), True))

    def open_logs(self) -> None:
        self._open(paths.LOG_DIR, "logs folder")

    def open_vpn_folder(self) -> None:
        self._open(paths.VPN_DIR, "VPN folder")

    def open_data(self) -> None:
        self._open(paths.BASE, "data folder")

    def open_settings_file(self) -> None:
        if not paths.SETTINGS_PATH.exists():
            settings_mod.save(settings_mod.load()[0])
        try:
            subprocess.Popen(["notepad.exe", str(paths.SETTINGS_PATH)])
        except OSError as e:
            self.events.put(("toast", "Could not open settings.json", str(e), True))

    # -------------------------------------------------------------- device approval
    def device_verdict(self, mac: str, verdict: str, name: str = "") -> None:
        """approve / block / forget a hotspot device; takes effect within a second."""
        admission.decide(mac, verdict, name)
        if verdict == "forget":
            self.toasted_pending.discard(mac)
        guid = self.relay.record.hotspot_guid
        if guid:
            from . import neighbors

            def now():
                idx = neighbors.ifindex_for_guid(guid)
                if idx:
                    admission.enforce(idx)
            threading.Thread(target=now, daemon=True).start()
        word = {"approve": "Allowed", "block": "Blocked", "forget": "Forgotten"}[verdict]
        ui.info(f"Device {word.lower()}: {name or mac}")
        self.events.put(("toast", f"{word}: {name or mac}",
                         {"approve": "It can use the hotspot now.",
                          "block": "It stays on the Wi-Fi but gets no network.",
                          "forget": "It will be asked about the next time it connects."}[verdict], False))
        self.refresh_now()

    def toggle_autostart(self) -> None:
        if autostart.is_enabled():
            ok, msg = autostart.disable()
            self._autostart = (not ok, time.time())
            self.events.put(("toast", "Start with Windows: off" if ok else "Could not change autostart",
                             "" if ok else msg, not ok))
        else:
            ok, msg = autostart.enable()
            self._autostart = (ok, time.time())
            self.events.put(("toast", "Start with Windows: on" if ok else "Could not enable autostart",
                             "WireSpot starts in the tray at logon, elevated, without a UAC prompt." if ok else msg, not ok))
        self.refresh_now()

    def toggle_autoconnect(self) -> None:
        self.set_behavior("autoconnect", not self.snap.get("settings", {}).get("behavior", {}).get("autoconnect", False))

    def toggle_approval(self) -> None:
        """Device approval on/off. Turning it on while live trusts the devices already connected
        (so your own phone is not cut off); everyone after that has to be allowed."""
        on = not self.snap.get("settings", {}).get("behavior", {}).get("approve_devices", True)
        if on:
            macs = [c.mac for c in self.snap.get("clients_full") or []]
            n = admission.approve_all_connected(macs)
            self.set_behavior("approve_devices", True)
            if self.relay.m.state == State.READY:
                self.relay.start_gate()
            self.events.put(("toast", "Device approval on",
                             (f"The {n} device{'s' if n != 1 else ''} already connected stay{'s' if n == 1 else ''} allowed. "
                              if n else "") + "New devices wait for you to allow them.", False))
        else:
            self.set_behavior("approve_devices", False)
            self.events.put(("toast", "Device approval off", "Anyone with the Wi-Fi password gets the VPN.", False))

    def set_behavior(self, key: str, value) -> None:
        s, _ = settings_mod.load()
        s["behavior"][key] = value
        settings_mod.save(s)
        if key == "debug":
            log.enable() if value else log.disable()
        if key == "watch_downloads":
            self.inbox.start() if value else self.inbox.stop()
        self.refresh_now()

    def cancel_pause(self) -> None:
        sync.clear_pause()
        self.events.put(("toast", "Auto-resume cancelled", "WireSpot stays disconnected.", False))
        self.refresh_now()

    # -------------------------------------------------------------- profiles
    def import_conf(self, path: Path, move: bool) -> tuple[bool, str]:
        cand = Candidate(path, "import", "")
        info = self.inbox.inspect(cand)
        if info.error:
            return False, info.error
        if info.duplicate:
            return False, f"already imported as {info.duplicate.name}"
        dest = self.inbox.import_file(path, move=move)
        s, _ = settings_mod.load()
        if not s["vpn"].get("default_profile"):
            s["vpn"]["default_profile"] = dest.name
            settings_mod.save(s)
        self.refresh_now()
        return True, dest.name

    def set_default_profile(self, name: str) -> None:
        s, _ = settings_mod.load()
        s["vpn"]["default_profile"] = name
        settings_mod.save(s)
        self.refresh_now()
