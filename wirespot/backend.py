"""System backend: the only layer that touches Windows. The relay engine
talks to this interface, so its state machine and rollback can be tested
with a fake backend."""
from __future__ import annotations

import json
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_NAME, OWNER_TAG, VERSION, clients, hotspot, ics, netid, paths, profiles, scripts, winexec, wireguard, wlan


@dataclass
class EnvReport:
    windows: bool
    build: int
    admin: bool
    wireguard: Path | None
    wg: Path | None
    problems: list[str] = field(default_factory=list)


class Backend:
    def env(self, wireguard_path: str = "", require_wireguard: bool = True) -> EnvReport:
        is_win = sys.platform == "win32"
        build = sys.getwindowsversion().build if is_win else 0
        wgx = wireguard.find_wireguard(wireguard_path) if is_win else None
        rep = EnvReport(is_win, build, winexec.is_admin(), wgx, wireguard.find_wg(wgx))
        if not is_win:
            rep.problems.append("WireSpot only runs on Windows 10/11.")
        if is_win and build < 17763:
            rep.problems.append(f"Windows build {build} is too old (need Windows 10 1809+).")
        if not rep.admin:
            rep.problems.append("Administrator rights are required.")
        if require_wireguard and not wgx:
            rep.problems.append("WireGuard for Windows is not installed (https://www.wireguard.com/install/).")
        return rep

    # -- Wi-Fi -----------------------------------------------------------
    def wlan(self) -> tuple[list[wlan.WlanInterface], str]:
        try:
            ifaces = wlan.query_interfaces()
        except OSError as e:
            return [], str(e)
        # netsh gives an explicit "Band" (disambiguates 6 GHz) on English systems.
        r = winexec.run(["netsh", "wlan", "show", "interfaces"], timeout=10, name="netsh-wlan")
        hints = wlan.parse_netsh_interfaces(r.stdout) if r.returncode == 0 else {}
        for i in ifaces:
            i.band_hint = hints.get(i.guid.lower(), {}).get("band", "")
        return ifaces, ""

    def uplink(self) -> list[dict]:
        """Non-tunnel interfaces with an IPv4 default route, best first."""
        r = winexec.powershell(scripts.UPLINK_PS, name="uplink", timeout=40)
        return list((r.data or {}).get("uplinks") or []) if isinstance(r.data, dict) else []

    # -- inventory -------------------------------------------------------
    def inventory(self, with_ics: bool = True) -> netid.Inventory:
        r = winexec.powershell(scripts.INVENTORY_PS, ["-WithIcs"] if with_ics else [], name="inventory", timeout=90)
        if not isinstance(r.data, dict):
            inv = netid.Inventory(error=r.error_text())
            return inv
        return netid.build_inventory(r.data)

    def adapter(self, name: str = "", guid: str = "") -> dict | None:
        r = winexec.powershell(scripts.ADAPTER_PS, ["-Name", name, "-Guid", guid], name="adapter", timeout=30)
        return r.data.get("adapter") if isinstance(r.data, dict) else None

    # -- WireGuard -------------------------------------------------------
    def wg_services(self):
        return wireguard.services()

    def wg_install(self, wgx: Path, conf: Path) -> winexec.Result:
        return wireguard.install(wgx, conf)

    def wg_uninstall(self, wgx: Path, name: str) -> winexec.Result:
        return wireguard.uninstall(wgx, name)

    def wg_handshake(self, wg: Path, name: str, timeout: float = 20.0):
        return wireguard.wait_handshake(wg, name, timeout)

    def wg_show(self, wg: Path, name: str):
        return wireguard.show(wg, name)

    def write_runtime(self, profile: profiles.Profile, protection: str) -> tuple[Path, str]:
        return profiles.write_runtime_config(profile, protection, paths.RUNTIME_DIR)

    def remove_runtime(self, name: str) -> None:
        profiles.remove_runtime_config(paths.RUNTIME_DIR, name)
        profiles.remove_runtime_config(paths.LEGACY_RUNTIME_DIR, name)

    # -- hotspot ---------------------------------------------------------
    def hotspot_status(self, tunnel_guid: str = "", wifi_guid: str = "", source: str = "any"):
        return hotspot.status(tunnel_guid, wifi_guid, source)

    def hotspot_start(self, **kw) -> hotspot.HotspotResult:
        return hotspot.start(**kw)

    def hotspot_stop(self, tunnel_guid: str = "", wifi_guid: str = "") -> hotspot.HotspotResult:
        return hotspot.stop(tunnel_guid, wifi_guid)

    def hotspot_timeout(self, enable: bool) -> dict:
        """Enable/disable Windows' 'turn off when no devices are connected'. Returns
        {'ok', 'before', 'after'}."""
        r = winexec.powershell(scripts.HOTSPOT_TIMEOUT_PS, ["-Enable"] if enable else [],
                               name="hotspot-timeout", winrt=True, timeout=45)
        return r.data if isinstance(r.data, dict) else {"ok": False, "error": r.error_text()}

    def guard_tick(self, tunnel: str, hotspot_guid: str) -> dict:
        r = winexec.powershell(scripts.GUARD_PS, ["-Tunnel", tunnel, "-HotspotGuid", hotspot_guid],
                               name="guard", winrt=True, timeout=45)
        return r.data if isinstance(r.data, dict) else {"ok": False, "error": r.error_text()}

    def clients(self, hotspot_guid: str, resolve: bool = True):
        return clients.fetch(hotspot_guid, resolve)

    # -- ICS -------------------------------------------------------------
    def ics_list(self):
        return ics.list_connections()

    def ics_flags(self):
        return ics.flags()

    def ics_disable(self, guids: list[str]) -> tuple[bool, str]:
        return ics.disable(guids)

    # -- DNS / routing ---------------------------------------------------
    def dns_lock(self, action: str, servers: list[str] | None = None) -> dict:
        r = winexec.powershell(scripts.DNS_LOCK_PS, ["-Action", action, "-Servers", ",".join(servers or []),
                                                     "-Tag", OWNER_TAG], name=f"dns-{action}", timeout=45)
        return r.data if isinstance(r.data, dict) else {"ok": False, "error": r.error_text()}

    def route_check(self, targets=("1.1.1.1", "9.9.9.9")) -> dict:
        r = winexec.powershell(scripts.ROUTE_CHECK_PS, ["-Targets", ",".join(targets)], name="route-check", timeout=30)
        return r.data if isinstance(r.data, dict) else {"ok": False, "error": r.error_text()}

    def public_ip(self) -> str:
        for url in ("https://api.ipify.org?format=json", "https://api64.ipify.org?format=json"):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{VERSION}"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    return json.loads(resp.read().decode("utf-8")).get("ip", "")
            except Exception:
                continue
        return ""
