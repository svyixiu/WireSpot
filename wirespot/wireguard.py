"""WireGuard for Windows: tunnel services, status, handshake verification.

Kill-switch audit (wireguard-windows tunnel/firewall/rules.go): when a peer's
AllowedIPs contains 0.0.0.0/0 or ::/0, the tunnel service installs WFP
filters at the ALE_AUTH_CONNECT and ALE_AUTH_RECV_ACCEPT layers: permit the
tunnel interface, the WireGuard service, loopback, DHCP *client* traffic
(local port 68), NDP and Hyper-V VM-to-VM frames, then block everything
else, plus a DNS block for non-tunnel resolvers. There are no IPFORWARD
filters, so packets merely routed through the PC are not blocked, but ICS's
DHCP *server* (inbound UDP 67 on the hotspot interface) and its DNS proxy
(inbound UDP/TCP 53) are local sockets on a non-tunnel interface and are
blocked. Result: in strict mode, phones associate but cannot obtain an IP
address. Balanced mode (two /1 routes) avoids those filters.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import OWNED_TUNNEL_PREFIXES, scripts, winexec


def find_wireguard(configured: str = "") -> Path | None:
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates += [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WireGuard" / "wireguard.exe",
        Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "WireGuard" / "wireguard.exe",
    ]
    found = shutil.which("wireguard.exe")
    if found:
        candidates.append(Path(found))
    return next((p for p in candidates if p.is_file()), None)


def find_wg(wireguard: Path | None) -> Path | None:
    if wireguard and (wireguard.parent / "wg.exe").is_file():
        return wireguard.parent / "wg.exe"
    found = shutil.which("wg.exe")
    return Path(found) if found else None


@dataclass
class TunnelService:
    name: str               # tunnel name (service name without prefix)
    state: str
    start_mode: str
    config_path: str

    @property
    def ours(self) -> bool:
        return self.name.startswith(OWNED_TUNNEL_PREFIXES)

    @property
    def running(self) -> bool:
        return self.state.lower() == "running"


def services() -> tuple[list[TunnelService], str]:
    r = winexec.powershell(scripts.SERVICES_PS, name="wg-services", timeout=30)
    if not isinstance(r.data, dict):
        return [], r.error_text()
    out = []
    for s in r.data.get("services") or []:
        name = str(s.get("name", ""))
        tunnel = name.split("$", 1)[1] if "$" in name else name
        path = str(s.get("path", ""))
        cfg = path.split("/tunnelservice", 1)[1].strip().strip('"') if "/tunnelservice" in path else ""
        out.append(TunnelService(tunnel, str(s.get("state", "")), str(s.get("start_mode", "")), cfg))
    return out, ""


def install(wireguard: Path, conf: Path) -> winexec.Result:
    return winexec.run([str(wireguard), "/installtunnelservice", str(conf)], timeout=45, name="wireguard-install")


def uninstall(wireguard: Path, name: str) -> winexec.Result:
    return winexec.run([str(wireguard), "/uninstalltunnelservice", name], timeout=45, name="wireguard-uninstall")


@dataclass
class PeerStatus:
    public_key: str
    endpoint: str
    allowed_ips: list[str]
    latest_handshake: int       # unix seconds, 0 = never
    rx: int
    tx: int
    keepalive: str
    has_preshared_key: bool = False

    def handshake_age(self, now: float | None = None) -> float | None:
        if not self.latest_handshake:
            return None
        return max(0.0, (now or time.time()) - self.latest_handshake)


@dataclass
class WgStatus:
    public_key: str
    listen_port: str
    peers: list[PeerStatus] = field(default_factory=list)


def parse_dump(text: str) -> WgStatus | None:
    """Parse ``wg show <if> dump``. The private and preshared keys are dropped
    immediately and never stored."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    first = lines[0].split("\t")
    if len(first) < 3:
        return None
    # first[0] is the private key - intentionally discarded.
    status = WgStatus(public_key=first[1], listen_port=first[2])
    for ln in lines[1:]:
        f = ln.split("\t")
        if len(f) < 8:
            continue
        status.peers.append(PeerStatus(
            public_key=f[0], has_preshared_key=f[1] not in ("(none)", ""), endpoint=f[2],
            allowed_ips=[x for x in f[3].split(",") if x and x != "(none)"],
            latest_handshake=int(f[4]) if f[4].isdigit() else 0,
            rx=int(f[5]) if f[5].isdigit() else 0, tx=int(f[6]) if f[6].isdigit() else 0,
            keepalive=f[7],
        ))
    return status


def show(wg: Path, name: str) -> tuple[WgStatus | None, str]:
    r = winexec.run([str(wg), "show", name, "dump"], timeout=15, name="wg-show")
    if r.returncode != 0:
        return None, r.error_text()
    return parse_dump(r.stdout), ""


def wait_handshake(wg: Path, name: str, timeout: float = 20.0, poll=time.sleep) -> tuple[WgStatus | None, str]:
    """Wait until every peer has a handshake younger than 3 minutes."""
    deadline = time.monotonic() + timeout
    last_err, st = "", None
    while time.monotonic() < deadline:
        st, last_err = show(wg, name)
        if st and st.peers and all((p.handshake_age() or 1e9) < 180 for p in st.peers):
            return st, ""
        poll(1.0)
    if st and st.peers:
        return st, "no handshake with the VPN server yet"
    return st, last_err or "tunnel status unavailable"


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(seconds)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m {s % 60}s ago"
    return f"{s // 3600}h {(s % 3600) // 60}m ago"
