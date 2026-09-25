"""Conservative NordVPN discovery and path validation.

WireSpot never drives NordVPN or alters its settings. The interface GUID is
correlated with Windows routes and WinRT tethering capability on each scan.
"""
from __future__ import annotations

import socket
import struct
import ipaddress

from . import log
from .netid import Adapter
from .vpn_provider import ProviderState, ProviderStatus


def _protocol(adapter: Adapter) -> str | None:
    text = " ".join((adapter.name, adapter.description, adapter.component,
                     adapter.driver_provider)).lower()
    if "nordlynx" in text:
        return "NordLynx"
    # DCO is used by multiple providers. Require a NordVPN marker as well.
    if "nordvpn" in text and ("openvpn" in text or "data channel offload" in text):
        return "OpenVPN"
    if "nordvpn" in text and "tap" in text:
        return "OpenVPN"
    return None


def _bound_internet(ifindex: int) -> bool:
    """Probe TCP on this interface, not the machine's ordinary default route."""
    for host in ("1.1.1.1", "9.9.9.9"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(3)
                # Windows IP_UNICAST_IF is 31 and takes network byte order.
                sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack("!I", ifindex))
                sock.connect((host, 443))
                return True
        except OSError:
            pass
    return False


def _bound_dns(ifindex: int, servers: list[str]) -> bool:
    # A minimal A query for example.com. Replies must arrive on the selected
    # tunnel interface, so host DNS configured on ordinary Wi-Fi cannot pass.
    query = (b"\x57\x53\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
             b"\x07example\x03com\x00\x00\x01\x00\x01")
    for server in servers:
        try:
            if ipaddress.ip_address(server).version != 4:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(3)
                sock.setsockopt(socket.IPPROTO_IP, 31, struct.pack("!I", ifindex))
                sock.sendto(query, (server, 53))
                reply, _ = sock.recvfrom(512)
                if len(reply) >= 12 and reply[:2] == query[:2] and reply[2] & 0x80 and not (reply[3] & 0x0f):
                    return True
        except (OSError, ValueError):
            pass
    return False


class NordVPNProvider:
    name = "NordVPN"

    def __init__(self, backend):
        self.backend = backend

    def detect(self, *, validate: bool = False) -> ProviderStatus:
        log.event("info", "[NordVPN] Beginning detection")
        inv = self.backend.inventory(with_ics=False)
        if inv.error:
            return ProviderStatus(ProviderState.INCOMPATIBLE, "Could not inspect network interfaces: " + inv.error)
        candidates = [(a, _protocol(a)) for a in inv.adapters]
        candidates = [(a, p) for a, p in candidates if p and a.guid and not a.hardware and a.up and a.ipv4]
        if not candidates:
            return ProviderStatus(ProviderState.NOT_CONNECTED,
                                  "NordVPN not connected. Connect in the NordVPN app, then scan again.")
        routes = self.backend.route_check()
        selected = []
        for a, protocol in candidates:
            matching = bool(routes.get("ok") and len(routes.get("routes") or []) >= 2 and
                            all(r.get("ifindex") == a.ifindex for r in routes["routes"]))
            log.event("info", f"[NordVPN] Candidate {a.description} {{{a.guid}}}: route={'valid' if matching else 'invalid'}")
            if matching:
                selected.append((a, protocol))
        if len(selected) != 1:
            reason = ("Multiple NordVPN tunnels own the route; disconnect one and scan again." if len(selected) > 1
                      else "The active NordVPN interface does not own the internet route.")
            return ProviderStatus(ProviderState.INCOMPATIBLE, reason)
        adapter, protocol = selected[0]
        connected = ProviderStatus(ProviderState.CONNECTED, "NordVPN connected; validation pending.",
                                   adapter, protocol, route_valid=True)
        if not validate:
            return connected
        if not _bound_internet(adapter.ifindex):
            return ProviderStatus(ProviderState.INCOMPATIBLE,
                                  "Internet access through the NordVPN interface failed.", adapter, protocol,
                                  route_valid=True)
        if not adapter.dns:
            return ProviderStatus(ProviderState.INCOMPATIBLE,
                                  "NordVPN has no DNS servers on its interface.", adapter, protocol,
                                  route_valid=True, internet_valid=True)
        if not _bound_dns(adapter.ifindex, adapter.dns):
            return ProviderStatus(ProviderState.INCOMPATIBLE,
                                  "DNS queries through NordVPN failed.", adapter, protocol,
                                  route_valid=True, internet_valid=True)
        data, err = self.backend.hotspot_status(adapter.guid, source="vpn")
        src = (data or {}).get("source") or {}
        sharing = (not err and src.get("kind") == "vpn" and
                   src.get("adapter", "").strip("{}").lower() == adapter.guid and
                   src.get("capability") == "Enabled" and src.get("level") == "InternetAccess")
        if not sharing:
            return ProviderStatus(ProviderState.INCOMPATIBLE,
                                  "Windows cannot share this NordVPN connection with Mobile Hotspot.",
                                  adapter, protocol, True, True, True)
        log.event("info", f"[NordVPN] Validated {protocol} {{{adapter.guid}}}: route, internet, DNS, sharing")
        return ProviderStatus(ProviderState.READY, "NordVPN is ready to host.", adapter, protocol,
                              True, True, True, True)
