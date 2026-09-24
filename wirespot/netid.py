"""Adapter identity and correlation across Windows networking APIs.

Root cause of the v0.1.1 ICS failure: the old discovery picked any *Up*
adapter named "Local Area Connection*". Windows gives that generated name to
WAN Miniports as well ("Local Area Connection* 11" = WAN Miniport (IPv6) on
the reporting machine). Those are hidden, never exposed to ICS, and have
nothing to do with the hotspot. The real hotspot adapter is a "Microsoft
Wi-Fi Direct Virtual Adapter" (ComponentID ...\\vwifimp_wfd), and it only
comes Up once tethering is actually On - which it never was (WiFiDeviceOff).

Identity is therefore established by *kind* (driver component/description),
*state* (Up, carrying the ICS scope address) and *GUID*, which is the one
value shared by Get-NetAdapter (InterfaceGuid), WinRT (NetworkAdapterId) and
HNetCfg (INetConnectionProps.Guid).
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

KIND_WIFI = "wifi"
KIND_WIFI_DIRECT = "wifi-direct"
KIND_HOSTED = "hosted-network"
KIND_WIREGUARD = "wireguard"
KIND_WAN_MINIPORT = "wan-miniport"
KIND_ETHERNET = "ethernet"
KIND_OTHER = "other"


def norm_guid(g) -> str:
    return str(g or "").strip().strip("{}").lower()


def classify(description: str, component: str, media: str, hardware: bool) -> str:
    d = (description or "").lower()
    c = (component or "").lower()
    m = (media or "").lower()
    # NdisPhysicalMedium is sometimes reported as its numeric enum value.
    m = {"9": "native 802.11", "1": "wireless lan", "14": "802.3"}.get(m, m)
    if "vwifimp_wfd" in c or d.startswith("microsoft wi-fi direct virtual adapter"):
        return KIND_WIFI_DIRECT
    if "hosted network virtual adapter" in d or "vwifimp" in c:
        return KIND_HOSTED
    if d.startswith("wireguard tunnel"):
        return KIND_WIREGUARD
    if d.startswith("wan miniport"):
        return KIND_WAN_MINIPORT
    if hardware and ("802.11" in m or "wireless" in m or "wi-fi" in d or "wireless" in d or "wlan" in d):
        return KIND_WIFI
    if hardware and ("802.3" in m or "ethernet" in d or "gbe" in d):
        return KIND_ETHERNET
    return KIND_OTHER


@dataclass
class Adapter:
    name: str
    description: str
    status: str
    ifindex: int
    guid: str
    mac: str = ""
    hardware: bool = False
    virtual: bool = False
    hidden: bool = False
    component: str = ""
    driver_version: str = ""
    driver_provider: str = ""
    driver_date: str = ""
    media: str = ""
    ipv4: list[str] = field(default_factory=list)
    dns: list[str] = field(default_factory=list)
    forwarding: str = ""
    metric: int | None = None
    kind: str = KIND_OTHER

    @property
    def up(self) -> bool:
        return self.status.lower() == "up"


@dataclass
class IcsConn:
    name: str
    guid: str
    device: str = ""
    status: int = -1
    media: int = -1
    sharing_enabled: bool = False
    sharing_type: int = -1          # 0 public, 1 private
    error: str = ""

    @property
    def sharing(self) -> str:
        if not self.sharing_enabled:
            return "disabled"
        return {0: "public", 1: "private"}.get(self.sharing_type, f"type {self.sharing_type}")


@dataclass
class Route:
    prefix: str
    ifindex: int
    nexthop: str
    metric: int


@dataclass
class Inventory:
    adapters: list[Adapter] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    services: dict[str, str] = field(default_factory=dict)
    profiles: list[dict] = field(default_factory=list)
    ics: list[IcsConn] | None = None
    ics_error: str = ""
    ics_scope: str = "192.168.137.1"
    ics_flags: list | None = None          # classic ICS flags (ics.IcsFlag), no admin needed
    ics_flags_error: str = ""
    error: str = ""

    def by_guid(self, guid: str) -> Adapter | None:
        g = norm_guid(guid)
        return next((a for a in self.adapters if a.guid == g), None)

    def by_ifindex(self, idx: int) -> Adapter | None:
        return next((a for a in self.adapters if a.ifindex == idx), None)

    def by_name(self, name: str) -> Adapter | None:
        return next((a for a in self.adapters if a.name == name), None)

    def ics_by_guid(self, guid: str) -> IcsConn | None:
        if self.ics is None:
            return None
        g = norm_guid(guid)
        return next((c for c in self.ics if c.guid == g), None)

    def of_kind(self, *kinds: str) -> list[Adapter]:
        return [a for a in self.adapters if a.kind in kinds]


def ics_from_data(items) -> list[IcsConn]:
    out = []
    for e in items or []:
        out.append(IcsConn(
            name=str(e.get("name", "")), guid=norm_guid(e.get("guid")), device=str(e.get("device", "")),
            status=int(e.get("status", -1)), media=int(e.get("media", -1)),
            sharing_enabled=bool(e.get("sharing_enabled")), sharing_type=int(e.get("sharing_type", -1)),
            error=str(e.get("error", "") or ""),
        ))
    return out


def build_inventory(data: dict) -> Inventory:
    """Turn the INVENTORY_PS JSON into model objects (pure, unit-tested)."""
    inv = Inventory(error=str(data.get("error", "") or ""))
    ips: dict[int, list[str]] = {}
    for e in data.get("ipv4") or []:
        ips.setdefault(int(e["ifindex"]), []).append(str(e["ip"]))
    dns = {int(e["ifindex"]): list(e.get("servers") or []) for e in data.get("dns") or []}
    ipif = {int(e["ifindex"]): e for e in data.get("ipif") or []}
    for e in data.get("adapters") or []:
        idx = int(e.get("ifindex", 0))
        a = Adapter(
            name=str(e.get("name", "")), description=str(e.get("description", "")),
            status=str(e.get("status", "")), ifindex=idx, guid=norm_guid(e.get("guid")),
            mac=str(e.get("mac", "")), hardware=bool(e.get("hardware")), virtual=bool(e.get("virtual")),
            hidden=bool(e.get("hidden")), component=str(e.get("component", "")),
            driver_version=str(e.get("driver_version", "")), driver_provider=str(e.get("driver_provider", "")),
            driver_date=str(e.get("driver_date", "")), media=str(e.get("media", "")),
            ipv4=ips.get(idx, []), dns=dns.get(idx, []),
        )
        if idx in ipif:
            a.forwarding = str(ipif[idx].get("forwarding", ""))
            a.metric = ipif[idx].get("metric")
        a.kind = classify(a.description, a.component, a.media, a.hardware)
        inv.adapters.append(a)
    for e in data.get("routes") or []:
        inv.routes.append(Route(str(e["prefix"]), int(e["ifindex"]), str(e.get("nexthop", "")), int(e.get("metric", 0))))
    inv.services = {str(s["name"]): str(s["status"]) for s in data.get("services") or []}
    inv.profiles = list(data.get("profiles") or [])
    inv.ics_scope = str(data.get("ics_scope") or "192.168.137.1")
    if "ics" in data:
        inv.ics = ics_from_data(data["ics"]) if data["ics"] is not None else None
    inv.ics_error = str(data.get("ics_error", "") or "")
    if "ics_flags" in data:
        from .ics import parse_flags

        inv.ics_flags = parse_flags({"flags": data["ics_flags"]}) if data["ics_flags"] is not None else None
    inv.ics_flags_error = str(data.get("ics_flags_error", "") or "")
    return inv


def uplink_kind(u: dict) -> str:
    """Human label for an uplink entry from UPLINK_PS."""
    kind = classify(u.get("description", ""), "", u.get("media", ""), bool(u.get("hardware")))
    d = (u.get("description") or "").lower()
    if "rndis" in d or "remote ndis" in d or "usb" in d and "ethernet" not in d:
        return "USB tethering"
    return {KIND_WIFI: "Wi-Fi", KIND_ETHERNET: "Ethernet"}.get(kind, "network")


def mac_siblings(a: str, b: str) -> bool:
    """Wi-Fi Direct MACs are derived from the radio MAC: same middle bytes,
    first byte differing in the locally-administered bits, sometimes the
    last byte incremented."""
    pa = [x for x in a.replace(":", "-").split("-") if x]
    pb = [x for x in b.replace(":", "-").split("-") if x]
    if len(pa) != 6 or len(pb) != 6:
        return False
    return pa[1:5] == pb[1:5]


def in_scope(ip: str, scope: str, prefix: int = 24) -> bool:
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(f"{scope}/{prefix}", strict=False)
    except ValueError:
        return False


@dataclass
class HotspotMatch:
    adapter: Adapter | None
    reasons: list[str] = field(default_factory=list)
    candidates: list[tuple[Adapter, int, list[str]]] = field(default_factory=list)
    problem: str = ""               # '', 'no_virtual_adapter', 'none_up', 'ambiguous'


def locate_hotspot(inv: Inventory, wifi_guid: str = "") -> HotspotMatch:
    """Identify the Mobile Hotspot private interface. Never matches by name."""
    virt = inv.of_kind(KIND_WIFI_DIRECT, KIND_HOSTED)
    if not virt:
        return HotspotMatch(None, problem="no_virtual_adapter")
    wifi = inv.by_guid(wifi_guid) if wifi_guid else next(iter(inv.of_kind(KIND_WIFI)), None)
    scored: list[tuple[Adapter, int, list[str]]] = []
    for a in virt:
        score, why = 0, []
        if not a.up:
            scored.append((a, -1, [f"status {a.status}"]))
            continue
        score += 1
        why.append("Up")
        if any(ip == inv.ics_scope for ip in a.ipv4):
            score += 4
            why.append(f"has ICS address {inv.ics_scope}")
        elif any(in_scope(ip, inv.ics_scope) for ip in a.ipv4):
            score += 3
            why.append("address in ICS scope")
        conn = inv.ics_by_guid(a.guid)
        if conn is not None:
            score += 2
            why.append(f"visible to ICS ({conn.sharing})")
        if wifi and a.mac and wifi.mac and mac_siblings(a.mac, wifi.mac):
            score += 1
            why.append(f"MAC derived from {wifi.name}")
        scored.append((a, score, why))
    scored.sort(key=lambda t: t[1], reverse=True)
    up = [t for t in scored if t[1] >= 0]
    if not up:
        return HotspotMatch(None, candidates=scored, problem="none_up")
    if len(up) > 1 and up[0][1] == up[1][1]:
        return HotspotMatch(None, candidates=scored, problem="ambiguous")
    best = up[0]
    return HotspotMatch(best[0], reasons=best[2], candidates=scored)


@dataclass
class Correlation:
    adapter: Adapter
    ics: IcsConn | None


def correlate(inv: Inventory, kinds=(KIND_WIFI, KIND_WIFI_DIRECT, KIND_HOSTED, KIND_WIREGUARD, KIND_ETHERNET)) -> tuple[list[Correlation], list[IcsConn]]:
    """Pair relevant adapters with their ICS entry by GUID.

    Returns (pairs, ics_entries_without_adapter)."""
    pairs = [Correlation(a, inv.ics_by_guid(a.guid)) for a in inv.adapters if a.kind in kinds]
    known = {a.guid for a in inv.adapters}
    orphans = [c for c in (inv.ics or []) if c.guid not in known]
    return pairs, orphans


def default_route_interfaces(inv: Inventory) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for r in inv.routes:
        out.setdefault(r.prefix, []).append(r.ifindex)
    return out
