"""Who is connected to the hotspot: IP, MAC, hostname and a device-type guess.

Sources, merged by MAC address:
  * NetworkOperatorTetheringManager.GetTetheringClients() - MAC + host names
  * Get-NetNeighbor on the hotspot interface - IPv4 <-> MAC (ARP cache)
  * optional reverse lookup through the ICS DNS proxy (name.mshome.net)

Device type is a *guess* from the host name and the MAC vendor prefix (OUI).
Modern phones use a private/randomised MAC per network; those are flagged
and no vendor is claimed for them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import scripts, winexec

# Small offline OUI table: common phone / console / laptop vendors only.
OUI = {
    "00:1C:B3": "Apple", "3C:22:FB": "Apple", "F0:18:98": "Apple", "A4:83:E7": "Apple", "BC:D0:74": "Apple",
    "DC:A9:04": "Apple", "F4:0F:24": "Apple", "88:66:A5": "Apple", "AC:BC:32": "Apple", "8C:85:90": "Apple",
    "F8:FF:C2": "Apple", "D0:81:7A": "Apple", "00:16:6C": "Samsung", "5C:0A:5B": "Samsung", "8C:77:12": "Samsung",
    "E8:50:8B": "Samsung", "34:14:5F": "Samsung", "A8:7C:01": "Samsung", "FC:DE:90": "Samsung", "84:25:DB": "Samsung",
    "3C:28:6D": "Google", "F4:F5:D8": "Google", "DA:A1:19": "Google", "58:CB:52": "Google", "94:EB:2C": "Google",
    "64:09:80": "Xiaomi", "F8:A4:5F": "Xiaomi", "28:6C:07": "Xiaomi", "78:11:DC": "Xiaomi", "50:8F:4C": "Xiaomi",
    "00:9A:CD": "Huawei", "48:46:FB": "Huawei", "E0:19:1D": "Huawei", "AC:E2:15": "Huawei",
    "2C:4D:54": "OnePlus", "94:65:2D": "OnePlus", "C0:EE:FB": "OnePlus", "A0:D7:F3": "Oppo", "D4:1A:3F": "Oppo",
    "00:09:BF": "Nintendo", "98:B6:E9": "Nintendo", "E8:4E:CE": "Nintendo", "7C:BB:8A": "Nintendo", "04:03:D6": "Nintendo",
    "00:D9:D1": "Sony", "F8:46:1C": "Sony", "BC:60:A7": "Sony", "70:9E:29": "Sony", "00:1D:D8": "Microsoft",
    "7C:1E:52": "Microsoft", "C8:3F:26": "Microsoft", "98:5F:D3": "Microsoft", "3C:FD:FE": "Intel",
    "A4:C3:F0": "Intel", "8C:8D:28": "Intel", "F8:34:41": "Intel", "00:E0:4C": "Realtek", "44:65:0D": "Amazon",
    "F0:27:2D": "Amazon", "74:C2:46": "Amazon", "B8:27:EB": "Raspberry Pi", "DC:A6:32": "Raspberry Pi",
    "E4:5F:01": "Raspberry Pi", "18:B4:30": "Nest", "00:04:4B": "NVIDIA", "48:B0:2D": "NVIDIA",
}

HOST_RULES: list[tuple[str, str]] = [
    (r"iphone", "iPhone"), (r"ipad", "iPad"), (r"macbook|imac|mac-?mini|\bmac\b", "Mac"),
    (r"apple-?watch", "Apple Watch"), (r"apple-?tv", "Apple TV"),
    (r"galaxy|\bsm-[a-z0-9]+", "Samsung Galaxy"), (r"pixel", "Google Pixel"),
    (r"redmi|xiaomi|\bmi-|poco", "Xiaomi phone"), (r"oneplus", "OnePlus phone"), (r"huawei|honor", "Huawei/Honor phone"),
    (r"oppo|realme|vivo", "Android phone"), (r"android|phone", "Android phone"),
    (r"nintendo|switch", "Nintendo Switch"), (r"\bps[345]\b|playstation", "PlayStation"), (r"xbox", "Xbox"),
    (r"desktop-|laptop-|\bwin-|windows", "Windows PC"), (r"chromecast", "Chromecast"),
    (r"echo|alexa|kindle|fire-?tv", "Amazon device"), (r"raspberry|raspberrypi", "Raspberry Pi"),
    (r"steamdeck|steam-deck", "Steam Deck"), (r"tv|bravia|roku", "Smart TV"),
]

VENDOR_TYPES = {
    "Apple": "Apple device", "Samsung": "Samsung device", "Google": "Google device", "Xiaomi": "Xiaomi device",
    "Huawei": "Huawei device", "OnePlus": "OnePlus phone", "Oppo": "Oppo phone", "Nintendo": "Nintendo console",
    "Sony": "Sony device (PlayStation?)", "Microsoft": "Microsoft device (Xbox/Surface?)",
    "Intel": "PC/laptop (Intel Wi-Fi)", "Realtek": "PC/laptop", "Amazon": "Amazon device",
    "Raspberry Pi": "Raspberry Pi", "Nest": "Google Nest", "NVIDIA": "NVIDIA Shield",
}


SINK_MAC = "02:57:53:00:00:01"      # neighbors.SINK_MAC in this module's notation


def norm_mac(mac: str) -> str:
    hexes = re.findall(r"[0-9A-Fa-f]{2}", mac or "")
    return ":".join(h.upper() for h in hexes[:6]) if len(hexes) >= 6 else (mac or "").upper()


def is_randomized(mac: str) -> bool:
    """Locally-administered bit set -> private/randomised address."""
    m = norm_mac(mac)
    try:
        return bool(int(m[:2], 16) & 0x02)
    except ValueError:
        return False


def vendor_of(mac: str) -> str:
    m = norm_mac(mac)
    if is_randomized(m):
        return ""
    return OUI.get(m[:8], "")


def guess_type(hostnames: list[str], mac: str) -> str:
    text = " ".join(hostnames).lower()
    for pattern, label in HOST_RULES:
        if re.search(pattern, text):
            return label
    v = vendor_of(mac)
    if v:
        return VENDOR_TYPES.get(v, v)
    if is_randomized(mac):
        return "Phone/tablet (private MAC)"
    return "Unknown"


@dataclass
class Client:
    mac: str
    ip: str = ""
    hostnames: list[str] = field(default_factory=list)
    arp_state: str = ""
    in_tethering_list: bool = False
    access: str = "approved"      # device approval: approved | pending | blocked

    @property
    def randomized(self) -> bool:
        return is_randomized(self.mac)

    @property
    def vendor(self) -> str:
        return vendor_of(self.mac)

    @property
    def device(self) -> str:
        return guess_type(self.hostnames, self.mac)

    @property
    def display_name(self) -> str:
        return self.hostnames[0] if self.hostnames else "(no name)"


def merge(data: dict) -> list[Client]:
    """Merge tethering clients and neighbour entries by MAC (pure, unit-tested)."""
    by_mac: dict[str, Client] = {}
    for t in data.get("tethering") or []:
        mac = norm_mac(t.get("mac", ""))
        c = by_mac.setdefault(mac, Client(mac=mac))
        c.in_tethering_list = True
        for h in t.get("hostnames") or []:
            name = str(h.get("name", "") if isinstance(h, dict) else h)
            htype = str(h.get("type", "") if isinstance(h, dict) else "")
            if not name:
                continue
            if htype.lower().startswith("ipv4") or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", name):
                c.ip = c.ip or name
            elif htype.lower().startswith("ipv6") or ":" in name:
                continue
            elif name not in c.hostnames:
                c.hostnames.append(name)
    for n in data.get("neighbors") or []:
        mac = norm_mac(n.get("mac", ""))
        if mac == SINK_MAC:            # device-approval hold, not a device
            continue
        ip = str(n.get("ip", ""))
        if ip.endswith(".255") or ip.startswith(("224.", "239.")):
            continue
        c = by_mac.setdefault(mac, Client(mac=mac))
        c.ip = c.ip or ip
        c.arp_state = str(n.get("state", ""))
        ptr = str(n.get("ptr", "") or "")
        if ptr:
            short = ptr.split(".")[0]
            if short and short not in c.hostnames:
                c.hostnames.append(short)
    clients = list(by_mac.values())
    clients.sort(key=lambda c: tuple(int(x) for x in c.ip.split(".")) if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", c.ip) else (999,))
    return clients


def fetch(hotspot_guid: str, resolve: bool = True) -> tuple[list[Client], str, dict]:
    args = ["-HotspotGuid", hotspot_guid] + (["-Resolve"] if resolve else [])
    r = winexec.powershell(scripts.CLIENTS_PS, args, name="clients", winrt=True, timeout=45)
    if not isinstance(r.data, dict):
        return [], r.error_text(), {}
    if not r.data.get("ok"):
        return [], str(r.data.get("error", "client query failed")), r.data
    return merge(r.data), str(r.data.get("tethering_error", "") or ""), r.data
