"""Diagnostics: doctor [quick|os|wifi|vpn|hotspot|ics|network|clients|full] [save]

Designed so one run on the affected machine answers the questions that
otherwise need guessing: which adapter is which (by GUID), what ICS can
see, what the tethering API reports, and whether the tunnel really carries
traffic. Never prints private keys or the hotspot password.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

from . import APP_NAME, VERSION, hotspot as hs_mod, ics as ics_mod
from . import paths, profiles, scripts, ui, winexec, wireguard, wlan
from . import settings as settings_mod
from .netid import (KIND_HOSTED, KIND_WAN_MINIPORT, KIND_WIFI, KIND_WIFI_DIRECT, Inventory,
                    correlate, locate_hotspot)

SECTIONS = ("os", "wifi", "vpn", "hotspot", "ics", "network", "clients")
UNSAFE_SIDS = {"S-1-5-32-545": "Users", "S-1-1-0": "Everyone", "S-1-5-11": "Authenticated Users"}


class _Cache:
    def __init__(self, backend):
        self.b = backend
        self._inv: Inventory | None = None
        self._wlan = None
        self._hs = None

    def inventory(self) -> Inventory:
        if self._inv is None:
            with ui.task("Collecting adapters, routes, DNS and ICS"):
                self._inv = self.b.inventory(with_ics=True)
        return self._inv

    def wlan(self):
        if self._wlan is None:
            self._wlan = self.b.wlan()
        return self._wlan

    def hotspot(self, tunnel_guid: str = ""):
        if self._hs is None:
            with ui.task("Querying Mobile Hotspot"):
                self._hs = self.b.hotspot_status(tunnel_guid=tunnel_guid, source="any")
        return self._hs


def run(backend, settings: dict, args: list[str], relay=None) -> None:
    args = [a.lower() for a in args]
    save = "save" in args
    args = [a for a in args if a != "save"]
    what = args[0] if args else "quick"
    if what not in SECTIONS + ("full", "quick", "all"):
        ui.err(f"Unknown doctor section '{what}'. Use: {', '.join(SECTIONS)}, full.")
        return
    cache = _Cache(backend)
    if save:
        ui.capture_begin()
    t0 = time.monotonic()
    try:
        ui.heading(f"{APP_NAME} doctor · {what} · {datetime.now():%Y-%m-%d %H:%M:%S}")
        if what == "quick":
            _quick(cache, settings, relay)
        else:
            order = SECTIONS if what in ("full", "all") else (what,)
            for sec in order:
                globals()[f"_sec_{sec}"](cache, settings, relay)
        ui.blank()
        ui.hint(f"doctor finished in {time.monotonic() - t0:.1f}s")
    finally:
        if save:
            lines = ui.capture_end()
            paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
            out = paths.LOG_DIR / f"doctor-{what}-{datetime.now():%Y%m%d-%H%M%S}.txt"
            from . import log

            out.write_text(log.redact("\n".join(lines)) + "\n", encoding="utf-8")
            ui.ok(f"Report saved: {out}")


def _tunnel(relay) -> tuple[str, str]:
    if relay and relay.record.tunnel_name:
        return relay.record.tunnel_name, relay.record.tunnel_guid
    return "", ""


# ---------------------------------------------------------------- quick
def _quick(cache: _Cache, s: dict, relay) -> None:
    b = cache.b
    env = b.env(s["vpn"].get("wireguard_path", ""))
    _check(env.admin, "Administrator", "yes" if env.admin else "no - relaunch elevated")
    _check(bool(env.wireguard), "WireGuard for Windows", str(env.wireguard or "not installed"))
    profs, bad = profiles.list_profiles(paths.VPN_DIR)
    _check(bool(profs), "VPN profiles", f"{len(profs)} valid, {len(bad)} rejected in {paths.VPN_DIR}")
    problems = settings_mod.validate_hotspot(s)
    _check(not problems, "Hotspot settings", "valid" if not problems else "; ".join(problems))
    ifaces, e = cache.wlan()
    wifi = next((i for i in ifaces if i.connected), None)
    up = b.uplink()
    from .netid import uplink_kind

    if up:
        kind = uplink_kind(up[0])
        extra = f" · {wifi.ssid} · {wifi.band or '?'} GHz · ch {wifi.channel}" if (wifi and kind == "Wi-Fi") else f" · {up[0]['name']}"
        _check(True, "Uplink", kind + extra)
    else:
        _check(False, "Uplink", "no default route - not connected to any network")
    radio = next(iter(ifaces), None)
    _check(bool(radio and radio.radio_on), "Wi-Fi radio (hotspot)",
           f"{radio.description} · {'on' if radio.radio_on else 'OFF'}" if radio else (e or "no Wi-Fi adapter"))
    data, err_ = cache.hotspot(_tunnel(relay)[1])
    cap = ((data or {}).get("source") or {}).get("capability", err_ or "unknown")
    _check(cap == "Enabled", "Tethering capability", cap)
    if data:
        band = s["hotspot"]["band"]
        plan = hs_mod.plan_band(band, wifi.band if wifi else "", hs_mod.supported_bands(data))
        _check(plan.ok, f"Band '{band}'", "supported" if plan.ok else plan.reason)
    prot = s["vpn"]["protection"]
    full = any(p.full_tunnel for p in profs)
    _check(not (prot == "strict" and full), "Protection vs hotspot",
           "balanced: hotspot-compatible" if prot == "balanced" else
           "strict + /0 profile: WireGuard kill-switch blocks hotspot DHCP/DNS (start will offer balanced)")
    if relay:
        ui.kv("State", relay.m.state.value + (f" · last error: {relay.record.last_error}" if relay.record.last_error else ""))
    ui.hint("More: doctor full · doctor hotspot · doctor ics · add 'save' to write a report file")


def _check(ok: bool, label: str, value: str) -> None:
    (ui.ok if ok else ui.warn)(f"{label}: {value}")


# ---------------------------------------------------------------- os
def _sec_os(cache: _Cache, s: dict, relay) -> None:
    ui.rule("OS")
    env = cache.b.env(s["vpn"].get("wireguard_path", ""))
    files = [str(p) for p in (env.wireguard, env.wg) if p]
    r = winexec.powershell(scripts.SYSINFO_PS, ["-Files", "|".join(files)], name="sysinfo", timeout=30)
    d = r.data or {}
    build = int(d.get("build") or env.build or 0)
    product = d.get("product", "Windows")
    if build >= 22000:
        product = product.replace("Windows 10", "Windows 11")
    ui.kv("Windows", f"{product} {d.get('display', '')} ({d.get('edition', '')})")
    ui.kv("Build", f"{build}.{d.get('ubr', '')}")
    ui.kv("Administrator", "yes" if env.admin else "NO")
    ui.kv("PowerShell", d.get("ps", "?"))
    ui.kv(APP_NAME, f"{VERSION} · {'frozen exe' if getattr(sys, 'frozen', False) else 'python ' + sys.version.split()[0]}")
    ui.kv("Program folder", paths.APP_DIR)
    ui.kv("Data folder", paths.BASE)
    ui.kv("Runtime folder", paths.RUNTIME_DIR)
    for f in d.get("files") or []:
        ui.kv(Path(f["path"]).name, f"{f['version']} · {f['path']}")
    if build < 19041:
        ui.warn("Hotspot band selection needs Windows 10 2004 (19041)+.")
    if build < 26100 and s["hotspot"]["security"] != "wpa2":
        ui.warn("WPA3 hotspot selection needs Windows 11 24H2 (26100)+.")
    _acl_report([paths.RUNTIME_DIR, paths.LEGACY_RUNTIME_DIR])


def _acl_report(dirs: list[Path]) -> None:
    r = winexec.powershell(scripts.ACL_PS, ["-Paths", "|".join(str(d) for d in dirs)], name="acl", timeout=30)
    for item in (r.data or {}).get("items", []):
        if not item.get("exists"):
            continue
        exposed = sorted({UNSAFE_SIDS[a["sid"]] for a in item.get("aces", []) if a.get("sid") in UNSAFE_SIDS})
        files = item.get("files") or []
        label = f"{item['path']} ({len(files)} file{'s' if len(files) != 1 else ''})"
        if exposed:
            ui.err(f"Runtime configs readable by {', '.join(exposed)}: {label}")
            ui.hint("These files contain WireGuard private keys. WireSpot hardens this folder on startup; 'stop' deletes them.")
        else:
            ui.ok(f"Private-key folder restricted to SYSTEM/Administrators: {label}")


# ---------------------------------------------------------------- wifi
def _sec_wifi(cache: _Cache, s: dict, relay) -> None:
    ui.rule("Physical Wi-Fi")
    ifaces, e = cache.wlan()
    if e:
        ui.err(f"WLAN API: {e}")
    for i in ifaces:
        ui.kv("Adapter", i.description)
        ui.kv("GUID", "{" + i.guid + "}")
        ui.kv("State", i.state)
        ui.kv("Radio", f"software {i.radio_software} · hardware {i.radio_hardware}")
        if i.connected:
            ui.kv("Connected to", f"{i.ssid} (profile {i.profile})")
            ui.kv("Channel / band", f"{i.channel} · {i.band or '?'} GHz" + (f" (netsh: {i.band_hint})" if i.band_hint else ""))
            ui.kv("Signal / rate", f"{i.signal}% · {i.rx_mbps:.0f} Mbps")
        for x in i.errors:
            ui.warn(f"WLAN query: {x}")
    r = winexec.run(["netsh", "wlan", "show", "drivers"], timeout=15, name="netsh-drivers")
    for d in wlan.parse_netsh_drivers(r.stdout):
        ui.kv("Driver", f"{d.driver} · {d.vendor}")
        ui.kv("Driver version", f"{d.version} ({d.date})")
        ui.kv("Radio types", d.radio_types)
        ui.kv("Supported bands", ", ".join(d.bands) or "(netsh did not list)")
        ui.kv("Hosted network", d.hosted_network + " (legacy API; Mobile Hotspot uses Wi-Fi Direct instead)")
        ui.kv("WPA3-Personal", "yes" if d.wpa3_personal else "no")
    if r.returncode != 0 or not r.stdout.strip():
        ui.warn("netsh wlan show drivers returned nothing (WLAN AutoConfig stopped?)")
    inv = cache.inventory()
    ui.blank()
    ui.info("Wi-Fi related adapters (Get-NetAdapter -IncludeHidden)")
    _adapter_table([a for a in inv.adapters if a.kind in (KIND_WIFI, KIND_WIFI_DIRECT, KIND_HOSTED)])
    wifi = next((i for i in ifaces if i.connected), None)
    if wifi and wifi.band:
        ui.hint(f"Uplink on {wifi.band} GHz. A hotspot on the other band works on many adapters (time-slicing); "
                f"if it fails with WiFiDeviceOff, use band auto.")


def _adapter_table(adapters) -> None:
    rows = [[a.name, a.description[:40], a.status, "{" + a.guid + "}", str(a.ifindex), a.mac or "-",
             ", ".join(a.ipv4) or "-", a.kind] for a in adapters]
    if rows:
        ui.table(["Name", "Description", "Status", "GUID", "ifIdx", "MAC", "IPv4", "Kind"], rows)
    else:
        ui.detail("(none)")


# ---------------------------------------------------------------- vpn
def _sec_vpn(cache: _Cache, s: dict, relay) -> None:
    ui.rule("WireGuard")
    env = cache.b.env(s["vpn"].get("wireguard_path", ""))
    ui.kv("wireguard.exe", env.wireguard or "NOT FOUND")
    ui.kv("wg.exe", env.wg or "NOT FOUND")
    svcs, e = cache.b.wg_services()
    if e:
        ui.warn(f"Service query: {e}")
    if not svcs:
        ui.info("No WireGuard tunnel services installed.")
    inv = cache.inventory()
    for x in svcs:
        owner = "WireSpot" if x.ours else "other software"
        ui.blank()
        (ui.ok if x.running else ui.warn)(f"Tunnel {x.name} · {x.state} · start {x.start_mode} · owner: {owner}")
        if x.name.startswith("pr_"):
            ui.detail("Created by ProtonRelay 0.1.x; 'stop' removes it.")
        ad = inv.by_name(x.name)
        if ad:
            ui.detail(f"Adapter {{{ad.guid}}} · ifIndex {ad.ifindex} · {ad.status} · IPv4 {', '.join(ad.ipv4) or '-'} · DNS {', '.join(ad.dns) or '-'}")
            routes = [r.prefix for r in inv.routes if r.ifindex == ad.ifindex]
            ui.detail(f"Default routes: {', '.join(routes) or 'none'}")
            if "0.0.0.0/0" in routes:
                ui.detail("Kill-switch mode (/0): WireGuard firewall blocks non-tunnel traffic incl. hotspot DHCP/DNS.")
            elif "0.0.0.0/1" in routes:
                ui.detail("Balanced mode (/1 + /1): hotspot-compatible.")
        if x.running and env.wg:
            st, err_ = wireguard.show(env.wg, x.name)
            if st:
                ui.detail(f"Interface public key {st.public_key} · listen port {st.listen_port}")
                for p in st.peers:
                    ui.detail(f"Peer {p.public_key[:10]}… endpoint {p.endpoint} · handshake {wireguard.fmt_age(p.handshake_age())}"
                              f" · rx {wireguard.fmt_bytes(p.rx)} · tx {wireguard.fmt_bytes(p.tx)} · keepalive {p.keepalive}")
                    ui.detail(f"Allowed IPs {', '.join(p.allowed_ips)}")
            else:
                ui.warn(f"wg show: {err_}")
    ui.blank()
    profs, bad = profiles.list_profiles(paths.VPN_DIR)
    ui.info(f"Profiles in {paths.VPN_DIR}: {len(profs)} valid")
    for i, p in enumerate(profs, 1):
        ui.detail(f"{i}. {p.label} · {'full tunnel' if p.full_tunnel else 'split'} · tunnel name {profiles.tunnel_name(p.path)}")
    for path, why in bad:
        ui.warn(f"Rejected {path.name}: {why}")
    _acl_report([paths.RUNTIME_DIR, paths.LEGACY_RUNTIME_DIR])


# ---------------------------------------------------------------- hotspot
def _sec_hotspot(cache: _Cache, s: dict, relay) -> None:
    ui.rule("Mobile Hotspot")
    data, e = cache.hotspot(_tunnel(relay)[1])
    if not data:
        ui.err(f"Tethering API unavailable: {e}")
        return
    if e:
        ui.warn(e)
    for p in data.get("profiles") or []:
        ui.kv(f"Source '{p['name']}'", f"{p['kind']} · connectivity {p['level']} · capability {p['capability']}", 30)
    ui.kv("Operational state", data.get("state"))
    ui.kv("Clients", f"{data.get('clients')} / {data.get('max_clients')}")
    ap = data.get("ap") or {}
    if ap:
        ui.kv("Configured SSID", ap.get("ssid", ""))
        ui.kv("Configured band", hs_mod.BAND_LABEL.get(hs_mod.BAND_NAMES.get(ap.get("band", ""), ""), ap.get("band", "-")))
        if ap.get("auth"):
            ui.kv("Configured security", ap["auth"])
    elif data.get("ap_error"):
        ui.warn(f"GetCurrentAccessPointConfiguration: {data['ap_error']}")
    bands = data.get("bands") or {}
    ui.kv("Band support", ", ".join(f"{hs_mod.BAND_LABEL.get(hs_mod.BAND_NAMES.get(k, k), k)}={v}" for k, v in bands.items()) or "API not available")
    kinds = data.get("auth_kinds") or {}
    ui.kv("Auth kinds", ", ".join(f"{k}={v}" for k, v in kinds.items()) or "WPA2 only (selection needs build 26100+)")
    if "no_connections_timeout" in data:
        ui.kv("Idle auto-off", "enabled" if data["no_connections_timeout"] else "disabled")
    ifaces, _ = cache.wlan()
    wifi = next((i for i in ifaces if i.connected), None)
    plan = hs_mod.plan_band(s["hotspot"]["band"], wifi.band if wifi else "", hs_mod.supported_bands(data))
    (ui.ok if plan.ok else ui.warn)(f"Configured band '{s['hotspot']['band']}': " + ("supported" if plan.ok else plan.reason))
    if plan.note:
        ui.detail(plan.note)
    inv = cache.inventory()
    wfd = inv.of_kind(KIND_WIFI_DIRECT, KIND_HOSTED)
    if not wfd:
        ui.err("No Microsoft Wi-Fi Direct Virtual Adapter exists; Mobile Hotspot needs it.")
    for a in wfd:
        if a.status.lower() == "disabled":
            ui.err(f"{a.name} ({a.description}) is DISABLED - enable it in Device Manager (View > Show hidden devices).")
    if relay and relay.record.last_error:
        ui.kv("Last WireSpot error", relay.record.last_error)
    ui.hint("WiFiDeviceOff = Windows could not start the Wi-Fi access-point role (radio off, Wi-Fi Direct adapter disabled, "
            "band the driver refuses next to the uplink, or a transient network change).")


# ---------------------------------------------------------------- ics
def _sec_ics(cache: _Cache, s: dict, relay) -> None:
    ui.rule("Sharing / NAT")
    inv = cache.inventory()
    for name in ("icssvc", "SharedAccess"):
        ui.kv(name, inv.services.get(name, "not found"))
    ui.kv("Client subnet gateway", inv.ics_scope)
    ui.hint("Mobile Hotspot's NAT/DHCP/DNS is run by icssvc from the connection the hotspot was started from.")
    ui.hint("WireSpot starts it from the WireGuard tunnel; classic ICS (below) is not used and should be empty.")
    tname, tguid = _tunnel(relay)
    if relay and relay.record.hotspot_started_by_us:
        ui.kv("Hotspot source", f"{tname} (recorded by WireSpot)")
    match = locate_hotspot(inv)
    ui.blank()
    if inv.ics_flags is None:
        ui.warn(f"Classic ICS flags unreadable: {inv.ics_flags_error}")
    elif not inv.ics_flags:
        ui.ok("Classic ICS: no connection is shared (expected)")
    else:
        ui.info("Classic ICS flags (root\\Microsoft\\HomeNet):")
        for f in inv.ics_flags:
            role = " + ".join(r for r, on in (("public", f.public), ("private", f.private)) if on)
            ui.detail(f"{f.name or '(unknown)'} {{{f.guid}}} · {role}" + ("" if f.exists else " · ADAPTER NO LONGER EXISTS"))
        for c in ics_mod.conflicts(inv.ics_flags, tguid, match.adapter.guid if match.adapter else ""):
            (ui.err if c.kind == "leak" else ui.warn)(c.detail)
    if inv.ics is None:
        ui.hint(f"HNetShare enumeration: {inv.ics_error or 'not available'}")
    else:
        ui.blank()
        ui.info(f"HNetShare connections ({len(inv.ics)}) - identity by GUID")
        for n, c in enumerate(inv.ics, 1):
            ad = inv.by_guid(c.guid)
            ui.detail(f"[{n}] {c.name or '(no name)'} {{{c.guid}}} · sharing {c.sharing}"
                      + (f" · ifIndex {ad.ifindex} · {ad.status}" if ad else " · no Get-NetAdapter match")
                      + (f" · error {c.error}" if c.error else ""))
        pairs, _ = correlate(inv)
        missing = [p.adapter for p in pairs if p.ics is None]
        if missing:
            ui.warn("Adapters not exposed to HNetShare: " + ", ".join(f"{a.name} ({a.kind})" for a in missing))
    lac_wan = [a for a in inv.adapters if a.kind == KIND_WAN_MINIPORT and a.name.lower().startswith("local area connection")]
    if lac_wan:
        ui.blank()
        ui.info("'Local Area Connection* N' names that are WAN Miniports (never the hotspot):")
        ui.detail(", ".join(f"{a.name} = {a.description}" for a in lac_wan))
    ui.blank()
    if match.adapter:
        ui.ok(f"Hotspot adapter by identity: {match.adapter.name} {{{match.adapter.guid}}} ({', '.join(match.reasons)})")
    else:
        ui.info({"none_up": "No Wi-Fi Direct virtual adapter is Up (hotspot off?).",
                 "no_virtual_adapter": "No Wi-Fi Direct virtual adapter exists.",
                 "ambiguous": "Several virtual adapters qualify; see 'network adapters'."}.get(match.problem, match.problem))
    d = cache.b.dns_lock("list")
    ours = [r for r in d.get("rules") or [] if r.get("ours")]
    ui.kv("DNS lock (NRPT)", f"{len(ours)} WireSpot rule(s)" + (f" → {', '.join(ours[0]['servers'])}" if ours else ""))


# ---------------------------------------------------------------- network
def _sec_network(cache: _Cache, s: dict, relay) -> None:
    ui.rule("Routing and DNS")
    inv = cache.inventory()
    rows = []
    for r in sorted(inv.routes, key=lambda r: (r.prefix, r.metric)):
        a = inv.by_ifindex(r.ifindex)
        rows.append([r.prefix, a.name if a else str(r.ifindex), r.nexthop, str(r.metric)])
    ui.table(["Prefix", "Interface", "Next hop", "Metric"], rows)
    ui.blank()
    rows = [[a.name, ", ".join(a.dns)] for a in inv.adapters if a.dns and a.up]
    ui.table(["Interface", "DNS servers"], rows)
    rc = cache.b.route_check()
    for r in rc.get("routes", []):
        ui.kv(f"Route to {r.get('target')}", r.get("alias") or r.get("error"))
    with ui.task("Checking public IP"):
        ip = cache.b.public_ip()
    ui.kv("Public IP", ip or "unavailable")


# ---------------------------------------------------------------- clients
def _sec_clients(cache: _Cache, s: dict, relay) -> None:
    ui.rule("Hotspot clients")
    guid = relay.record.hotspot_guid if relay and relay.record.hotspot_guid else ""
    if not guid:
        m = locate_hotspot(cache.inventory())
        guid = m.adapter.guid if m.adapter else ""
    show_clients(cache.b, guid)


def show_clients(backend, hotspot_guid: str) -> None:
    with ui.task("Looking up connected devices"):
        cl, warn_, _ = backend.clients(hotspot_guid, True)
    if warn_:
        ui.warn(warn_)
    if not cl:
        ui.info("No devices connected." if hotspot_guid else "Hotspot is not running.")
        return
    from . import admission, settings as settings_mod

    store = admission.load()
    on = settings_mod.load()[0]["behavior"].get("approve_devices", True)
    here = {c.mac for c in cl}
    for mac, p in list(store["pending"].items()) + list(store["blocked"].items()):
        if mac not in here and p.get("ips"):
            from .clients import Client

            cl.append(Client(mac=mac, ip=p["ips"][-1]))
    rows = [[c.ip or "-", c.display_name, c.device, c.mac + (" (private)" if c.randomized else ""),
             (admission.status_of(c.mac, store) if on else "allowed").replace("approved", "allowed")
             .replace("pending", "WAITING")] for c in cl]
    ui.table(["IP", "Name", "Device (guess)", "MAC", "Access"], rows)
    if on and any(r[4] == "WAITING" for r in rows):
        ui.hint("Waiting devices have no network until you run 'allow' (or 'block' to stop being asked).")
    ui.hint("Device type is inferred from the host name and MAC vendor; phones with private MACs hide their vendor.")
