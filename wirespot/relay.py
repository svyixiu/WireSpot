"""The relay engine: ``start`` as a transaction with explicit states and rollback.

    DISCONNECTED -> VPN_CONNECTING -> VPN_CONNECTED -> HOTSPOT_STARTING
      -> HOTSPOT_ACTIVE -> SHARING_CONFIGURING -> READY      (STOPPING / ERROR)

Rules:
  * A stage that fails never gets a success marker and the pipeline never
    continues past it.
  * Failure after the VPN is verified rolls back the sharing layer (hotspot,
    ICS, DNS lock) and leaves the VPN up - and says so.
  * Failure before that rolls back everything WireSpot created.
  * Only WireSpot-owned objects are ever removed.
"""
from __future__ import annotations

import functools
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import clients as clients_mod
from . import hotspot as hs_mod
from . import ics as ics_mod
from . import admission, log, oplock, paths, profiles, settings as settings_mod, sync, ui
from .netid import locate_hotspot, norm_guid
from .state import Machine, RelayRecord, State, Transaction
from .ui import Choice
from .wlan import WlanInterface
from .nordvpn import NordVPNProvider
from . import forward_guard, network_events

PHASES = 7


class Abort(Exception):
    """Stop the pipeline. ``keep_vpn`` decides the rollback scope."""

    def __init__(self, message: str = "", keep_vpn: bool = True):
        super().__init__(message)
        self.keep_vpn = keep_vpn


@dataclass
class StartOptions:
    band: str | None = None
    vpn_only: bool = False
    protection: str | None = None
    reuse_vpn: bool = False          # hotspot start: keep the connected tunnel


@dataclass
class Ctx:
    settings: dict
    profile: profiles.Profile | None = None
    protection: str = "strict"
    wifi: WlanInterface | None = None
    band: str = "auto"
    security: str = "wpa2"
    wireguard: Path | None = None
    wg: Path | None = None
    tunnel_name: str = ""
    tunnel_guid: str = ""
    tunnel_ifindex: int = 0
    hotspot_guid: str = ""
    hotspot_name: str = ""
    vpn_verified: bool = False
    external: bool = False
    notes: list[str] = field(default_factory=list)


def exclusive(fn):
    """Run a network-changing operation under the cross-process lock, on fresh state."""
    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        try:
            with oplock.operation():
                self.reload()
                sync.op_begin(fn.__name__)
                ok = False
                try:
                    ok = fn(self, *a, **k)
                    return ok
                finally:
                    sync.op_end(bool(ok), "" if ok else self.record.last_error)
        except oplock.Busy as e:
            op = sync.remote_op()
            ui.warn(f"Busy: {sync.describe(op) if op else e}. Try again when it finishes.")
            return False
    return wrapper


class Relay:
    def __init__(self, backend, *, decide: Callable = ui.choose, state_path: Path | None = None,
                 save_settings: Callable[[dict], None] | None = None, sleep=time.sleep):
        self.b = backend
        self.decide = decide
        self.sleep = sleep
        self.state_path = state_path if state_path is not None else paths.STATE_PATH
        self.record = RelayRecord.load(self.state_path)
        self.m = Machine(self.record, self.state_path,
                         on_change=lambda a, b_: log.event("info", f"STATE {a.value} -> {b_.value}"))
        self.save_settings = save_settings or settings_mod.save
        self.lock = threading.RLock()
        self.guard: Guard | None = None
        self.gate = None                       # admission.Gatekeeper while live
        self.gate_factory = admission.Gatekeeper
        # notify(kind, message) - kinds: joined, left, fail, hotspot_off. Set by the tray.
        self.notify: Callable[[str, str], None] | None = None

    def reload(self) -> None:
        """Pick up changes another WireSpot process (CLI/tray) wrote to state.json."""
        rec = RelayRecord.load(self.state_path)
        self.record = rec
        self.m.record = rec

    def _notify(self, kind: str, message: str) -> None:
        if self.notify:
            try:
                self.notify(kind, message)
            except Exception as e:
                log.event("error", f"notify: {e}")

    # ================================================================ start
    @exclusive
    def start(self, s: dict, token: str | None, opts: StartOptions | None = None) -> bool:
        opts = opts or StartOptions()
        with self.lock:
            self.stop_guard()
            ctx = Ctx(settings=s)
            tx = Transaction(report=self._report_undo)
            try:
                if self.m.state not in (State.DISCONNECTED, State.ERROR, State.VPN_CONNECTED, State.READY):
                    self.m.force(State.ERROR)
                want_external = bool(s["behavior"].get("profile_less"))
                if self.record.tunnel_name and bool(self.record.provider) != want_external:
                    ui.err("Stop the current WireSpot session before switching VPN modes.")
                    return False
                if not self._preflight(ctx, token, opts):
                    return False
                if self.record.hotspot_started_by_us or self.record.ics_guids or self.record.dns_lock or self.record.forward_guard:
                    ui.info("Tearing down the current hotspot session first…")
                    if not self._stop_sharing():
                        self._safe_to(State.ERROR, "Could not safely stop the previous hotspot session")
                        return False
                    if self.m.state == State.READY:
                        self._safe_to(State.VPN_CONNECTED)
                if ctx.external:
                    if not self._connect_external(ctx):
                        return False
                elif not (opts.reuse_vpn and self._attach_vpn(ctx)):
                    if not self._connect_vpn(ctx, tx):
                        return False
                if opts.vpn_only or not s["behavior"].get("auto_start_hotspot", True):
                    tx.commit()
                    self._vpn_only_summary(ctx)
                    return True
                if not self._share(ctx, tx):
                    return False
                tx.commit()
                self._ready_summary(ctx)
                self.start_guard()
                return True
            except Abort as a:
                self._abort(ctx, tx, str(a), a.keep_vpn)
                return False
            except KeyboardInterrupt:
                ui.blank()
                self._abort(ctx, tx, "Interrupted.", keep_vpn=ctx.vpn_verified)
                return False
            except Exception as e:  # never leave a half-configured system behind
                log.event("error", f"unexpected {type(e).__name__}: {e}")
                self._abort(ctx, tx, f"Unexpected error: {type(e).__name__}: {e}", keep_vpn=ctx.vpn_verified)
                return False

    def _abort(self, ctx: Ctx, tx: Transaction, message: str, keep_vpn: bool) -> None:
        if message:
            ui.err(message)
        keep_vpn = keep_vpn and ctx.vpn_verified
        if tx.stack:
            ui.info("Rolling back " + ("hotspot/sharing changes" if keep_vpn else "all changes") + "…")
            errors = tx.rollback({"share"} if keep_vpn else None)
            for e in errors:
                ui.warn(f"Rollback step failed: {e}")
        if self.record.forward_guard:
            try:
                self._release_forward_guard()
            except Exception as e:
                ui.err(f"Forwarding guard remains active: {e}")
        self.record.hotspot_started_by_us = False
        self.record.ics_changed = False
        self.record.ics_guids = []
        self.record.dns_lock = False
        self.record.ready_since = 0.0
        if self.record.forward_guard:
            self._safe_to(State.ERROR, message or "Forwarding guard retained while hotspot status is uncertain")
        elif keep_vpn:
            self._safe_to(State.VPN_CONNECTED, message)
            ui.info(("NordVPN remains managed by its desktop app." if ctx.external else
                     f"VPN remains connected ({ctx.tunnel_name}). Use 'stop' to disconnect it."))
            ui.hint("Run 'doctor hotspot' for diagnostics.")
        else:
            self.record.tunnel_name = self.record.tunnel_guid = self.record.runtime_conf = ""
            self.record.provider = self.record.provider_protocol = ""
            self._safe_to(State.DISCONNECTED, message)
        self.m.persist()

    def _safe_to(self, new: State, error: str = "") -> None:
        try:
            self.m.to(new, error)
        except Exception:
            self.m.force(new)

    def _report_undo(self, desc: str, ok: bool, err: str) -> None:
        (ui.detail if ok else ui.warn)(f"undo: {desc}" + ("" if ok else f" FAILED {err}"))

    # ------------------------------------------------------------ preflight
    def _preflight(self, ctx: Ctx, token: str | None, opts: StartOptions) -> bool:
        s = ctx.settings
        ctx.external = bool(s["behavior"].get("profile_less"))
        ui.step(1, PHASES, "Preflight")
        log.event("info", "stage 1: validate environment")
        env = (self.b.env(s["vpn"].get("wireguard_path", ""), require_wireguard=False)
               if ctx.external else self.b.env(s["vpn"].get("wireguard_path", "")))
        if env.problems:
            for p in env.problems:
                ui.err(p)
            return False
        ctx.wireguard, ctx.wg = env.wireguard, env.wg
        ui.ok(f"Windows build {env.build} · administrator" + ("" if ctx.external else " · WireGuard found"))

        if ctx.external:
            detected = NordVPNProvider(self.b).detect()
            if not detected.adapter or not detected.route_valid:
                ui.err(detected.reason)
                if self.record.provider == "nordvpn":
                    if self.record.hotspot_started_by_us:
                        self.b.hotspot_stop(self.record.tunnel_guid)
                        self.record.hotspot_started_by_us = False
                    self._safe_to(State.ERROR, detected.reason)
                return False

        log.event("info", "stage 2: validate config")
        if not opts.vpn_only and not self._ensure_hotspot_config(s):
            return False

        if ctx.external:
            if token:
                ui.err("Profile-less Mode uses NordVPN; no WireGuard profile can be selected.")
                return False
            if opts.protection:
                ui.err("WireGuard protection modes do not apply to NordVPN.")
                return False
            ctx.protection = "NordVPN"
            if opts.vpn_only:
                return True
            if not self._check_uplink(ctx):
                return False
            return self._check_tethering(ctx, opts)

        log.event("info", "stage 3: validate profile")
        profs, bad = profiles.list_profiles(paths.VPN_DIR)
        for path, why in bad:
            ui.warn(f"Skipping {path.name}: {why}")
        if not profs:
            ui.err(f"No usable WireGuard profile in {paths.VPN_DIR}")
            ui.hint("Drop a Proton .conf onto this window, save one to Downloads, or use 'import <path>'.")
            return False
        p = profiles.resolve_profile(token, profs, s["vpn"].get("default_profile", ""))
        if not p:
            ui.err(f"No profile matches '{token}'. Use 'profiles' to list them.")
            return False
        ctx.profile = p
        ui.ok(f"Profile {p.label}")

        ctx.protection = opts.protection or s["vpn"].get("protection", "strict")
        if not opts.vpn_only and ctx.protection == "strict" and p.full_tunnel:
            if not self._resolve_strict(ctx):
                return False
        ui.detail(f"Protection: {ctx.protection}" + (" (WireGuard kill-switch active)" if ctx.protection == "strict" and p.full_tunnel else ""))

        if opts.vpn_only:
            return True

        log.event("info", "stage 4: check Wi-Fi uplink")
        if not self._check_uplink(ctx):
            return False

        log.event("info", "stage 5+6: tethering capability, band and security")
        return self._check_tethering(ctx, opts)

    def _ensure_hotspot_config(self, s: dict) -> bool:
        problems = settings_mod.validate_hotspot(s)
        pwd_problem = any("password" in p.lower() for p in problems)
        if pwd_problem and ui.INTERACTIVE and not ui.AUTO_YES:
            ui.warn("The hotspot password is not set (8-63 characters).")
            pick = self.decide("Set it now?", [
                Choice("set", "Enter a hotspot password now", "Typed hidden; saved in settings.json in the WireSpot data folder."),
                Choice("stop", "Stop"),
            ], 0, cancel="stop")
            if pick != "set":
                return False
            for _ in range(3):
                pw = ui.ask_secret("New hotspot password")
                if not 8 <= len(pw) <= 63:
                    ui.err("Must be 8-63 characters.")
                    continue
                if ui.ask_secret("Repeat password") != pw:
                    ui.err("Passwords do not match.")
                    continue
                s["hotspot"]["password"] = pw
                self.save_settings(s)
                log.register_secret(pw)
                ui.ok("Hotspot password saved.")
                break
            problems = settings_mod.validate_hotspot(s)
        if problems:
            for p in problems:
                ui.err(p)
            return False
        log.register_secret(s["hotspot"]["password"])
        return True

    def _resolve_strict(self, ctx: Ctx) -> bool:
        ui.warn("Strict mode keeps AllowedIPs = 0.0.0.0/0, which switches on WireGuard's kill-switch firewall.")
        ui.detail("That firewall blocks inbound DHCP (UDP 67) and DNS (53) on every non-tunnel interface,")
        ui.detail("including the hotspot, so phones join JulyVPN but never receive an IP address.")
        pick = self.decide("How should WireSpot route this session?", [
            Choice("once", "Use balanced for this session",
                   "Full VPN routing via 0.0.0.0/1 + 128.0.0.0/1; hotspot DHCP/DNS work. settings.json unchanged."),
            Choice("save", "Switch to balanced and save it", "Same as above, and becomes the default."),
            Choice("strict", "Keep strict anyway", "Maximum host leak protection; hotspot clients will most likely fail."),
            Choice("stop", "Stop"),
        ], 0, cancel="stop")
        if pick == "stop":
            return False
        if pick in ("once", "save"):
            ctx.protection = "balanced"
            if pick == "save":
                ctx.settings["vpn"]["protection"] = "balanced"
                self.save_settings(ctx.settings)
                ui.ok("Saved protection = balanced.")
            else:
                ui.info("Using balanced for this session only.")
        return True

    def _check_uplink(self, ctx: Ctx) -> bool:
        while True:
            ifaces, e = self.b.wlan()
            if e or not ifaces:
                ui.err("No Wi-Fi adapter is available: " + (e or "Windows reports no WLAN interface."))
                ui.hint("Mobile Hotspot needs a Wi-Fi adapter with the WLAN AutoConfig service running.")
                return False
            off = [i for i in ifaces if not i.radio_on]
            if off and not any(i.connected for i in ifaces):
                ui.err(f"The Wi-Fi radio is switched off ({off[0].description}: software {off[0].radio_software}, hardware {off[0].radio_hardware}).")
                pick = self.decide("Turn Wi-Fi on (Action Center / airplane mode), then:", [
                    Choice("retry", "Check again"), Choice("stop", "Stop")], 0, cancel="stop")
                if pick == "retry":
                    continue
                return False
            ctx.wifi = next((i for i in ifaces if i.connected), None)
            uplinks = self.b.uplink()
            best = uplinks[0] if uplinks else None
            from .netid import uplink_kind

            if best is None:
                ui.err("There is no internet uplink (no default route on Wi-Fi, Ethernet or USB).")
                ui.hint("Connect to a network first; the VPN needs a path to the Proton server.")
                return False
            kind = uplink_kind(best)
            if ctx.wifi and kind == "Wi-Fi":
                band = f"{ctx.wifi.band} GHz" if ctx.wifi.band else "unknown band"
                ui.ok(f"Uplink Wi-Fi '{ctx.wifi.ssid}' · {band} · channel {ctx.wifi.channel} · signal {ctx.wifi.signal}%")
            else:
                ui.ok(f"Uplink {kind} '{best['name']}' ({best['description']}) · gateway {best.get('gateway') or '?'}")
                if ctx.wifi:
                    ui.detail(f"Wi-Fi is also connected to '{ctx.wifi.ssid}' ({ctx.wifi.band or '?'} GHz); "
                              f"the hotspot shares the radio with it.")
                else:
                    ui.detail("The Wi-Fi radio is free, so the hotspot can use any band the adapter supports.")
            return True

    def _check_tethering(self, ctx: Ctx, opts: StartOptions) -> bool:
        s = ctx.settings
        wifi_guid = ctx.wifi.guid if ctx.wifi else ""
        with ui.task("Checking Mobile Hotspot capability"):
            data, e = self.b.hotspot_status(wifi_guid=wifi_guid, source="any")
        if data is None or e:
            ui.err(f"Could not query Windows Mobile Hotspot: {e or 'no data'}")
            return False
        src = data.get("source") or {}
        cap = src.get("capability", "")
        if cap != "Enabled":
            ui.err(f"Mobile Hotspot is unavailable: {cap}. {hs_mod.CAPABILITY.get(cap, '')}")
            return False
        ui.ok(f"Wi-Fi tethering supported · up to {data.get('max_clients', '?')} devices")
        if data.get("state") == "On" and not self.record.hotspot_started_by_us:
            ui.warn("Mobile Hotspot is already on (started outside WireSpot) and currently shares a non-VPN connection.")
            pick = self.decide("Reconfigure it for WireSpot?", [
                Choice("go", "Reconfigure it (it restarts behind the VPN)"), Choice("stop", "Stop")], 0, cancel="stop")
            if pick == "stop":
                return False

        band = opts.band or s["hotspot"].get("band", "auto")
        plan = hs_mod.plan_band(band, ctx.wifi.band if ctx.wifi else "", hs_mod.supported_bands(data))
        if not plan.ok:
            ui.warn(f"{hs_mod.BAND_LABEL.get(band, band)} hotspot is unavailable right now.")
            ui.detail(plan.reason)
            choices = []
            for alt in plan.alternatives:
                label = "auto (follow the uplink channel)" if alt == "auto" else hs_mod.BAND_LABEL[alt]
                choices.append(Choice(alt, f"Use {label} for this session", "settings.json is not changed."))
            choices += [Choice("anyway", f"Try {hs_mod.BAND_LABEL.get(band, band)} anyway"), Choice("stop", "Stop")]
            pick = self.decide("Which band?", choices, 0, cancel="stop")
            if pick == "stop":
                return False
            if pick != "anyway":
                ui.info(f"Band for this session: {hs_mod.BAND_LABEL[pick]} (saved setting stays '{band}').")
                band = pick
        else:
            ui.ok(f"Band {hs_mod.BAND_LABEL.get(band, band)} supported by the adapter")
            if plan.note:
                ui.detail(plan.note)
        ctx.band = band

        sec = s["hotspot"].get("security", "wpa2")
        if sec != "wpa2":
            kinds = data.get("auth_kinds") or {}
            want = {"transition": "Wpa3TransitionMode", "wpa3": "Wpa3"}[sec]
            if kinds.get(want) is not True:
                why = "this Windows build cannot select WPA3 (needs 24H2 / 26100+)" if not kinds else "the adapter does not support it"
                ui.warn(f"Security '{sec}' is unavailable: {why}.")
                pick = self.decide("Use WPA2 instead?", [
                    Choice("wpa2", "Use WPA2 for this session"), Choice("stop", "Stop")], 0, cancel="stop")
                if pick == "stop":
                    return False
                sec = "wpa2"
        ctx.security = sec
        ui.ok(f"Security {sec.upper()} supported")
        return True

    # ------------------------------------------------------------ VPN
    def _connect_external(self, ctx: Ctx) -> bool:
        svcs, _ = self.b.wg_services()
        if any(x.ours and x.running for x in svcs):
            ui.err("A WireSpot WireGuard tunnel is still running. Stop it before hosting NordVPN.")
            return False
        self._safe_to(State.VPN_CONNECTING)
        status = NordVPNProvider(self.b).detect(validate=True)
        if not status.ready or not status.adapter:
            self._safe_to(State.DISCONNECTED, status.reason)
            ui.err(status.reason)
            return False
        a = status.adapter
        ctx.tunnel_name, ctx.tunnel_guid, ctx.tunnel_ifindex = "NordVPN", a.guid, a.ifindex
        ctx.vpn_verified = True
        self.record.tunnel_name, self.record.tunnel_guid = "NordVPN", a.guid
        self.record.profile, self.record.protection = "", "NordVPN"
        self.record.provider, self.record.provider_protocol = "nordvpn", status.protocol
        self._safe_to(State.VPN_CONNECTED)
        ui.ok(f"NordVPN {status.protocol} · route, internet, DNS and sharing validated")
        return True

    def _connect_vpn(self, ctx: Ctx, tx: Transaction) -> bool:
        p = ctx.profile
        ui.step(2, PHASES, "WireGuard")
        log.event("info", "stage 7: connect WireGuard")
        svcs, e = self.b.wg_services()
        foreign = [x for x in svcs if not x.ours and x.running]
        if foreign:
            ui.warn("Another WireGuard tunnel is running: " + ", ".join(x.name for x in foreign))
            ui.detail("Two full-tunnel VPNs fight over the default route; traffic may bypass Proton.")
            pick = self.decide("Continue?", [
                Choice("stop", "Stop - I will disconnect the other tunnel first"),
                Choice("go", "Continue anyway")], 0, cancel="stop")
            if pick == "stop":
                return False
        self.m.to(State.VPN_CONNECTING)
        for x in [x for x in svcs if x.ours]:
            with ui.task(f"Removing previous tunnel {x.name}"):
                r = self.b.wg_uninstall(ctx.wireguard, x.name)
            if r.ok:
                self.b.remove_runtime(x.name)
                ui.ok(f"Removed previous tunnel {x.name}")
            else:
                raise Abort(f"Could not remove previous tunnel {x.name}: {r.error_text()}", keep_vpn=False)

        conf, name = self.b.write_runtime(p, ctx.protection)
        ctx.tunnel_name = name
        tx.add("vpn", "delete runtime config", lambda: self.b.remove_runtime(name))
        with ui.task(f"Starting tunnel {name}"):
            r = self.b.wg_install(ctx.wireguard, conf)
        if not r.ok:
            raise Abort(f"WireGuard refused the tunnel: {r.error_text()}", keep_vpn=False)
        tx.add("vpn", f"uninstall tunnel {name}", lambda: self.b.wg_uninstall(ctx.wireguard, name).ok)
        self.record.tunnel_name, self.record.runtime_conf = name, str(conf)
        self.record.profile, self.record.protection = p.path.name, ctx.protection
        self.m.persist()

        adapter = None
        with ui.task("Waiting for the tunnel adapter") as t:
            for i in range(30):
                adapter = self.b.adapter(name=name)
                if adapter and adapter.get("status") == "Up" and adapter.get("ipv4"):
                    break
                t.update(f"Waiting for the tunnel adapter ({adapter.get('status') if adapter else 'not created yet'})")
                self.sleep(0.5)
        if not (adapter and adapter.get("status") == "Up"):
            raise Abort("The WireGuard adapter did not come up.", keep_vpn=False)
        ctx.tunnel_guid = norm_guid(adapter["guid"])
        ctx.tunnel_ifindex = int(adapter["ifindex"])
        self.record.tunnel_guid = ctx.tunnel_guid
        self.m.persist()
        ui.ok(f"Tunnel adapter {name}")
        ui.detail(f"GUID {{{ctx.tunnel_guid}}} · ifIndex {ctx.tunnel_ifindex} · {', '.join(adapter.get('ipv4') or [])}")

        ui.step(3, PHASES, "Verify tunnel")
        log.event("info", "stage 8: verify handshake/interface/routing")
        while True:
            with ui.task(f"Handshake with {p.endpoint}"):
                st, err_ = self.b.wg_handshake(ctx.wg, name, 20.0) if ctx.wg else (None, "wg.exe not found")
            if not err_:
                break
            ui.err(f"No WireGuard handshake: {err_}")
            ui.detail("Server unreachable, UDP blocked by this network, or the Proton key was revoked/expired.")
            pick = self.decide("What now?", [
                Choice("stop", "Disconnect and stop"), Choice("retry", "Wait 20 s more"),
                Choice("keep", "Keep the tunnel anyway (not verified)")], 0, cancel="stop")
            if pick == "stop":
                raise Abort("", keep_vpn=False)
            if pick == "keep":
                st = None
                break
        if st and st.peers:
            peer = st.peers[0]
            from .wireguard import fmt_age, fmt_bytes

            ui.ok(f"Handshake {fmt_age(peer.handshake_age())} · rx {fmt_bytes(peer.rx)} · tx {fmt_bytes(peer.tx)}")
        rc = self.b.route_check()
        bad = [r for r in rc.get("routes", []) if r.get("ifindex") != ctx.tunnel_ifindex]
        if not rc.get("ok") or bad:
            where = ", ".join(f"{r.get('target')} via {r.get('alias') or r.get('error')}" for r in bad) or rc.get("error", "")
            raise Abort(f"Internet traffic would not use the tunnel: {where}", keep_vpn=False)
        ui.ok("Internet routes resolve to the tunnel")
        self.m.to(State.VPN_CONNECTED)
        ctx.vpn_verified = True
        ctx.settings["vpn"]["default_profile"] = p.path.name
        self.save_settings(ctx.settings)
        return True

    def _attach_vpn(self, ctx: Ctx) -> bool:
        """Reuse the running WireSpot tunnel instead of reconnecting."""
        rec = self.record
        if not rec.tunnel_name or rec.profile != ctx.profile.path.name:
            return False
        if rec.protection and rec.protection != ctx.protection:
            return False   # protection changed (e.g. strict -> balanced): reconnect
        svcs, _ = self.b.wg_services()
        if not any(x.name == rec.tunnel_name and x.running for x in svcs):
            return False
        ad = self.b.adapter(name=rec.tunnel_name)
        if not ad or ad.get("status") != "Up":
            return False
        ctx.tunnel_name, ctx.tunnel_guid, ctx.tunnel_ifindex = rec.tunnel_name, norm_guid(ad["guid"]), int(ad["ifindex"])
        rc = self.b.route_check()
        if not rc.get("ok") or any(r.get("ifindex") != ctx.tunnel_ifindex for r in rc.get("routes", [])):
            return False
        ui.step(2, PHASES, "WireGuard")
        ui.ok(f"Using the connected tunnel {rec.tunnel_name} ({ctx.protection})")
        ctx.vpn_verified = True
        if self.m.state != State.VPN_CONNECTED:
            self.m.force(State.VPN_CONNECTED)
        return True

    # ------------------------------------------------------------ hotspot + sharing
    def _share(self, ctx: Ctx, tx: Transaction) -> bool:
        s = ctx.settings
        if ctx.external:
            status = NordVPNProvider(self.b).detect(validate=True)
            if not status.ready or not status.adapter or status.adapter.guid != ctx.tunnel_guid:
                raise Abort("NordVPN changed before hosting: " + status.reason, keep_vpn=True)
            try:
                forward_guard.install(ctx.tunnel_guid)
            except forward_guard.ForwardGuardError as e:
                raise Abort("Cannot safely share NordVPN: " + str(e), keep_vpn=True) from e
            self.record.forward_guard = True
            self.m.persist()
            tx.add("share", "remove forwarding guard", self._release_forward_guard)
        ui.step(4, PHASES, "Mobile Hotspot")
        # The hotspot is ALWAYS started from the WireGuard connection profile:
        # Windows' tethering service then NATs hotspot clients into the tunnel.
        # There is deliberately no "share Wi-Fi" fallback - it would give the
        # phones a path that bypasses the VPN.
        checks = 0
        while not self._vpn_source_ready(ctx):
            checks += 1
            ui.detail("WireSpot only shares the VPN connection; it will not fall back to sharing plain Wi-Fi.")
            pick = self.decide("What now?", [
                Choice("retry", "Check again", "Windows sometimes needs a few seconds to classify a new tunnel."),
                Choice("vpn-only", "Keep only the VPN, stop here"),
                Choice("stop", "Disconnect VPN and stop")], 0 if checks < 3 else 1, cancel="vpn-only")
            if pick == "stop":
                raise Abort("", keep_vpn=False)
            if pick == "vpn-only":
                raise Abort("Hotspot setup skipped.", keep_vpn=True)

        log.event("info", "stage 9+10: start + verify Mobile Hotspot")
        attempts = 0
        while True:
            attempts += 1
            self.m.to(State.HOTSPOT_STARTING)
            label = hs_mod.BAND_LABEL.get(ctx.band, ctx.band)
            with ui.task(f"Starting '{s['hotspot']['ssid']}' · {label} · {ctx.security.upper()} · sharing the VPN"):
                res = self.b.hotspot_start(
                    ssid=s["hotspot"]["ssid"], passphrase=s["hotspot"]["password"], band=ctx.band,
                    security=ctx.security, source="vpn", tunnel_guid=ctx.tunnel_guid,
                    wifi_guid=ctx.wifi.guid if ctx.wifi else "")
            if res.ok:
                break
            self.m.to(State.VPN_CONNECTED)
            if res.state_after and res.state_after not in ("Off",):
                self.b.hotspot_stop(ctx.tunnel_guid)
            what = res.status or res.code or "error"
            ui.err(f"Mobile Hotspot could not start: {what}")
            if res.status:
                ui.detail(hs_mod.describe_status(res.status))
            if res.status == "Success" and res.state_after != "On":
                ui.detail(f"Windows accepted the request but the hotspot state is '{res.state_after}', not On.")
            for extra in (res.message, res.error if res.error != what else ""):
                if extra:
                    ui.detail(extra)
            self._failure_context(ctx)
            if attempts >= 4:
                raise Abort("Giving up after 4 attempts.", keep_vpn=True)
            choices = []
            if ctx.band != "auto":
                choices.append(Choice("auto", "Retry with band auto", "Lets Windows/driver pick a band it can host."))
            choices += [Choice("retry", "Retry as is", "WiFiDeviceOff is sometimes transient right after a network change."),
                        Choice("doctor", "Show hotspot diagnostics, then decide"),
                        Choice("vpn-only", "Keep the VPN connected, stop here"),
                        Choice("stop", "Disconnect VPN and stop")]
            while True:
                pick = self.decide("How do you want to continue?", choices, 0, cancel="vpn-only")
                if pick != "doctor":
                    break
                from . import doctor

                doctor.run(self.b, s, ["hotspot"], relay=self)
            if pick == "stop":
                raise Abort("", keep_vpn=False)
            if pick == "vpn-only":
                raise Abort("", keep_vpn=True)
            if pick == "auto":
                ctx.band = "auto"
                ui.info("Retrying with band=auto (this session only).")

        self.record.hotspot_started_by_us = True
        self.record.hotspot_source, self.record.band = "vpn", res.applied_band or ctx.band
        self.record.wifi_guid = ctx.wifi.guid if ctx.wifi else ""
        tx.add("share", "stop Mobile Hotspot", lambda: self.b.hotspot_stop(ctx.tunnel_guid).ok)
        self.m.to(State.HOTSPOT_ACTIVE)
        src = res.source or {}
        if src.get("kind") != "vpn" or norm_guid(src.get("adapter")) != ctx.tunnel_guid:
            raise Abort(f"Windows started the hotspot from '{src.get('name', '?')}' instead of the VPN; "
                        f"stopped to avoid an unprotected path.", keep_vpn=True)
        ui.ok(f"{s['hotspot']['ssid']} is broadcasting")
        ui.detail(f"Band {hs_mod.BAND_LABEL.get(res.applied_band or ctx.band, ctx.band)} · {ctx.security.upper()} · "
                  f"source {src.get('name')} (the tunnel)")
        nct = self.b.hotspot_timeout(False)
        if nct.get("ok"):
            self.record.no_connections_timeout_before = bool(nct.get("before"))
            if nct.get("before"):
                tx.add("share", "restore hotspot idle timeout", lambda: self.b.hotspot_timeout(True).get("ok", False))
                ui.detail("Idle auto-off disabled while WireSpot runs (restored on stop).")

        ui.step(5, PHASES, "Hotspot interface")
        log.event("info", "stage 11: locate hotspot interface")
        inv, match = None, None
        with ui.task("Identifying the hotspot adapter") as t:
            for i in range(12):
                inv = self.b.inventory(with_ics=False)
                match = locate_hotspot(inv, ctx.wifi.guid if ctx.wifi else "")
                if match.adapter and match.adapter.ipv4:
                    break
                t.update(f"Identifying the hotspot adapter (attempt {i + 2})")
                self.sleep(1.5)
        if not (match and match.adapter):
            ui.err({"no_virtual_adapter": "No Microsoft Wi-Fi Direct Virtual Adapter exists on this PC.",
                    "none_up": "The hotspot reports On, but no Wi-Fi Direct virtual adapter is Up.",
                    "ambiguous": "More than one virtual adapter looks like the hotspot; refusing to guess."}.get(
                match.problem if match else "", "Hotspot adapter not found."))
            for a, score, why in (match.candidates if match else []):
                ui.detail(f"{a.name} · {a.description} · {a.status} · {{{a.guid}}} · score {score} ({', '.join(why)})")
            raise Abort("", keep_vpn=True)
        ha = match.adapter
        ctx.hotspot_guid, ctx.hotspot_name = ha.guid, ha.name
        self.record.hotspot_guid = ha.guid
        self.m.persist()
        self.start_gate(ctx.settings)          # device approval holds new devices from the first second
        ui.ok("Hotspot interface identified")
        ui.detail(f"{ha.name} · {ha.description}")
        ui.detail(f"GUID {{{ha.guid}}} · ifIndex {ha.ifindex} · IPv4 {', '.join(ha.ipv4) or '-'}")
        ui.detail("matched by: " + ", ".join(match.reasons))

        ui.step(6, PHASES, "Routing")
        log.event("info", "stage 12: routing/NAT")
        self.m.to(State.SHARING_CONFIGURING)
        ui.ok(f"Windows NATs hotspot clients into {ctx.tunnel_name} (tethering source = tunnel)")
        self._check_classic_ics(ctx, inv.ics_flags, inv.ics_flags_error)

        ui.step(7, PHASES, "Verify")
        return self._verify(ctx, tx, inv.ics_scope)

    def _check_classic_ics(self, ctx: Ctx, flags, error: str) -> None:
        """Classic ICS is not used by Mobile Hotspot, but another tool's classic
        sharing into the hotspot adapter would be a second, non-VPN path."""
        if flags is None:
            ui.warn(f"Could not read classic ICS settings: {error}")
            return
        found = ics_mod.conflicts(flags, ctx.tunnel_guid, ctx.hotspot_guid)
        if not found:
            ui.ok("No classic Internet Connection Sharing in the way")
            return
        for c in found:
            (ui.warn if c.kind != "leak" else ui.err)(c.detail[0].upper() + c.detail[1:])
        leaks = [c for c in found if c.kind == "leak"]
        if not leaks:
            return
        pick = self.decide("That classic sharing would let hotspot clients bypass the VPN. What now?", [
            Choice("clear", "Turn that classic sharing off", "Same as unticking 'Allow other network users…' in ncpa.cpl."),
            Choice("stop", "Stop the hotspot, leave it untouched")], 0, cancel="stop")
        if pick != "clear":
            raise Abort("", keep_vpn=True)
        guids = [f.guid for f in flags if f.public or f.private]
        ok, err_ = self.b.ics_disable(guids)
        if not ok:
            raise Abort(f"Could not turn classic sharing off: {err_}", keep_vpn=True)
        ui.ok("Classic sharing turned off")

    def _failure_context(self, ctx: Ctx) -> None:
        """Snapshot the radio/adapter state at the moment of a hotspot failure,
        so the next WiFiDeviceOff is diagnosable instead of guessed at."""
        try:
            ifaces, _ = self.b.wlan()
            for i in ifaces:
                ui.detail(f"Wi-Fi radio: software {i.radio_software}, hardware {i.radio_hardware} · {i.state}"
                          + (f" · uplink '{i.ssid}' channel {i.channel} ({i.band or '?'} GHz)" if i.connected else ""))
            inv = self.b.inventory(with_ics=False)
            from .netid import KIND_HOSTED, KIND_WIFI_DIRECT

            wfd = inv.of_kind(KIND_WIFI_DIRECT, KIND_HOSTED)
            if not wfd:
                ui.detail("No Microsoft Wi-Fi Direct Virtual Adapter exists - Mobile Hotspot cannot work without it.")
            for a in wfd:
                disabled = a.status.lower() == "disabled"
                ui.detail(f"{a.name} ({a.description}): {a.status}"
                          + (" <- disabled: enable it in Device Manager (View > Show hidden devices)" if disabled else ""))
            ui.detail(f"Requested: band {ctx.band}, {ctx.security.upper()}, source = VPN tunnel")
        except Exception as e:  # diagnostics must never mask the real failure
            log.event("error", f"failure context: {e}")

    def _vpn_source_ready(self, ctx: Ctx) -> bool:
        data, cap, level = None, "", ""
        with ui.task("Checking that Windows can share the VPN connection") as t:
            for i in range(12):
                data, e = self.b.hotspot_status(ctx.tunnel_guid, ctx.wifi.guid if ctx.wifi else "", "vpn")
                src = (data or {}).get("source") or {}
                cap, level = src.get("capability", ""), src.get("level", "")
                if src.get("kind") == "vpn" and cap == "Enabled":
                    break
                t.update("Waiting for Windows to classify the tunnel as an internet connection")
                self.sleep(1.0)
        if cap == "Enabled" and (data or {}).get("source", {}).get("kind") == "vpn":
            ui.ok("Windows can share the VPN connection directly")
            return True
        ui.warn(f"Windows does not offer the VPN as a hotspot source (capability '{cap or 'n/a'}', connectivity '{level or 'none'}').")
        return False

    def _verify(self, ctx: Ctx, tx: Transaction, scope: str) -> bool:
        s = ctx.settings
        log.event("info", "stage 13: verify gateway")
        with ui.task("Verifying the client gateway"):
            ha = self.b.adapter(guid=ctx.hotspot_guid) or {}
            data, _ = self.b.hotspot_status(ctx.tunnel_guid, "", "vpn")
        if (data or {}).get("state") != "On":
            raise Abort(f"The hotspot is no longer On (state {(data or {}).get('state')}).", keep_vpn=True)
        if scope in (ha.get("ipv4") or []):
            ui.ok(f"Client gateway {scope} (Windows DHCP + DNS proxy for hotspot clients)")
        else:
            ui.warn(f"Hotspot adapter address is {', '.join(ha.get('ipv4') or []) or 'none'}, expected {scope}.")

        log.event("info", "stage 14: verify DNS")
        dns = [] if ctx.external else ctx.profile.dns
        if ctx.external:
            ui.ok("NordVPN controls DNS; WireSpot leaves its resolver unchanged")
        elif not dns:
            ui.warn("The profile has no DNS server; clients will use whatever the host resolver uses.")
        elif ctx.protection == "strict" and ctx.profile.full_tunnel:
            ui.ok(f"DNS {', '.join(dns)} (non-tunnel DNS blocked by WireGuard's firewall)")
        elif s["behavior"].get("dns_lock", True):
            with ui.task("Locking DNS to the tunnel resolver"):
                d = self.b.dns_lock("add", [x for x in dns if ":" not in x] or dns)
            effective = any(str(r.get("namespace")) == "." for r in (d.get("effective") or []))
            if d.get("ok"):
                self.record.dns_lock = True
                self.m.persist()
                tx.add("share", "remove DNS lock", lambda: self.b.dns_lock("remove").get("ok", False))
                ui.ok(f"DNS locked to {', '.join(dns)} for this PC and hotspot clients" +
                      ("" if effective else " (policy not reported as effective yet)"))
            else:
                ui.warn(f"DNS lock failed: {d.get('error')}. Lookups may also go to the Wi-Fi router's DNS.")
        else:
            ui.warn("DNS lock is off: in balanced mode Windows may also query the Wi-Fi router's DNS.")

        log.event("info", "stage 15: verify client path")
        rc = self.b.route_check()
        if not rc.get("ok") or len(rc.get("routes") or []) < 2 or any(
                r.get("ifindex") != ctx.tunnel_ifindex for r in rc.get("routes", [])):
            raise Abort("Routing changed: internet traffic no longer resolves to the tunnel.", keep_vpn=True)
        if ctx.external and not forward_guard.installed():
            raise Abort("NordVPN forwarding guard is missing.", keep_vpn=True)
        with ui.task("Checking exit IP"):
            ctx.notes.append(self.b.public_ip())
        ui.ok("Path: hotspot → Windows NAT → " + ("NordVPN" if ctx.external else "WireGuard → Proton"))
        log.event("info", "stage 16: ready")
        self.record.ready_since = time.time()
        self.record.last_error = ""
        self.m.to(State.READY)
        return True

    # ------------------------------------------------------------ summaries
    def _ready_summary(self, ctx: Ctx) -> None:
        s = ctx.settings
        ip = ctx.notes[-1] if ctx.notes else ""
        ui.blank()
        ui.rule("READY")
        ui.kv("SSID", ui.color(s["hotspot"]["ssid"], ui.C.bold), 12)
        ui.kv("Password", ui.mask(s["hotspot"]["password"]) + "  ('show password' reveals it)", 12)
        ui.kv("Band", f"{hs_mod.BAND_LABEL.get(self.record.band or ctx.band, ctx.band)} · {ctx.security.upper()}", 12)
        ui.kv("VPN", "NordVPN · " + self.record.provider_protocol if ctx.external else ctx.profile.label, 12)
        ui.kv("Exit IP", ip or "unavailable", 12)
        ui.kv("Mode", ctx.protection + (" · DNS locked" if self.record.dns_lock else ""), 12)
        ui.rule()
        ui.hint("Connect your phone to the hotspot. 'clients' shows who is connected; 'stop' shuts everything down.")

    def _vpn_only_summary(self, ctx: Ctx) -> None:
        ui.blank()
        ui.ok(("NordVPN validated (managed in NordVPN app)" if ctx.external else
               f"VPN connected: {ctx.profile.label} ({ctx.protection})"))
        ui.hint("Hotspot not started. Run 'hotspot start' to share this VPN over Wi-Fi.")

    # ================================================================ hotspot-only paths
    @exclusive
    def hotspot_start(self, s: dict) -> bool:
        """'hotspot start' - share the already-connected VPN."""
        with self.lock:
            if not self.record.tunnel_name or self.m.state not in (State.VPN_CONNECTED, State.READY, State.ERROR):
                ui.warn("The VPN is not connected. Starting the hotspot now would share your normal, unprotected internet.")
                pick = self.decide("What should WireSpot do?", [
                    Choice("start", "Connect the VPN first, then start the hotspot"),
                    Choice("cancel", "Cancel")], 0, cancel="cancel")
                if pick == "start":
                    return self.start(s, None)
                return False
            return self.start(s, self.record.profile or None, StartOptions(reuse_vpn=True))

    @exclusive
    def hotspot_stop(self, s: dict) -> bool:
        with self.lock:
            self.stop_guard()
            ok_all = self._stop_sharing()
            if self.record.tunnel_name and self.m.state != State.DISCONNECTED:
                self._safe_to(State.VPN_CONNECTED)
            return ok_all

    @exclusive
    def bind(self, s: dict) -> bool:
        """Make sure a running hotspot shares the VPN. Mobile Hotspot switched on in
        Windows Settings shares Wi-Fi; restarting it from the tunnel fixes that."""
        with self.lock:
            if not self.record.tunnel_name:
                ui.err("No WireSpot VPN is connected. Use 'start'.")
                return False
            ui.info("Restarting Mobile Hotspot with the VPN tunnel as its source…")
            return self.start(s, self.record.profile or None, StartOptions(reuse_vpn=True))

    # ================================================================ stop
    def _stop_sharing(self) -> bool:
        ok_all = True
        if self.record.hotspot_guid:
            try:
                released = admission.release_guid(self.record.hotspot_guid)
                if released:
                    ui.ok(f"Device-approval holds released ({released})")
            except Exception as e:
                log.event("error", f"approval release: {e}")
        d = {"ok": True} if self.record.provider else self.b.dns_lock("remove")
        if d.get("removed"):
            ui.ok("DNS lock removed")
        elif not d.get("ok"):
            ui.warn(f"Could not check/remove the DNS lock: {d.get('error')}")
            ok_all = False
        self.record.dns_lock = False

        data, status_error = self.b.hotspot_status(self.record.tunnel_guid)
        if self.record.forward_guard and (status_error or not data):
            ui.err("Cannot verify whether Mobile Hotspot is off; forwarding guard remains active.")
            ok_all = False
        if data and data.get("state") not in ("Off", None):
            with ui.task("Stopping Mobile Hotspot"):
                r = self.b.hotspot_stop(self.record.tunnel_guid)
            if r.ok:
                ui.ok("Mobile Hotspot stopped")
            else:
                ui.err(f"Mobile Hotspot did not stop: {r.summary}")
                ok_all = False
        if self.record.no_connections_timeout_before:
            self.b.hotspot_timeout(True)
            self.record.no_connections_timeout_before = None

        if self.record.ics_guids:  # recorded by WireSpot 0.2.0, which still drove classic ICS
            ok, err_ = self.b.ics_disable(self.record.ics_guids)
            if ok:
                ui.ok("Classic ICS sharing left by WireSpot 0.2.0 cleared")
            else:
                ui.warn(f"Could not clear classic ICS sharing: {err_}")
                ok_all = False
            self.record.ics_guids, self.record.ics_journal, self.record.ics_changed = [], [], False
        if self.record.forward_guard:
            try:
                self._release_forward_guard()
            except Exception as e:
                ui.err(f"Forwarding guard remains active: {e}")
                ok_all = False
        self.record.hotspot_started_by_us = False
        self.record.hotspot_guid = ""
        self.record.ready_since = 0.0
        self.m.persist()
        return ok_all

    def _release_forward_guard(self) -> bool:
        if not self.record.forward_guard:
            return True
        data, error = self.b.hotspot_status(self.record.tunnel_guid)
        if error or not data or data.get("state") != "Off":
            raise forward_guard.ForwardGuardError("Mobile Hotspot has not been verified off")
        forward_guard.remove()
        self.record.forward_guard = False
        self.m.persist()
        return True

    @exclusive
    def stop(self, s: dict) -> bool:
        with self.lock:
            self.stop_guard()
            self._safe_to(State.STOPPING)
            ok_all = self._stop_sharing()
            ok_all = self.disconnect_vpn(s, quiet_if_none=True) and ok_all
            self._safe_to(State.DISCONNECTED if ok_all else State.ERROR, "" if ok_all else "stop incomplete")
            if ok_all:
                ui.ok("Everything WireSpot set up has been removed")
            return ok_all

    @exclusive
    def disconnect_vpn(self, s: dict, quiet_if_none: bool = False) -> bool:
        if self.record.provider == "nordvpn":
            if self.record.forward_guard:
                ui.err("NordVPN sharing guard is still active; stop the hotspot first.")
                return False
            self.record.tunnel_name = self.record.tunnel_guid = ""
            self.record.provider = self.record.provider_protocol = ""
            self.m.persist()
            return True
        from .wireguard import find_wireguard

        wgx = find_wireguard(s["vpn"].get("wireguard_path", ""))
        svcs, e = self.b.wg_services()
        ours = [x for x in svcs if x.ours]
        if not ours:
            if not quiet_if_none:
                ui.info("No WireSpot tunnel is installed.")
        ok_all = True
        for x in ours:
            if not wgx:
                ui.err("WireGuard is not installed; cannot remove tunnel services.")
                return False
            with ui.task(f"Disconnecting {x.name}"):
                r = self.b.wg_uninstall(wgx, x.name)
            if r.ok:
                self.b.remove_runtime(x.name)
                ui.ok(f"Disconnected {x.name}")
            else:
                ui.err(f"Could not remove {x.name}: {r.error_text()}")
                ok_all = False
        if ok_all:
            self.record.tunnel_name = self.record.tunnel_guid = self.record.runtime_conf = ""
            if self.m.state in (State.VPN_CONNECTED, State.ERROR):
                self._safe_to(State.DISCONNECTED)
            self.m.persist()
        return ok_all

    # ================================================================ guard
    def start_guard(self, s: dict | None = None) -> None:
        """Start the fail-closed guard (if enabled) and device approval (if enabled)."""
        s = s or settings_mod.load()[0]
        if self.guard is None and self.record.tunnel_name and (self.record.provider or s["behavior"].get("guard", True)):
            self.guard = Guard(self)
            self.guard.start()
        self.start_gate(s)

    def start_gate(self, s: dict | None = None) -> None:
        s = s or settings_mod.load()[0]
        if self.gate is None and self.gate_factory and s["behavior"].get("approve_devices", True):
            self.gate = self.gate_factory(self)
            self.gate.start()

    def stop_guard(self) -> None:
        if self.guard is not None:
            guard = self.guard
            guard.stop()
            self.guard = None
            if guard is not threading.current_thread():
                guard.join(timeout=5)
        if self.gate is not None:
            self.gate.stop()
            self.gate = None

    def reconcile_external(self, s: dict) -> bool:
        """On restart, re-arm protection without trusting a saved adapter index."""
        if self.record.provider != "nordvpn":
            return False
        if self.record.forward_guard:
            try:
                protected = forward_guard.installed()
            except Exception as e:
                protected = False
                log.event("error", f"[ProfileLess] Could not inspect forwarding guard: {e}")
            if not protected:
                self.b.hotspot_stop(self.record.tunnel_guid)
                self._safe_to(State.ERROR, "NordVPN forwarding guard was missing; hotspot stopped")
                return True
            if self.m.state in (State.READY, State.ERROR):
                self.start_guard(s)
        elif self.m.state == State.READY:
            self.b.hotspot_stop(self.record.tunnel_guid)
            self._safe_to(State.ERROR, "NordVPN forwarding guard was missing; hotspot stopped")
        return True


class Guard(threading.Thread):
    """While READY: fail closed if the VPN drops or ICS drifts off the tunnel;
    announce devices joining/leaving."""

    INTERVAL = 20.0

    def __init__(self, relay: Relay, notify: Callable[[], None] | None = None):
        super().__init__(daemon=True, name="wirespot-guard")
        self.relay = relay
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self.known: dict[str, clients_mod.Client] = {}
        self.enabled = True

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    def run(self) -> None:
        mutex = oplock.NamedMutex(oplock.GUARD)
        try:
            with network_events.NetworkEvents(self._wake) if self.relay.record.provider else _NoEvents():
                if self.relay.record.provider:
                    self._wake.set()
                self._loop(mutex)
        finally:
            mutex.close()

    def _loop(self, mutex) -> None:
        owned = False
        while not self._stopping.is_set():
            self._wake.wait(self.INTERVAL)
            self._wake.clear()
            if self._stopping.is_set():
                break
            if not owned:
                owned = mutex.acquire(0)       # another process may already be guarding
                if not owned:
                    continue
            if oplock.operation_busy() or not self.relay.lock.acquire(blocking=False):
                continue  # a start/stop is running somewhere
            try:
                self.relay.reload()
                if self.relay.m.state == State.READY:
                    self.tick()
                elif self.relay.record.provider == "nordvpn" and self.relay.m.state == State.ERROR:
                    self.recover()
            except Exception as e:
                log.event("error", f"guard: {e}")
            finally:
                self.relay.lock.release()

    def tick(self) -> None:
        r = self.relay
        rec = r.record
        if rec.provider == "nordvpn":
            try:
                if not forward_guard.installed():
                    return self.fail_closed("the NordVPN forwarding guard is missing")
                status = NordVPNProvider(r.b).detect(validate=False)
                if not status.adapter or status.adapter.guid != rec.tunnel_guid or not status.route_valid:
                    return self.fail_closed("NordVPN interface or route was lost")
            except Exception as e:
                return self.fail_closed(f"NordVPN validation failed: {e}")
        data = r.b.guard_tick(rec.tunnel_name, rec.hotspot_guid)
        if not data.get("ok"):
            if rec.provider:
                return self.fail_closed("Windows hotspot status could not be verified")
            return
        if not rec.provider and data.get("tunnel_state") != "Running":
            return self.fail_closed(f"the VPN tunnel service is {data.get('tunnel_state')}")
        hs_state = data.get("hotspot_state")
        if hs_state == "On" and data.get("flags") is not None:
            leaks = [c for c in ics_mod.conflicts(ics_mod.parse_flags(data), rec.tunnel_guid, rec.hotspot_guid)
                     if c.kind == "leak"]
            if leaks:
                return self.fail_closed(leaks[0].detail)
        if hs_state == "Off":
            ui.warn("Mobile Hotspot was turned off outside WireSpot. The VPN is still connected ('hotspot start' to resume).")
            r._notify("hotspot_off", "Mobile Hotspot was turned off outside WireSpot. The VPN is still connected.")
            r.record.hotspot_started_by_us = False
            r._safe_to(State.VPN_CONNECTED)
            _redraw()
            return
        current = {c.mac: c for c in clients_mod.merge(data)}
        for mac, c in current.items():
            if mac not in self.known:
                ui.info(f"Device joined: {c.display_name} · {c.ip or 'no IP yet'} · {c.device}")
                r._notify("joined", f"{c.display_name} · {c.ip or 'no IP yet'} · {c.device}")
                _redraw()
        for mac, c in self.known.items():
            if mac not in current:
                ui.info(f"Device left: {c.display_name} · {c.ip or mac}")
                r._notify("left", f"{c.display_name} · {c.ip or mac}")
                _redraw()
        self.known = current

    def fail_closed(self, reason: str) -> None:
        r = self.relay
        ui.blank()
        ui.err(f"Guard: {reason}.")
        ui.warn("VPN connection lost. Connected-device internet is suspended to prevent fallback." if r.record.provider
                else "Stopping the hotspot so no device can reach the internet outside the VPN.")
        result = r.b.hotspot_stop(r.record.tunnel_guid)
        if not result.ok:
            log.event("error", "[ProfileLess] Hotspot stop failed; forwarding guard must remain active")
        r.record.hotspot_started_by_us = False
        r._safe_to(State.ERROR, reason)
        ui.hint("Waiting for NordVPN…" if r.record.provider else "Run 'status' to inspect, then 'start' to rebuild.")
        r._notify("fail", f"{reason}. Connected-device internet is suspended to prevent fallback.")
        _redraw()
        if not r.record.provider:
            self.stop()

    def recover(self) -> None:
        r = self.relay
        if not r.record.forward_guard:
            return
        status = NordVPNProvider(r.b).detect(validate=True)
        if not status.ready or not status.adapter:
            return
        log.event("info", "[ProfileLess] NordVPN restored; starting revalidation and hosting")
        self.stop()
        threading.Thread(target=lambda: r.start(settings_mod.load()[0], None),
                         daemon=True, name="wirespot-nordvpn-recover").start()


class _NoEvents:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


_redraw_hook: Callable[[], None] | None = None


def set_redraw_hook(fn: Callable[[], None]) -> None:
    global _redraw_hook
    _redraw_hook = fn


def _redraw() -> None:
    if _redraw_hook:
        try:
            _redraw_hook()
        except Exception:
            pass
