"""IPv4 neighbour (ARP) table access through the IP Helper API (ctypes).

Used by device approval: a device that is not approved gets a *permanent*
neighbour entry pointing its IP at a sink hardware address that no station
owns. Everything the laptop sends to that IP - NAT'd internet replies, DNS
answers from the hotspot's DNS proxy - is addressed to nobody and dropped by
the Wi-Fi driver, so the device has no network until it is approved (the
entry is deleted and the real MAC is learned again). Fast (microseconds per
call) and precise: only the entries WireSpot created carry the sink MAC.
"""
from __future__ import annotations

import ctypes
import os
import uuid
from ctypes import wintypes

# Locally administered, unicast ("02"), spells "WS" - never a real adapter.
SINK_MAC = "02-57-53-00-00-01"
AF_INET = 2
NLNS_PERMANENT = 6


class SOCKADDR_IN(ctypes.Structure):
    _fields_ = [("sin_family", ctypes.c_ushort), ("sin_port", ctypes.c_ushort),
                ("sin_addr", ctypes.c_ubyte * 4), ("sin_zero", ctypes.c_ubyte * 8)]


class SOCKADDR_IN6(ctypes.Structure):
    _fields_ = [("sin6_family", ctypes.c_ushort), ("sin6_port", ctypes.c_ushort), ("sin6_flowinfo", ctypes.c_ulong),
                ("sin6_addr", ctypes.c_ubyte * 16), ("sin6_scope_id", ctypes.c_ulong)]


class SOCKADDR_INET(ctypes.Union):
    _fields_ = [("Ipv4", SOCKADDR_IN), ("Ipv6", SOCKADDR_IN6), ("si_family", ctypes.c_ushort)]


class MIB_IPNET_ROW2(ctypes.Structure):
    _fields_ = [("Address", SOCKADDR_INET), ("InterfaceIndex", ctypes.c_ulong), ("InterfaceLuid", ctypes.c_ulonglong),
                ("PhysicalAddress", ctypes.c_ubyte * 32), ("PhysicalAddressLength", ctypes.c_ulong),
                ("State", ctypes.c_int), ("Flags", ctypes.c_ubyte), ("ReachabilityTime", ctypes.c_ulong)]


class MIB_IPNET_TABLE2(ctypes.Structure):
    _fields_ = [("NumEntries", ctypes.c_ulong), ("Table", MIB_IPNET_ROW2 * 1)]


_ip = ctypes.WinDLL("iphlpapi") if os.name == "nt" else None
if _ip is not None:
    _ip.GetIpNetTable2.argtypes = [ctypes.c_ushort, ctypes.POINTER(ctypes.POINTER(MIB_IPNET_TABLE2))]
    _ip.FreeMibTable.argtypes = [ctypes.c_void_p]
    for _fn in ("CreateIpNetEntry2", "DeleteIpNetEntry2"):
        getattr(_ip, _fn).argtypes = [ctypes.POINTER(MIB_IPNET_ROW2)]
    _ip.ConvertInterfaceGuidToLuid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulonglong)]
    _ip.ConvertInterfaceLuidToIndex.argtypes = [ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(wintypes.ULONG)]


def norm(mac: str) -> str:
    h = "".join(ch for ch in (mac or "") if ch.isalnum()).upper()
    return "-".join(h[i:i + 2] for i in range(0, 12, 2)) if len(h) == 12 else (mac or "").upper()


def ifindex_for_guid(guid: str) -> int:
    if _ip is None or not guid:
        return 0
    try:
        g = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(guid.strip("{}")).bytes_le)
    except ValueError:
        return 0
    luid = ctypes.c_ulonglong()
    if _ip.ConvertInterfaceGuidToLuid(ctypes.byref(g), ctypes.byref(luid)) != 0:
        return 0
    idx = wintypes.ULONG()
    if _ip.ConvertInterfaceLuidToIndex(ctypes.byref(luid), ctypes.byref(idx)) != 0:
        return 0
    return int(idx.value)


def table(ifindex: int | None = None) -> list[dict]:
    """[{ip, mac, state, permanent}] for IPv4 neighbours (optionally one interface)."""
    if _ip is None:
        return []
    ptr = ctypes.POINTER(MIB_IPNET_TABLE2)()
    if _ip.GetIpNetTable2(AF_INET, ctypes.byref(ptr)) != 0:
        return []
    try:
        n = ptr.contents.NumEntries
        rows = ctypes.cast(ctypes.byref(ptr.contents.Table), ctypes.POINTER(MIB_IPNET_ROW2 * n)).contents
        out = []
        for r in rows:
            if ifindex is not None and r.InterfaceIndex != ifindex:
                continue
            ip = ".".join(str(b) for b in r.Address.Ipv4.sin_addr)
            mac = "-".join("%02X" % r.PhysicalAddress[i] for i in range(min(6, r.PhysicalAddressLength)))
            out.append({"ip": ip, "mac": mac, "state": int(r.State), "permanent": r.State == NLNS_PERMANENT,
                        "ifindex": int(r.InterfaceIndex)})
        return out
    finally:
        _ip.FreeMibTable(ptr)


def _row(ifindex: int, ip: str, mac: str = "") -> MIB_IPNET_ROW2:
    row = MIB_IPNET_ROW2()
    row.Address.Ipv4.sin_family = AF_INET
    for i, part in enumerate(ip.split(".")):
        row.Address.Ipv4.sin_addr[i] = int(part)
    row.InterfaceIndex = ifindex
    if mac:
        for i, part in enumerate(norm(mac).split("-")):
            row.PhysicalAddress[i] = int(part, 16)
        row.PhysicalAddressLength = 6
    row.State = NLNS_PERMANENT
    return row


def delete(ifindex: int, ip: str) -> int:
    if _ip is None:
        return 1
    return int(_ip.DeleteIpNetEntry2(ctypes.byref(_row(ifindex, ip))))


def pin(ifindex: int, ip: str, mac: str) -> int:
    """Replace whatever is cached for ``ip`` with a permanent entry -> ``mac``. 0 = success."""
    if _ip is None:
        return 1
    delete(ifindex, ip)
    return int(_ip.CreateIpNetEntry2(ctypes.byref(_row(ifindex, ip, mac))))


def is_unicast(mac: str) -> bool:
    m = norm(mac)
    if len(m) != 17 or m in ("00-00-00-00-00-00", "FF-FF-FF-FF-FF-FF"):
        return False
    return not (int(m[:2], 16) & 1)
