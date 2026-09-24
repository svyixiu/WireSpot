"""Classic Internet Connection Sharing (HNetCfg) - observed, never driven.

Measured on the reporting machine (Windows 11 22631): while Windows Mobile
Hotspot is running and sharing, *no* connection carries the classic ICS
public/private flags (root\\Microsoft\\HomeNet HNet_ConnectionProperties is
all False before, during and after). Mobile Hotspot's own service (icssvc,
tetheringservice.dll) runs the NAT/DHCP/DNS for its clients from the
connection profile it was started from.

Consequences:
  * WireSpot starts the hotspot *from the WireGuard connection profile*;
    Windows then NATs hotspot clients into the tunnel. Nothing to bind.
  * Forcing classic ICS onto the Wi-Fi Direct adapter fights icssvc and fails
    with 0x80040201 (EVENT_E_ALL_SUBSCRIBERS_FAILED) - the v0.2.0 failure.
  * Classic ICS still matters as a *conflict*: if some other tool enabled
    classic sharing with the hotspot adapter as private and a non-VPN
    connection as public, that is a second path. It is detected and reported.

The HomeNet WMI classes are readable without administrator rights.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import scripts, winexec
from .netid import IcsConn, ics_from_data, norm_guid


@dataclass
class IcsFlag:
    guid: str
    name: str
    public: bool
    private: bool
    exists: bool = True


def parse_flags(data: dict | None) -> list[IcsFlag]:
    out = []
    for e in (data or {}).get("flags") or []:
        out.append(IcsFlag(norm_guid(e.get("guid")), str(e.get("name") or ""), bool(e.get("public")),
                           bool(e.get("private")), bool(e.get("exists", True))))
    return out


def flags() -> tuple[list[IcsFlag] | None, str]:
    """Connections with classic ICS public/private set (HomeNet WMI, no admin needed)."""
    r = winexec.powershell(scripts.ICS_FLAGS_PS, name="ics-flags", timeout=45)
    if not isinstance(r.data, dict) or not r.data.get("ok"):
        return None, (r.data or {}).get("error", "") if isinstance(r.data, dict) else r.error_text()
    return parse_flags(r.data), ""


@dataclass
class Conflict:
    kind: str          # "leak" | "stale" | "other"
    detail: str


def conflicts(fl: list[IcsFlag] | None, tunnel_guid: str, hotspot_guid: str) -> list[Conflict]:
    """Pure: which classic-ICS settings could interfere with a VPN-sourced hotspot."""
    out: list[Conflict] = []
    if not fl:
        return out
    tunnel, hot = norm_guid(tunnel_guid), norm_guid(hotspot_guid)
    publics = [f for f in fl if f.public]
    privates = [f for f in fl if f.private]
    for f in fl:
        if not f.exists:
            out.append(Conflict("stale", f"classic ICS is still recorded for a connection that no longer exists "
                                         f"({f.name or f.guid}); this is a known cause of 0x80040201 errors"))
    if hot and any(p.guid == hot for p in privates):
        bad = [p for p in publics if p.guid != tunnel]
        if bad:
            out.append(Conflict("leak", f"classic ICS shares '{bad[0].name or bad[0].guid}' into the hotspot adapter "
                                        f"- a second, non-VPN path for hotspot clients"))
    elif publics:
        names = ", ".join(p.name or p.guid for p in publics)
        out.append(Conflict("other", f"classic ICS is enabled by other software/settings (public: {names}); "
                                     f"it does not carry hotspot traffic"))
    return out


def list_connections() -> tuple[list[IcsConn] | None, str]:
    """Full HNetShare enumeration (administrator only) for diagnostics."""
    r = winexec.powershell(scripts.ICS_PS, ["-Action", "list"], name="ics-list", timeout=45)
    if not isinstance(r.data, dict) or not r.data.get("ok"):
        return None, (r.data or {}).get("error", "") if isinstance(r.data, dict) else r.error_text()
    return ics_from_data(r.data.get("before")), ""


def disable(guids: list[str]) -> tuple[bool, str]:
    """Only used to clean up classic sharing recorded by WireSpot 0.2.0 / ProtonRelay."""
    r = winexec.powershell(scripts.ICS_PS, ["-Action", "disable", "-DisableGuids", ",".join(guids)],
                           name="ics-disable", timeout=60)
    ok = isinstance(r.data, dict) and bool(r.data.get("ok"))
    return ok, "" if ok else r.error_text()
