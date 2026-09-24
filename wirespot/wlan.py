"""Physical Wi-Fi state via the native WLAN API (wlanapi.dll), locale-independent.

``netsh wlan`` output is translated on non-English Windows, so the channel,
radio state and connection state come from WlanQueryInterface instead. netsh
parsing is kept only as a supplementary source for doctor (driver details).
"""
from __future__ import annotations

import ctypes
import os
import re
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field

# WLAN_INTF_OPCODE
OP_RADIO_STATE = 4
OP_INTERFACE_STATE = 6
OP_CURRENT_CONNECTION = 7
OP_CHANNEL_NUMBER = 8

IFACE_STATES = {
    0: "not_ready", 1: "connected", 2: "ad_hoc_network_formed", 3: "disconnecting",
    4: "disconnected", 5: "associating", 6: "discovering", 7: "authenticating",
}
RADIO_STATES = {0: "unknown", 1: "on", 2: "off"}


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def to_str(self) -> str:
        return str(uuid.UUID(bytes_le=bytes(self)))


class WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [("InterfaceGuid", GUID), ("strInterfaceDescription", ctypes.c_wchar * 256),
                ("isState", ctypes.c_int)]


class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [("dwNumberOfItems", wintypes.DWORD), ("dwIndex", wintypes.DWORD),
                ("InterfaceInfo", WLAN_INTERFACE_INFO * 1)]


class WLAN_PHY_RADIO_STATE(ctypes.Structure):
    _fields_ = [("dwPhyIndex", wintypes.DWORD), ("dot11SoftwareRadioState", ctypes.c_int),
                ("dot11HardwareRadioState", ctypes.c_int)]


class WLAN_RADIO_STATE(ctypes.Structure):
    _fields_ = [("dwNumberOfPhys", wintypes.DWORD), ("PhyRadioState", WLAN_PHY_RADIO_STATE * 64)]


class DOT11_SSID(ctypes.Structure):
    _fields_ = [("uSSIDLength", wintypes.ULONG), ("ucSSID", ctypes.c_ubyte * 32)]


class WLAN_ASSOCIATION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("dot11Ssid", DOT11_SSID), ("dot11BssType", ctypes.c_int),
                ("dot11Bssid", ctypes.c_ubyte * 6), ("dot11PhyType", ctypes.c_int),
                ("uDot11PhyIndex", wintypes.ULONG), ("wlanSignalQuality", wintypes.ULONG),
                ("ulRxRate", wintypes.ULONG), ("ulTxRate", wintypes.ULONG)]


class WLAN_SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("bSecurityEnabled", wintypes.BOOL), ("bOneXEnabled", wintypes.BOOL),
                ("dot11AuthAlgorithm", ctypes.c_int), ("dot11CipherAlgorithm", ctypes.c_int)]


class WLAN_CONNECTION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("isState", ctypes.c_int), ("wlanConnectionMode", ctypes.c_int),
                ("strProfileName", ctypes.c_wchar * 256),
                ("wlanAssociationAttributes", WLAN_ASSOCIATION_ATTRIBUTES),
                ("wlanSecurityAttributes", WLAN_SECURITY_ATTRIBUTES)]


@dataclass
class WlanInterface:
    guid: str
    description: str
    state: str
    radio_software: str = "unknown"
    radio_hardware: str = "unknown"
    channel: int = 0
    ssid: str = ""
    profile: str = ""
    signal: int = 0
    rx_mbps: float = 0.0
    phy_type: int = 0
    band_hint: str = ""          # from netsh "Band" when available (6 GHz disambiguation)
    errors: list[str] = field(default_factory=list)

    @property
    def connected(self) -> bool:
        return self.state == "connected"

    @property
    def radio_on(self) -> bool:
        return self.radio_software != "off" and self.radio_hardware != "off"

    @property
    def band(self) -> str:
        return band_from_channel(self.channel, self.band_hint)


def band_from_channel(channel: int, hint: str = "") -> str:
    """'2.4' | '5' | '6' | '' . 6 GHz channel numbers overlap 2.4/5, so a
    netsh "Band" hint wins when present."""
    h = hint.replace(" ", "").lower()
    if h.startswith("6"):
        return "6"
    if h.startswith("5"):
        return "5"
    if h.startswith("2.4") or h.startswith("2,4"):
        return "2.4"
    if 1 <= channel <= 14:
        return "2.4"
    if 32 <= channel <= 196:
        return "5"
    return ""


def query_interfaces() -> list[WlanInterface]:
    if os.name != "nt":
        return []
    wlanapi = ctypes.WinDLL("wlanapi.dll")
    handle = wintypes.HANDLE()
    negotiated = wintypes.DWORD()
    rc = wlanapi.WlanOpenHandle(2, None, ctypes.byref(negotiated), ctypes.byref(handle))
    if rc != 0:
        raise OSError(rc, f"WlanOpenHandle failed ({rc}) - is the WLAN AutoConfig service running?")
    out: list[WlanInterface] = []
    try:
        plist = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
        rc = wlanapi.WlanEnumInterfaces(handle, None, ctypes.byref(plist))
        if rc != 0:
            raise OSError(rc, f"WlanEnumInterfaces failed ({rc})")
        try:
            count = plist.contents.dwNumberOfItems
            arr = ctypes.cast(ctypes.byref(plist.contents.InterfaceInfo),
                              ctypes.POINTER(WLAN_INTERFACE_INFO * count)).contents
            for info in arr:
                iface = WlanInterface(
                    guid=info.InterfaceGuid.to_str(),
                    description=info.strInterfaceDescription,
                    state=IFACE_STATES.get(info.isState, str(info.isState)),
                )
                _fill(wlanapi, handle, info.InterfaceGuid, iface)
                out.append(iface)
        finally:
            wlanapi.WlanFreeMemory(plist)
    finally:
        wlanapi.WlanCloseHandle(handle, None)
    return out


def _query(wlanapi, handle, guid, opcode):
    size = wintypes.DWORD()
    data = ctypes.c_void_p()
    rc = wlanapi.WlanQueryInterface(handle, ctypes.byref(guid), opcode, None,
                                    ctypes.byref(size), ctypes.byref(data), None)
    if rc != 0:
        return rc, None
    return 0, data


def _fill(wlanapi, handle, guid: GUID, iface: WlanInterface) -> None:
    rc, data = _query(wlanapi, handle, guid, OP_RADIO_STATE)
    if rc == 0:
        rs = ctypes.cast(data, ctypes.POINTER(WLAN_RADIO_STATE)).contents
        sw = {rs.PhyRadioState[i].dot11SoftwareRadioState for i in range(min(rs.dwNumberOfPhys, 64))}
        hw = {rs.PhyRadioState[i].dot11HardwareRadioState for i in range(min(rs.dwNumberOfPhys, 64))}
        # Any PHY off means the adapter radio is effectively switched off.
        iface.radio_software = "off" if 2 in sw else ("on" if 1 in sw else "unknown")
        iface.radio_hardware = "off" if 2 in hw else ("on" if 1 in hw else "unknown")
        wlanapi.WlanFreeMemory(data)
    else:
        iface.errors.append(f"radio_state rc={rc}")

    if iface.state != "connected":
        return
    rc, data = _query(wlanapi, handle, guid, OP_CHANNEL_NUMBER)
    if rc == 0:
        iface.channel = ctypes.cast(data, ctypes.POINTER(wintypes.ULONG)).contents.value
        wlanapi.WlanFreeMemory(data)
    else:
        iface.errors.append(f"channel rc={rc}")
    rc, data = _query(wlanapi, handle, guid, OP_CURRENT_CONNECTION)
    if rc == 0:
        ca = ctypes.cast(data, ctypes.POINTER(WLAN_CONNECTION_ATTRIBUTES)).contents
        a = ca.wlanAssociationAttributes
        iface.ssid = bytes(a.dot11Ssid.ucSSID[: min(a.dot11Ssid.uSSIDLength, 32)]).decode("utf-8", "replace")
        iface.profile = ca.strProfileName
        iface.signal = int(a.wlanSignalQuality)
        iface.rx_mbps = a.ulRxRate / 1000.0
        iface.phy_type = int(a.dot11PhyType)
        wlanapi.WlanFreeMemory(data)
    else:
        iface.errors.append(f"current_connection rc={rc}")


# --------------------------------------------------------------------------- netsh (supplementary)

def _field(block: str, *names: str) -> str:
    for n in names:
        m = re.search(rf"(?im)^\s*{re.escape(n)}\s*:\s*(.*?)\s*$", block)
        if m:
            return m.group(1)
    return ""


def parse_netsh_interfaces(text: str) -> dict[str, dict]:
    """Map lower-case interface GUID -> {'band','channel','radio_type'} (English netsh only)."""
    out: dict[str, dict] = {}
    for block in re.split(r"(?m)^\s*Name\s*:", text)[1:]:
        guid = _field(block, "GUID").lower()
        if guid:
            out[guid] = {
                "band": _field(block, "Band"),
                "channel": _field(block, "Channel"),
                "radio_type": _field(block, "Radio type"),
            }
    return out


@dataclass
class DriverInfo:
    name: str = ""
    driver: str = ""
    vendor: str = ""
    version: str = ""
    date: str = ""
    radio_types: str = ""
    hosted_network: str = ""
    bands: list[str] = field(default_factory=list)
    wpa3_personal: bool = False


def parse_netsh_drivers(text: str) -> list[DriverInfo]:
    out = []
    for block in re.split(r"(?m)^\s*Interface name\s*:", text)[1:]:
        name = block.splitlines()[0].strip() if block.splitlines() else ""
        d = DriverInfo(
            name=name,
            driver=_field(block, "Driver"),
            vendor=_field(block, "Vendor"),
            version=_field(block, "Version"),
            date=_field(block, "Date"),
            radio_types=_field(block, "Radio types supported"),
            hosted_network=_field(block, "Hosted network supported"),
        )
        m = re.search(r"(?ims)Number of supported bands\s*:\s*\d+\s*\n(.*?)(?:\n\s*\n|\n\s*IHV|\Z)", block)
        if m:
            for line in m.group(1).splitlines():
                bm = re.match(r"\s*([0-9.,]+\s*GHz)", line)
                if bm:
                    d.bands.append(bm.group(1).replace(" ", " "))
        d.wpa3_personal = bool(re.search(r"(?im)^\s*WPA3-Personal\s+", block))
        out.append(d)
    return out
