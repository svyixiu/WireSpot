"""WireSpot interactive shell and command dispatch."""
from __future__ import annotations

import difflib
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import APP_NAME, LEGACY_TUNNEL_PREFIXES, TAGLINE, VERSION
from . import admission, doctor, hotspot as hs_mod, log, paths, profiles, sync, ui, wlan
from . import settings as settings_mod
from .backend import Backend
from .inbox import Inbox, path_from_input, Candidate
from .lineedit import LineEditor, Suggestion
from .relay import Relay, StartOptions
from .state import State
from .ui import Choice


# ====================================================================== registry

@dataclass
class Command:
    name: str
    handler: Callable
    usage: str
    summary: str
    group: str
    details: str = ""
    aliases: tuple[str, ...] = ()
    subs: tuple[str, ...] = ()


COMMANDS: dict[str, Command] = {}
ALIASES: dict[str, str] = {}
GROUPS = ("Everyday", "Profiles", "VPN", "Hotspot", "Diagnostics", "Settings", "Shell")


def command(name, usage, summary, group, details="", aliases=(), subs=()):
    def deco(fn):
        COMMANDS[name] = Command(name, fn, usage, summary, group, details, tuple(aliases), tuple(subs))
        for a in aliases:
            ALIASES[a] = name
        return fn
    return deco


# ====================================================================== completion data

SUB_DESC = {
    "hotspot": {"start": "Share the connected VPN over Wi-Fi", "stop": "Stop the hotspot, keep the VPN",
                "status": "Tethering state, bands, clients"},
    "vpn": {"status": "Tunnel, hotspot and exit IP", "connect": "Connect only the WireGuard tunnel",
            "disconnect": "Disconnect the tunnel"},
    "network": {"status": "Routes, DNS servers, public IP", "adapters": "Every adapter with GUID / ifIndex / kind"},
    "doctor": {"quick": "Checklist (default)", "os": "Windows, admin, PowerShell, key-file ACLs",
               "wifi": "Radio, channel, driver, Wi-Fi Direct adapters", "vpn": "Tunnel services, handshake, routes",
               "hotspot": "Tethering capability, bands, state", "ics": "Sharing/NAT and classic-ICS conflicts",
               "network": "Routing table, DNS, exit IP", "clients": "Devices on the hotspot",
               "full": "Everything above", "save": "Also write a report to logs\\"},
    "profile": {"list": "List profiles", "use": "Set the default profile", "info": "Show a profile card"},
    "inbox": {"scan": "Also look at .conf files downloaded earlier"},
    "show": {"password": "Reveal the hotspot password"},
    "debug": {"on": "Write logs\\wirespot-YYYY-MM-DD.log", "off": "Stop logging"},
    "autostart": {"on": "Start the tray at logon", "off": "Remove the logon task", "status": "Show autostart state"},
}
SET_KEYS = {
    "ssid": "Hotspot network name", "password": "8-63 chars; omit the value for a hidden prompt",
    "security": "wpa2 · transition · wpa3", "band": "auto · 2.4 · 5 · 6", "protection": "balanced · strict",
    "debug": "Diagnostic logging", "guard": "Fail-closed watchdog while live", "dns-lock": "Force DNS through the tunnel",
    "fallbacks": "Offer recovery choices on failure", "watch": "Listen for .conf files in Downloads",
    "autoconnect": "Tray goes live at logon",
    "approval": "New devices need 'allow' before they get network",
    "wireguard-path": "Custom wireguard.exe location",
}
ON_OFF = [("on", "Enable"), ("off", "Disable")]
SET_VALUES = {
    "security": [("wpa2", "Widest compatibility"), ("transition", "WPA3 with WPA2 fallback (24H2+)"),
                 ("wpa3", "WPA3 only (24H2+)")],
    "band": [("auto", "Driver chooses - safest"), ("2.4", "Range and compatibility"), ("5", "Speed"),
             ("6", "Wi-Fi 6E adapters only")],
    "protection": [("balanced", "Full tunnel, hotspot-compatible"), ("strict", "Kill-switch; blocks hotspot DHCP")],
    "debug": ON_OFF, "guard": ON_OFF, "dns-lock": ON_OFF, "fallbacks": ON_OFF, "watch": ON_OFF, "autoconnect": ON_OFF,
    "approval": ON_OFF,
}
START_OPTS = [("--band", "One-off band: auto · 2.4 · 5 · 6"), ("--vpn-only", "Connect without the hotspot"),
              ("--balanced", "One-off balanced mode"), ("--strict", "One-off strict mode"),
              ("--yes", "Accept every recommended answer")]


# ====================================================================== app

class App:
    def __init__(self):
        self.settings, notes = settings_mod.load()
        for n in notes:
            ui.warn(n)
        self.backend = Backend()
        self.relay = Relay(self.backend, decide=self.decide, save_settings=self.save)
        self.inbox = Inbox()
        self.inbox.on_new = self._on_new_conf
        self.editor = LineEditor(self.complete, paths.HISTORY_PATH)
        self.editor.runnable = self.is_runnable
        self.running = True
        self._pcache: tuple[float, list] = (0.0, [])
        # sync with the WireSpot app (sync.py): notices + live status line
        self.notices: list[str] = []
        self.status_dirty = False
        self.seen_pending: set[str] = set(admission.load()["pending"])
        self.bus_rev = sync.read_bus().get("rev", 0)
        self.watcher = sync.Watcher(self._on_shared_change)
        self.editor.idle = self._idle

    # -------------------------------------------------------------- sync with the app
    def _on_shared_change(self, changed) -> None:
        """Background thread: the app (or another CLI) changed shared state."""
        names = {p.name for p in changed}
        if "bus.json" in names:
            bus = sync.read_bus()
            last = bus.get("last") or {}
            if bus.get("rev", 0) != self.bus_rev and last and last.get("pid") != os.getpid():
                who = sync.SOURCE_LABEL.get(last.get("who"), "WireSpot")
                ok = last.get("ok", True)
                msg = f"{who}: {last.get('label', 'done')}" + ("" if ok else " - failed")
                if last.get("message"):
                    msg += f" ({last['message']})"
                self.notices.append(("ok" if ok else "warn", msg))
            op = sync.remote_op(bus)
            if op:
                self.notices.append(("info", f"{sync.describe(op)}…"))
            self.bus_rev = bus.get("rev", 0)
        if "devices.json" in names:
            store = admission.load()
            for i, (mac, p) in enumerate(sorted(store["pending"].items(), key=lambda kv: kv[1].get("since", 0)), 1):
                if mac not in self.seen_pending:
                    self.seen_pending.add(mac)
                    from .clients import guess_type

                    ip = (p.get("ips") or ["no IP yet"])[-1]
                    self.notices.append(("claude", f"New device waiting for approval: {guess_type([], mac)} · {ip} · {mac}"
                                                   f"  -> 'allow {i}' or 'block {i}'"))
        self.status_dirty = True

    def _idle(self) -> None:
        """Main thread, while the prompt waits for keys: print notices, refresh the status line."""
        while self.notices:
            kind, msg = self.notices.pop(0)
            {"ok": ui.ok, "warn": ui.warn, "claude": ui.info, "info": ui.info}[kind](msg)
        if self.status_dirty:
            self.status_dirty = False
            text = self.status_line()
            if text != self.editor.status:
                with ui._print_lock:
                    self.editor.status = text
                    self.editor.redraw()
            self.relay.reload()
            from .state import State as _S

            if self.relay.m.state == _S.READY and self.relay.guard is None and self.relay.gate is None:
                self.relay.start_guard(self.settings)     # standby: takes over if the app quits

    # -------------------------------------------------------------- helpers
    def save(self, s: dict | None = None) -> None:
        settings_mod.save(s or self.settings)

    def decide(self, question, choices, default=0, cancel=None):
        if not self.settings["behavior"].get("offer_fallbacks", True) and any(c.key == "stop" for c in choices):
            ui.info(f"{question} -> stop (offer_fallbacks is off)")
            return "stop"
        return ui.choose(question, choices, default, cancel)

    def profiles(self):
        return profiles.list_profiles(paths.VPN_DIR)

    def _on_new_conf(self, cand: Candidate) -> None:
        ui.info(f"New WireGuard config in {cand.origin}: {ui.color(cand.path.name, ui.C.bold)}  "
                f"{ui.color('press Enter to review', ui.C.muted)}")

    def review_pending(self) -> bool:
        items = self.inbox.take_pending()
        for c in items:
            self.inbox.review(c, self.decide, self.settings, self.save)
        return bool(items)

    # -------------------------------------------------------------- completion
    def _profiles_cached(self):
        t, profs = self._pcache
        if time.monotonic() - t > 3:
            profs = self.profiles()[0]
            self._pcache = (time.monotonic(), profs)
        return profs

    def complete(self, text: str) -> list[Suggestion]:
        """Context-aware suggestions for the autocomplete menu."""
        parts = text.split(" ")
        cur = parts[-1].lower()
        if len(parts) == 1:
            return [Suggestion(c.name + " ", c.usage, c.summary) for c in COMMANDS.values()
                    if c.name.startswith(cur) or any(a.startswith(cur) for a in c.aliases)]
        cmd = ALIASES.get(parts[0].lower(), parts[0].lower())
        head = " ".join(parts[:-1])
        args = [x.lower() for x in parts[1:-1]]
        n = len(parts) - 1
        profs = [(str(i), p.label) for i, p in enumerate(self._profiles_cached(), 1)]
        opts: list[tuple[str, str]] = []
        if cmd == "start":
            if args and args[-1] == "--band":
                opts = SET_VALUES["band"]
            else:
                opts = ([] if any(x.isdigit() for x in args) else profs) + [o for o in START_OPTS if o[0] not in args]
        elif cmd in ("connect", "use") and n == 1:
            opts = profs
        elif cmd == "profile":
            if n == 1:
                opts = list(SUB_DESC["profile"].items()) + profs
            elif n == 2 and args[0] in ("use", "info"):
                opts = profs
        elif cmd == "vpn":
            if n == 1:
                opts = list(SUB_DESC["vpn"].items())
            elif n == 2 and args[0] == "connect":
                opts = profs
        elif cmd == "set":
            if n == 1:
                opts = list(SET_KEYS.items())
            elif n == 2:
                opts = SET_VALUES.get(args[0], [])
        elif cmd == "help" and n == 1:
            opts = [(c.name, c.summary) for c in COMMANDS.values()]
        elif cmd == "doctor":
            opts = [(k, v) for k, v in SUB_DESC["doctor"].items() if k not in args]
        elif cmd == "import":
            from .inbox import downloads_dir

            try:
                recent = sorted(downloads_dir().glob("*.conf"), key=lambda q: q.stat().st_mtime, reverse=True)[:6]
            except OSError:
                recent = []
            return [Suggestion(f'import "{q}" ', q.name, f"Downloads · {time.strftime('%d %b %H:%M', time.localtime(q.stat().st_mtime))}")
                    for q in recent if q.name.lower().startswith(cur.strip('"'))]
        elif cmd in SUB_DESC and n == 1:
            opts = list(SUB_DESC[cmd].items())
        return [Suggestion(f"{head} {tok} ", tok, desc) for tok, desc in opts if tok.lower().startswith(cur)]

    def is_runnable(self, text: str) -> bool:
        first = text.strip().split(" ")[0].lower()
        return bool(first) and (first in COMMANDS or first in ALIASES or path_from_input(text) is not None)

    # -------------------------------------------------------------- prompt
    def status_line(self) -> str:
        from .state import RelayRecord

        rec = RelayRecord.load(self.relay.state_path)        # read-only: never swaps the relay's record
        st = State(rec.state)
        g = ui.glyphs()
        op = sync.remote_op()
        if op:
            return ui.color(f"{g.dot} {sync.describe(op)}…", ui.C.shimmer)
        pause = sync.read_pause()
        if pause and st == State.DISCONNECTED:
            left = max(0, int(pause["pause_until"] - time.time()))
            return ui.color(f"{g.dot} paused", ui.C.shimmer) + ui.color(
                f" · resumes in {left // 60}m {left % 60:02d}s (the app resumes it) · 'start' or 'stop' cancels", ui.C.muted)
        if st == State.READY:
            text = ui.color(f"{g.dot} live", ui.C.success) + ui.color(f" · {self.settings['hotspot']['ssid']} via {rec.tunnel_name}", ui.C.muted)
        elif st == State.VPN_CONNECTED:
            text = ui.color(f"{g.dot} vpn connected", ui.C.shimmer) + ui.color(f" · {rec.tunnel_name}", ui.C.muted)
        elif st == State.ERROR:
            text = ui.color(f"{g.fail} needs attention", ui.C.error) + ui.color(" · run status", ui.C.muted)
        elif st == State.DISCONNECTED:
            text = ui.color(f"{g.dot} idle", ui.C.muted)
        else:
            text = ui.color(f"{g.dot} {st.value.lower()}", ui.C.warning)
        waiting = self.inbox.pending.qsize()
        if waiting:
            text += ui.color(f" · {waiting} new .conf - press enter", ui.C.claude)
        held = len(admission.load()["pending"]) if st == State.READY else 0
        if held:
            text += ui.color(f" · {held} device{'s' if held != 1 else ''} waiting - 'allow'", ui.C.claude)
        return text

    # -------------------------------------------------------------- loop
    def loop(self) -> None:
        self.watcher.start()
        while self.running:
            ui.set_title(APP_NAME)
            try:
                raw = self.editor.read("wirespot> ", self.status_line())
            except KeyboardInterrupt:
                ui.hint("(Ctrl+C) type 'exit' to quit")
                continue
            except EOFError:
                raw = "exit"
            line = raw.strip()
            if not line:
                self.review_pending()
                continue
            self.editor.remember(line)
            self.dispatch_line(line)

    def dispatch_line(self, line: str) -> None:
        self.settings, _ = settings_mod.load()
        if not self.relay.lock._is_owned():
            self.relay.reload()
        dropped = path_from_input(line)
        if dropped:
            self.inbox.review(Candidate(dropped, "drag & drop", ""), self.decide, self.settings, self.save)
            return
        try:
            args = shlex.split(line, posix=False)
            args = [a[1:-1] if len(a) >= 2 and a[0] == a[-1] == '"' else a for a in args]
        except ValueError as e:
            ui.err(str(e))
            return
        name = args[0].lower()
        name = ALIASES.get(name, name)
        cmd = COMMANDS.get(name)
        if not cmd:
            close = difflib.get_close_matches(name, list(COMMANDS) + list(ALIASES), n=3, cutoff=0.6)
            ui.err(f"Unknown command '{args[0]}'." + (f" Did you mean: {', '.join(close)}?" if close else ""))
            ui.hint("Type 'help' for all commands.")
            return
        t0 = time.monotonic()
        secret = name == "set" and len(args) > 1 and args[1].lower() == "password"
        log.event("info", ">>> set password <hidden>" if secret else f">>> {line}")
        try:
            cmd.handler(self, args[1:])
        except KeyboardInterrupt:
            ui.blank()
            ui.warn("Interrupted.")
        except Exception as e:
            log.event("error", f"command {name} crashed: {type(e).__name__}: {e}")
            ui.err(f"{type(e).__name__}: {e}")
            ui.hint("Turn on 'set debug on' and retry to capture a log for this.")
        finally:
            log.debug(f"<<< {name} {time.monotonic() - t0:.1f}s")
        self.review_pending()


# ====================================================================== commands

@command("help", "help [command]", "Show commands, or details for one command", "Shell", aliases=("?",))
def cmd_help(app: App, args):
    if args:
        name = ALIASES.get(args[0].lower(), args[0].lower())
        c = COMMANDS.get(name)
        if not c:
            ui.err(f"No command '{args[0]}'.")
            return
        ui.heading(c.usage)
        ui.detail(c.summary)
        if c.details:
            for line in c.details.strip().splitlines():
                ui.detail(line)
        if c.aliases:
            ui.detail("aliases: " + ", ".join(c.aliases))
        return
    ui.heading(f"{APP_NAME} commands")
    for g in GROUPS:
        items = [c for c in COMMANDS.values() if c.group == g]
        if not items:
            continue
        ui.blank()
        ui._emit("  " + ui.color(g, ui.C.claude + ui.C.bold))
        for c in items:
            ui._emit(f"    {c.usage.ljust(34)} {ui.color(c.summary, ui.C.muted)}")
    ui.blank()
    ui.hint("Start typing for suggestions · tab accepts · 'help <command>' for details · drop a .conf on this window to import it")


@command("start", "start [profile] [options]", "Connect VPN, start the hotspot, share VPN only", "Everyday",
         details="""
Runs the full pipeline with verification and rollback:
  preflight → WireGuard → verify tunnel → hotspot → identify interface → ICS → verify NAT/DNS/path
Options:
  --band auto|2.4|5|6     one-off band for this run
  --balanced / --strict   one-off protection mode
  --vpn-only              connect the VPN without the hotspot
  --yes                   accept the recommended answer at every prompt""", subs=())
def cmd_start(app: App, args):
    opts, token = _start_opts(args)
    if opts is None:
        return
    app.relay.start(app.settings, token, opts)
    ui.AUTO_YES = False


def _start_opts(args):
    opts, token = StartOptions(), None
    it = iter(args)
    for a in it:
        al = a.lower()
        if al == "--band":
            opts.band = settings_mod.normalize_band(next(it, "auto"))
            if opts.band not in settings_mod.BANDS:
                ui.err("--band must be auto, 2.4, 5 or 6")
                return None, None
        elif al == "--vpn-only":
            opts.vpn_only = True
        elif al in ("--balanced", "--strict"):
            opts.protection = al[2:]
        elif al in ("--yes", "-y"):
            ui.AUTO_YES = True
        elif al.startswith("--"):
            ui.err(f"Unknown option {a}. See 'help start'.")
            return None, None
        else:
            token = a
    return opts, token


@command("stop", "stop", "Stop hotspot, remove sharing/DNS lock, disconnect VPN", "Everyday")
def cmd_stop(app: App, args):
    app.relay.stop(app.settings)


@command("status", "status", "Show VPN, hotspot, sharing and devices at a glance", "Everyday", aliases=("st",))
def cmd_status(app: App, args):
    r, b = app.relay, app.backend
    rec = r.record
    ui.heading("Status")
    ui.kv("State", _state_text(r.m.state) + (f" · {rec.last_error}" if rec.last_error and r.m.state == State.ERROR else ""))
    with ui.task("Reading tunnel and hotspot state"):
        svcs, _ = b.wg_services()
        data, e = b.hotspot_status(rec.tunnel_guid)
    ours = [x for x in svcs if x.ours]
    if rec.provider == "nordvpn" or app.settings["behavior"].get("profile_less"):
        from .nordvpn import NordVPNProvider
        status = NordVPNProvider(b).detect(validate=False)
        ui.kv("NordVPN", status.reason)
        if status.adapter:
            ui.kv("Adapter", f"{status.adapter.name} · {status.protocol}")
        if rec.forward_guard:
            ui.kv("Forward guard", "active")
    if ours:
        env = b.env(app.settings["vpn"].get("wireguard_path", ""))
        for x in ours:
            mine = x.name == rec.tunnel_name and rec.profile
            ui.kv("VPN", f"{x.name} · {x.state}" + (f" · profile {rec.profile} · {rec.protection}" if mine else ""))
            if x.running and env.wg:
                from .wireguard import fmt_age, fmt_bytes, show

                st, _ = show(env.wg, x.name)
                for p in (st.peers if st else []):
                    ui.detail(f"{p.endpoint} · handshake {fmt_age(p.handshake_age())} · ↓ {fmt_bytes(p.rx)} ↑ {fmt_bytes(p.tx)}")
    elif not (rec.provider == "nordvpn" or app.settings["behavior"].get("profile_less")):
        ui.kv("VPN", "disconnected")
    if data:
        h = app.settings["hotspot"]
        ui.kv("Hotspot", f"{data.get('state')} · {h['ssid']} · {data.get('clients', 0)}/{data.get('max_clients', '?')} devices"
              + (f" · source {(data.get('source') or {}).get('name', '')}" if data.get("state") == "On" else ""))
    else:
        ui.kv("Hotspot", f"unknown ({e})")
    if rec.hotspot_guid and data and data.get("state") == "On":
        doctor.show_clients(b, rec.hotspot_guid)
    with ui.task("Checking exit IP"):
        ip = b.public_ip()
    ui.kv("Public IP", ip or "unavailable")


def _state_text(st: State) -> str:
    colors = {State.READY: ui.C.claude, State.ERROR: ui.C.error, State.VPN_CONNECTED: ui.C.shimmer}
    return ui.color(st.value, colors.get(st, ui.C.warning if st != State.DISCONNECTED else ui.C.muted))


@command("clients", "clients", "Devices on the hotspot: IP, name, device type", "Everyday", aliases=("devices", "who"))
def cmd_clients(app: App, args):
    guid = app.relay.record.hotspot_guid
    if not guid:
        from .netid import locate_hotspot

        with ui.task("Finding the hotspot adapter"):
            m = locate_hotspot(app.backend.inventory(with_ics=False))
        guid = m.adapter.guid if m.adapter else ""
    doctor.show_clients(app.backend, guid)


# ---------------------------------------------------------------- profiles
@command("profiles", "profiles", "List WireGuard profiles in .\\vpn", "Profiles")
def cmd_profiles(app: App, args):
    profs, bad = app.profiles()
    if not profs and not bad:
        ui.warn(f"No .conf files in {paths.VPN_DIR}")
        ui.hint("Drop a Proton WireGuard .conf onto this window, save it to Downloads, or 'import <path>'.")
        return
    default = app.settings["vpn"].get("default_profile", "")
    for i, p in enumerate(profs, 1):
        star = ui.color(ui.glyphs().dot, ui.C.claude) if p.path.name == default else " "
        extra = " · free" if p.server.free else ""
        ui._emit(f"  {star} {ui.color(f'{i:>2}', ui.C.accent)}  {p.label}{ui.color(extra, ui.C.muted)}")
        if p.features:
            ui._emit("        " + ui.color(" · ".join(p.features), ui.C.muted))
    for path, why in bad:
        ui.warn(f"{path.name}: {why}")
    ui.hint(f"{ui.glyphs().dot} = default · 'profile use <n>' · 'profile info <n>' · 'start <n>'")


@command("profile", "profile [list|use|info] <n|name>", "Choose or inspect a profile ('profile 1' = use 1)", "Profiles",
         subs=("list", "use", "info"))
def cmd_profile(app: App, args):
    if not args or args[0].lower() == "list":
        return cmd_profiles(app, [])
    sub = args[0].lower()
    if sub in ("use", "info"):
        if len(args) < 2:
            ui.err(f"Usage: profile {sub} <number|name>")
            return
        token = args[1]
    else:
        sub, token = "use", args[0]
    profs, _ = app.profiles()
    p = profiles.resolve_profile(token, profs)
    if not p:
        ui.err(f"No profile matches '{token}'.")
        return
    if sub == "use":
        app.settings["vpn"]["default_profile"] = p.path.name
        app.save()
        ui.ok(f"Default profile: {p.label}")
    else:
        from .inbox import Candidate as Cand

        app.inbox.card(p, Cand(p.path, "vpn folder", ""), title=f"Profile {p.server_name or p.path.stem}")


@command("use", "use <n|name>", "Set the default profile", "Profiles")
def cmd_use(app: App, args):
    if not args:
        ui.err("Usage: use <number|name>")
        return
    cmd_profile(app, ["use", args[0]])


@command("import", "import <path.conf>", "Review and import a WireGuard .conf file", "Profiles",
         details="You can also drag a .conf onto this window, or save it to your Downloads folder -\n"
                 "WireSpot notices it and shows a review card with Accept / Decline.")
def cmd_import(app: App, args):
    if not args:
        ui.err("Usage: import <path to .conf>")
        return
    p = Path(" ".join(args).strip('"'))
    if not p.is_file():
        ui.err(f"File not found: {p}")
        return
    app.inbox.review(Candidate(p, "import", ""), app.decide, app.settings, app.save)


@command("inbox", "inbox [scan]", "Show the .conf listener and review waiting files", "Profiles", subs=("scan",))
def cmd_inbox(app: App, args):
    state = "watching" if app.inbox.watching else "off ('set watch on')"
    ui.kv("Listener", f"{state} · {', '.join(str(d) for d in app.inbox.watch_dirs)}")
    if args and args[0].lower() == "scan":
        app.inbox._started = 0  # include older files once
        found = app.inbox.scan_once()
        app.inbox._started = time.time()
        ui.info(f"Found {len(found)} new config(s).")
    if not app.review_pending():
        ui.info("Nothing waiting for review.")


# ---------------------------------------------------------------- VPN
@command("vpn", "vpn status|connect [n]|disconnect", "VPN-only control", "VPN", subs=("status", "connect", "disconnect"))
def cmd_vpn(app: App, args):
    sub = args[0].lower() if args else "status"
    if sub == "connect":
        app.relay.start(app.settings, args[1] if len(args) > 1 else None, StartOptions(vpn_only=True))
    elif sub == "disconnect":
        cmd_disconnect(app, [])
    else:
        cmd_status(app, [])


@command("connect", "connect [n|name]", "Connect only the WireGuard tunnel", "VPN")
def cmd_connect(app: App, args):
    app.relay.start(app.settings, args[0] if args else None, StartOptions(vpn_only=True))


@command("disconnect", "disconnect", "Disconnect the WireSpot tunnel", "VPN")
def cmd_disconnect(app: App, args):
    r = app.relay
    if r.m.state in (State.READY, State.HOTSPOT_ACTIVE) or r.record.hotspot_started_by_us:
        ui.warn("The hotspot is sharing this VPN. Without it, devices would lose internet (or fall back unprotected).")
        pick = app.decide("Disconnect how?", [
            Choice("all", "Stop the hotspot too (full stop)"),
            Choice("cancel", "Cancel")], 0, cancel="cancel")
        if pick == "all":
            r.stop(app.settings)
        return
    with r.lock:
        r.disconnect_vpn(app.settings)


# ---------------------------------------------------------------- hotspot
@command("hotspot", "hotspot start|stop|status", "Mobile Hotspot control (shares the connected VPN)", "Hotspot",
         subs=("start", "stop", "status"))
def cmd_hotspot(app: App, args):
    sub = args[0].lower() if args else "status"
    if sub == "start":
        app.relay.hotspot_start(app.settings)
    elif sub == "stop":
        app.relay.hotspot_stop(app.settings)
    elif sub == "status":
        doctor.run(app.backend, app.settings, ["hotspot"], relay=app.relay)
    else:
        ui.err("Usage: hotspot start|stop|status")


@command("bind", "bind", "Re-source the hotspot from the VPN tunnel", "Hotspot",
         details="Use after turning Mobile Hotspot on in Windows Settings - that shares plain Wi-Fi.\n"
                 "bind restarts it with the WireGuard tunnel as the shared connection.")
def cmd_bind(app: App, args):
    app.relay.bind(app.settings)


@command("network", "network status|adapters", "Routes, DNS, public IP / adapter identities", "Diagnostics",
         subs=("status", "adapters"))
def cmd_network(app: App, args):
    sub = args[0].lower() if args else "status"
    if sub == "adapters":
        with ui.task("Collecting adapters"):
            inv = app.backend.inventory(with_ics=True)
        ui.heading("Network adapters (identity = GUID)")
        rows = []
        for a in sorted(inv.adapters, key=lambda a: (a.kind, a.name)):
            ics_ = inv.ics_by_guid(a.guid)
            rows.append([a.name[:28], a.description[:34], a.status, "{" + a.guid + "}", str(a.ifindex),
                         ", ".join(a.ipv4) or "-", a.kind, ics_.sharing if ics_ else ("n/a" if inv.ics is None else "not in ICS")])
        ui.table(["Name", "Description", "Status", "GUID", "ifIdx", "IPv4", "Kind", "ICS"], rows)
    else:
        doctor.run(app.backend, app.settings, ["network"], relay=app.relay)


@command("ics", "ics", "Show Internet Connection Sharing (same as 'doctor ics')", "Diagnostics")
def cmd_ics(app: App, args):
    doctor.run(app.backend, app.settings, ["ics"] + args, relay=app.relay)


@command("doctor", "doctor [section] [save]", "Diagnose Windows, Wi-Fi, VPN, hotspot, ICS", "Diagnostics",
         details="Sections: quick (default), os, wifi, vpn, hotspot, ics, network, clients, full.\n"
                 "Add 'save' to write a shareable report (no keys/passwords) to logs\\.",
         subs=("quick", "os", "wifi", "vpn", "hotspot", "ics", "network", "clients", "full", "save"))
def cmd_doctor(app: App, args):
    doctor.run(app.backend, app.settings, args, relay=app.relay)


@command("app", "app", "Open the WireSpot desktop app (window + tray)", "Everyday", aliases=("tray", "gui"))
def cmd_app(app: App, args):
    from .autostart import tray_command

    exe, targs = tray_command()
    targs = targs.replace("--background", "").replace("--autostart", "").strip()
    if not Path(exe).exists():
        ui.err(f"{Path(exe).name} was not found next to WireSpot.")
        return
    subprocess.Popen(f'"{exe}" {targs}', cwd=str(paths.APP_DIR))
    ui.ok("WireSpot app opened - it also lives in the tray next to the clock.")


@command("autostart", "autostart on|off|status", "Start the tray with Windows (elevated, no UAC prompt)", "Settings",
         subs=("on", "off", "status"))
def cmd_autostart(app: App, args):
    from . import autostart

    sub = args[0].lower() if args else "status"
    if sub == "on":
        ok, msg = autostart.enable()
        (ui.ok if ok else ui.err)("Tray starts at logon (Task Scheduler, highest privileges)" if ok else f"Could not enable: {msg}")
    elif sub == "off":
        ok, msg = autostart.disable()
        (ui.ok if ok else ui.err)("Autostart removed" if ok else f"Could not disable: {msg}")
    else:
        on = autostart.is_enabled()
        ui.kv("Start with Windows", "on" if on else "off")
        ui.kv("Go live at startup", "on" if app.settings["behavior"].get("autoconnect") else "off ('set autoconnect on')")


@command("ip", "ip", "Show the current public IP", "Diagnostics")
def cmd_ip(app: App, args):
    with ui.task("Asking api.ipify.org"):
        ip = app.backend.public_ip()
    ui.kv("Public IP", ip or "unavailable")


@command("logs", "logs", "Show where diagnostic logs are written", "Diagnostics")
def cmd_logs(app: App, args):
    ui.kv("Debug logging", "on" if log.enabled() else "off ('set debug on')")
    ui.kv("Folder", paths.LOG_DIR)


# ---------------------------------------------------------------- settings
SET_HELP = """
ssid <name>                       hotspot network name (1-32 chars)
password [value]                  8-63 chars; omit the value to type it hidden
security wpa2|transition|wpa3     WPA3 modes need Windows 11 24H2+
band auto|2.4|5|6                 'auto' follows the Wi-Fi uplink channel
protection strict|balanced        see 'help protection'
debug on|off                      write logs\\wirespot-YYYY-MM-DD.log
guard on|off                      fail-closed watchdog while READY
profile-less on|off               share a NordVPN app connection without a .conf
dns-lock on|off                   balanced mode: force all DNS through the tunnel
fallbacks on|off                  offer recovery choices when a step fails
watch on|off                      listen for new .conf files in Downloads
autoconnect on|off                tray goes live automatically at logon
wireguard-path <path>             custom wireguard.exe location"""


@command("set", "set <key> <value>", "Change a setting (see 'help set')", "Settings", details=SET_HELP,
         subs=("ssid", "password", "security", "band", "protection", "debug", "guard", "dns-lock", "profile-less",
               "fallbacks", "watch", "autoconnect", "wireguard-path"))
def cmd_set(app: App, args):
    if not args:
        cmd_help(app, ["set"])
        return
    key = args[0].lower()
    value = " ".join(args[1:]).strip()
    s = app.settings
    h, v, bh = s["hotspot"], s["vpn"], s["behavior"]
    onoff = {"on": True, "off": False, "true": True, "false": False, "yes": True, "no": False}

    if key == "password":
        if not value:
            value = ui.ask_secret("Hotspot password (hidden)")
            if value != ui.ask_secret("Repeat"):
                ui.err("Passwords do not match.")
                return
        if not 8 <= len(value) <= 63:
            ui.err("Password must be 8-63 characters.")
            return
        h["password"] = value
        log.register_secret(value)
        app.save()
        ui.ok(f"Saved password ({ui.mask(value)}).")
        return
    if key == "ssid":
        if not value or len(value.encode("utf-8")) > 32:
            ui.err("SSID must be 1-32 bytes.")
            return
        h["ssid"] = value
    elif key == "security":
        if value.lower() not in settings_mod.SECURITY:
            ui.err("Use wpa2, transition or wpa3.")
            return
        h["security"] = value.lower()
        if value.lower() != "wpa2" and sys.getwindowsversion().build < 26100:
            ui.warn("This Windows build cannot select WPA3 for the hotspot; start will offer WPA2.")
    elif key == "band":
        band = settings_mod.normalize_band(value)
        if band not in settings_mod.BANDS:
            ui.err("Use auto, 2.4, 5 or 6.")
            return
        h["band"] = band
        try:
            up = next((i for i in wlan.query_interfaces() if i.connected), None)
        except OSError:
            up = None
        if up and band != "auto" and up.band and up.band != band:
            ui.info(f"Your Wi-Fi uplink is on {up.band} GHz right now. Many adapters can still host {band} GHz; "
                    f"if Windows refuses (WiFiDeviceOff), start offers 'auto'.")
    elif key == "protection":
        if value.lower() not in settings_mod.PROTECTION:
            ui.err("Use strict or balanced.")
            return
        v["protection"] = value.lower()
        if value.lower() == "strict":
            ui.info("strict: WireGuard kill-switch (best leak protection for this PC), but it blocks hotspot DHCP/DNS.")
        else:
            ui.info("balanced: full VPN routing via /1 routes; hotspot-compatible; DNS lock keeps lookups in the tunnel.")
    elif key in ("debug", "guard", "dns-lock", "fallbacks", "watch", "autoconnect", "approval", "profile-less"):
        if value.lower() not in onoff:
            ui.err("Use on or off.")
            return
        flag = onoff[value.lower()]
        skey = {"dns-lock": "dns_lock", "profile-less": "profile_less", "fallbacks": "offer_fallbacks", "watch": "watch_downloads",
                "approval": "approve_devices"}.get(key, key)
        if key == "profile-less" and app.relay.record.tunnel_name:
            ui.err("Stop the current WireSpot session before changing VPN modes.")
            return
        bh[skey] = flag
        if key == "debug":
            if flag:
                ui.ok(f"Logging to {log.enable()}")
            else:
                log.disable()
        if key == "watch":
            app.inbox.start() if flag else app.inbox.stop()
    elif key == "wireguard-path":
        if value and not Path(value).is_file():
            ui.err(f"Not a file: {value}")
            return
        v["wireguard_path"] = value
    else:
        close = difflib.get_close_matches(key, [c for c in COMMANDS["set"].subs], n=2)
        ui.err(f"Unknown setting '{key}'." + (f" Did you mean {', '.join(close)}?" if close else ""))
        return
    app.save()
    ui.ok(f"Saved {key} = {value or '(empty)'}")


@command("debug", "debug [on|off]", "Toggle diagnostic logging", "Settings", subs=("on", "off"))
def cmd_debug(app: App, args):
    target = (args[0].lower() if args else ("off" if log.enabled() else "on"))
    cmd_set(app, ["debug", target])


@command("settings", "settings", "Show settings.json (password masked)", "Settings", aliases=("config",))
def cmd_settings(app: App, args):
    import json

    ui.heading(str(paths.SETTINGS_PATH))
    for line in json.dumps(settings_mod.redacted(app.settings), indent=2).splitlines():
        ui._emit("  " + line)


@command("show", "show password", "Reveal the hotspot password", "Settings", subs=("password",))
def cmd_show(app: App, args):
    if args and args[0].lower() == "password":
        pw = app.settings["hotspot"]["password"]
        ui.kv("Hotspot password", pw or "(not set)")
    else:
        ui.err("Usage: show password")


@command("protection", "protection", "Explain strict vs balanced", "Settings")
def cmd_protection(app: App, args):
    cur = app.settings["vpn"]["protection"]
    ui.heading("Protection modes")
    ui._emit(f"  {ui.color('strict', ui.C.bold)}    keeps AllowedIPs = 0.0.0.0/0. WireGuard for Windows then enables its kill-switch")
    ui._emit("            firewall: this PC cannot leak outside the tunnel, but inbound DHCP/DNS on the hotspot")
    ui._emit("            interface are blocked, so phones cannot get an address. Best for VPN-only use.")
    ui._emit(f"  {ui.color('balanced', ui.C.bold)}  rewrites /0 to 0.0.0.0/1 + 128.0.0.0/1 (and ::/1 + 8000::/1) in the runtime copy.")
    ui._emit("            Full VPN routing; hotspot DHCP/DNS work; WireSpot's DNS lock + guard close the gaps.")
    ui.hint(f"current: {cur} · change with 'set protection balanced|strict'")


# ---------------------------------------------------------------- shell
@command("clear", "clear", "Clear the screen", "Shell", aliases=("cls",))
def cmd_clear(app: App, args):
    if ui.use_color():
        sys.stdout.write("\x1b[2J\x1b[3J\x1b[H")
        sys.stdout.flush()
    else:
        os.system("cls" if os.name == "nt" else "clear")
    ui.set_title(APP_NAME)
    ui.banner(APP_NAME, VERSION, TAGLINE, home_rows(app))


# ---------------------------------------------------------------- device approval
def _pick_device(args, bucket: str):
    store = admission.load()
    items = sorted(store[bucket].items(), key=lambda kv: kv[1].get("since", kv[1].get("t", 0)))
    if not items:
        return None, store
    if not args:
        return (items[0][0] if len(items) == 1 else None), store
    token = args[0]
    if token.isdigit() and 1 <= int(token) <= len(items):
        return items[int(token) - 1][0], store
    from .clients import norm_mac

    mac = norm_mac(token)
    return (mac if any(m == mac for m, _ in items) else None), store


def _show_waiting(store) -> None:
    from .clients import guess_type

    rows = [[str(i), (p.get("ips") or ["-"])[-1], guess_type([], mac), mac]
            for i, (mac, p) in enumerate(sorted(store["pending"].items(), key=lambda kv: kv[1].get("since", 0)), 1)]
    if rows:
        ui.table(["#", "IP", "Device (guess)", "MAC"], rows)


def _verdict(app: App, args, verdict: str) -> None:
    word = {"approve": "Allowed", "block": "Blocked"}[verdict]
    bucket = "pending" if not (args and args[0].lower() == "blocked") else "blocked"
    if bucket == "blocked":
        args = args[1:]
    mac, store = _pick_device(args, bucket)
    if mac is None:
        if not store[bucket]:
            ui.info("No device is waiting for approval." if bucket == "pending" else "No blocked devices.")
        else:
            ui.warn("Which one? Give its number or MAC.")
            _show_waiting(store)
        return
    admission.decide(mac, verdict)
    guid = app.relay.record.hotspot_guid
    if guid:
        from . import neighbors

        idx = neighbors.ifindex_for_guid(guid)
        if idx:
            admission.enforce(idx)
    ui.ok(f"{word}: {mac}" + (" - it can use the hotspot now." if verdict == "approve" else " - it stays on the Wi-Fi without network."))


@command("allow", "allow [n|mac]", "Let a device that is waiting use the hotspot", "Hotspot", aliases=("approve",))
def cmd_allow(app: App, args):
    _verdict(app, args, "approve")


@command("block", "block [n|mac]", "Keep a waiting device off the network (and stop asking)", "Hotspot")
def cmd_block(app: App, args):
    _verdict(app, args, "block")


@command("waiting", "waiting", "Devices waiting for approval", "Hotspot", aliases=("pending",))
def cmd_waiting(app: App, args):
    store = admission.load()
    on = app.settings["behavior"].get("approve_devices", True)
    ui.kv("Device approval", "on - new devices need 'allow'" if on else "off ('set approval on')")
    if not store["pending"]:
        ui.info("Nobody is waiting.")
    _show_waiting(store)
    ui.kv("Allowed devices", str(len(store["approved"])))
    ui.kv("Blocked devices", str(len(store["blocked"])))


@command("version", "version", "Show version information", "Shell")
def cmd_version(app: App, args):
    ui.kv(APP_NAME, f"{VERSION} · {TAGLINE}")
    ui.kv("Python", sys.version.split()[0])


@command("exit", "exit", "Quit (asks what to do with a running session)", "Shell", aliases=("quit", "q"))
def cmd_exit(app: App, args):
    r = app.relay
    if r.m.state in (State.READY, State.VPN_CONNECTED, State.HOTSPOT_ACTIVE):
        pick = app.decide("WireSpot is still running. On exit:", [
            Choice("leave", "Leave it running",
                   "VPN/hotspot stay up; the tray keeps guarding it." if __import__("wirespot.oplock").oplock.exists("Local\\WireSpot.Tray")
                   else "VPN/hotspot stay up; run 'tray' to keep the fail-closed guard alive."),
            Choice("stop", "Stop everything, then exit"),
            Choice("cancel", "Cancel")], 0, cancel="cancel")
        if pick == "cancel":
            return
        if pick == "stop":
            r.stop(app.settings)
    r.stop_guard()
    app.inbox.stop()
    app.running = False


# ====================================================================== startup

def startup_checks(app: App) -> None:
    """Reconcile the recorded state with reality and clean up legacy leftovers."""
    r, b = app.relay, app.backend
    profiles.harden_dir(paths.RUNTIME_DIR)
    with ui.task("Checking system state"):
        svcs, _ = b.wg_services()
    running = {x.name for x in svcs if x.ours and x.running}
    referenced = {Path(x.config_path).name.lower() for x in svcs if x.config_path}
    if paths.LEGACY_RUNTIME_DIR.is_dir():
        removed = 0
        for f in paths.LEGACY_RUNTIME_DIR.glob("*.conf"):
            if f.name.lower() not in referenced:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        profiles.harden_dir(paths.LEGACY_RUNTIME_DIR)
        if removed:
            ui.ok(f"Removed {removed} stale ProtonRelay config cop{'y' if removed == 1 else 'ies'} containing private keys.")
    legacy = [n for n in running if n.startswith(LEGACY_TUNNEL_PREFIXES)]
    rec = r.record
    if r.reconcile_external(app.settings):
        ui.info("NordVPN is managed by its desktop app; WireSpot restored its hosting guard.")
    elif legacy and not rec.tunnel_name:
        ui.info(f"A ProtonRelay 0.1 tunnel is still running: {', '.join(legacy)}.")
        ui.hint("'start' replaces it · 'stop' removes it")
        rec.tunnel_name = legacy[0]
        r.m.force(State.VPN_CONNECTED)
    elif r.m.state != State.DISCONNECTED and rec.tunnel_name and rec.tunnel_name not in running:
        ui.warn(f"The previous session ended unexpectedly (state {r.m.state.value}); its tunnel is gone.")
        ui.hint("Run 'stop' to clear any leftover hotspot / ICS / DNS-lock settings.")
        r.m.force(State.ERROR)
    elif r.m.state == State.READY and rec.tunnel_name in running:
        ui.ok(f"Resuming: {rec.tunnel_name} is up and the hotspot session is recorded as live.")
        r.start_guard(app.settings)          # guard + device approval, as enabled in settings
    elif rec.tunnel_name in running:
        ui.info(f"VPN tunnel {rec.tunnel_name} is connected.")


def home_rows(app: App) -> list[str]:
    s = app.settings
    profs, _ = app.profiles()
    default = profiles.resolve_profile(None, profs, s["vpn"].get("default_profile", ""))
    h = s["hotspot"]

    def row(k, v):
        return f"{ui.color(k.ljust(9), ui.C.muted)}{v}"

    pw = "password set" if h["password"] else ui.color("password not set", ui.C.warning)
    profile_text = f"{len(profs)}" + (f" · default {default.server_name or default.path.stem}"
                    + (f" ({default.country})" if default.country else "") if default else " · none yet")
    if s["behavior"].get("profile_less"):
        profile_text = "NordVPN (connect in its app)"
    return [
        row("profiles", profile_text),
        row("hotspot", f"{h['ssid']} · {hs_mod.BAND_LABEL.get(h['band'], h['band'])} · {h['security'].upper()} · {pw}"),
        row("mode", "Profile-less · NordVPN" if s["behavior"].get("profile_less") else
            f"{s['vpn']['protection']} · the hotspot shares only the VPN tunnel"),
        row("inbox", "watching Downloads for .conf files · or drop one here" if app.inbox.watching else "off"),
    ]


def home(app: App) -> None:
    s = app.settings
    profs, _ = app.profiles()
    if not profs and not s["behavior"].get("profile_less"):
        ui.hint("Add a Proton WireGuard config first: drag the .conf onto this window.")
    elif not s["hotspot"]["password"]:
        ui.hint("Next: 'set password' (hidden prompt), then 'start'.")
    else:
        ui.hint("Start typing for suggestions · 'start' to go live · 'help' for everything")


def _journal_tee(line: str) -> None:
    """Share what the CLI does with the app's Activity page: network operations and guard events only."""
    import threading

    if getattr(sync._local, "depth", 0) or threading.current_thread().name in ("wirespot-guard", "wirespot-gate"):
        sync.journal(line, "cli")


def relaunch_elevated(argv: list[str]) -> bool:
    import ctypes

    params = subprocess.list2cmdline(argv + ["--elevated"])
    if getattr(sys, "frozen", False):
        exe = sys.executable
    else:
        exe = sys.executable
        params = subprocess.list2cmdline([str(Path(sys.argv[0]).resolve())]) + " " + params
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(paths.APP_DIR), 1)
    return rc > 32


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    elevated = "--elevated" in argv
    argv = [a for a in argv if a != "--elevated"]
    if os.name != "nt":
        print(f"{APP_NAME} targets Windows 10/11.")
        return 2
    paths.VPN_DIR.mkdir(parents=True, exist_ok=True)
    s, _ = settings_mod.load()
    if not paths.SETTINGS_PATH.exists():
        settings_mod.save(s)
    if s["behavior"].get("debug"):
        log.enable()
    log.event("info", f"{APP_NAME} {VERSION} argv={argv} app={paths.APP_DIR} data={paths.BASE}")

    from .winexec import is_admin

    if not is_admin() and not os.environ.get("WIRESPOT_NO_ELEVATE"):
        ui.info("Administrator rights are needed for WireGuard services and Internet Connection Sharing.")
        if relaunch_elevated(argv):
            return 0
        ui.err("Elevation was cancelled.")
        return 1

    ui.INTERACTIVE = ui._isatty(sys.stdin)
    if argv and argv[0].lower() in ("--yes", "-y"):
        ui.AUTO_YES = True
        argv = argv[1:]

    sync.SOURCE = "cli"
    ui.set_title(APP_NAME)
    ui.tee = _journal_tee
    app = App()
    if argv:  # one-shot mode: WireSpot.exe doctor full save
        startup_checks(app)
        app.dispatch_line(subprocess.list2cmdline(argv))
        if elevated and ui.INTERACTIVE:
            input("\nPress Enter to close…")
        return 0

    if app.settings["behavior"].get("watch_downloads", True):
        app.inbox.start()
    ui.banner(APP_NAME, VERSION, TAGLINE, home_rows(app))
    startup_checks(app)
    home(app)
    try:
        app.loop()
    finally:
        app.relay.stop_guard()
        app.inbox.stop()
    return 0
