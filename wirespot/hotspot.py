"""Windows Mobile Hotspot (NetworkOperatorTetheringManager) control and semantics.

Bug fixed here: v0.1.1 printed ``[+] Hotspot start: WiFiDeviceOff`` because it
treated *receiving* a NetworkOperatorTetheringOperationResult as success.
Now an operation only succeeds when Status == Success AND the manager's
TetheringOperationalState reaches On afterwards.

About WiFiDeviceOff (TetheringOperationStatus = 3): the tethering service
reports it when it cannot bring up the Wi-Fi *access-point role* (the
Microsoft Wi-Fi Direct virtual adapter) - it does not mean the Wi-Fi client
connection is off. Known causes: radio soft/hard-blocked, the Wi-Fi Direct
virtual adapter disabled, a band the driver cannot host next to the current
uplink (adapter/driver/regulatory dependent - Intel documents 5 GHz hotspot
restrictions in some countries), or a transient state during a network
change. Measured on the reporting machine (Intel AX201, driver 24.60.0.3):
a 5 GHz hotspot DID start while the uplink was on 2.4 GHz, so a band
mismatch alone is not treated as fatal; it is reported as a hint.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import scripts, winexec

OPERATION_STATUS = {
    "Success": "The operation completed.",
    "Unknown": "Windows reported an unspecified tethering failure.",
    "MobileBroadbandDeviceOff": "The mobile-broadband (cellular) device is off.",
    "WiFiDeviceOff": ("Windows could not bring up the Wi-Fi access-point role (the Wi-Fi Direct virtual adapter). "
                      "Known causes: Wi-Fi radio off / airplane mode, the Wi-Fi Direct adapter disabled, a band the "
                      "driver cannot host next to the current uplink, or a transient state right after a network change."),
    "EntitlementCheckTimeout": "The operator entitlement check timed out.",
    "EntitlementCheckFailure": "The operator entitlement check failed.",
    "OperationInProgress": "Another tethering operation is already in progress.",
    "BluetoothDeviceOff": "The Bluetooth device is off.",
    "NetworkLimitedConnectivity": "The source connection has limited connectivity (no internet).",
}

CAPABILITY = {
    "Enabled": "Tethering is available.",
    "DisabledByGroupPolicy": "Disabled by Group Policy (NC_ShowSharedAccessUI / Prohibit ICS).",
    "DisabledByHardwareLimitation": "The Wi-Fi hardware/driver cannot host a hotspot.",
    "DisabledByOperator": "Disabled by the mobile operator.",
    "DisabledBySku": "Not available on this Windows edition.",
    "DisabledByRequiredAppNotInstalled": "A required operator app is not installed.",
    "DisabledDueToUnknownCause": "Disabled for an unknown reason.",
    "DisabledBySystemCapability": "Disabled by a system capability check (for example no Wi-Fi adapter).",
}

BAND_NAMES = {"TwoPointFourGigahertz": "2.4", "FiveGigahertz": "5", "SixGigahertz": "6", "Auto": "auto"}
BAND_LABEL = {"auto": "auto", "2.4": "2.4 GHz", "5": "5 GHz", "6": "6 GHz"}


@dataclass
class HotspotResult:
    ok: bool
    status: str = ""
    status_code: int | None = None
    state_after: str = ""
    message: str = ""
    code: str = ""
    error: str = ""
    stage: str = ""
    source: dict = field(default_factory=dict)
    applied_band: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        if self.ok:
            return "hotspot is Off" if self.raw.get("action") == "stop" else "hotspot is On"
        if self.error:
            return self.error
        bits = []
        if self.status:
            bits.append(f"status {self.status}")
        if self.state_after:
            bits.append(f"state {self.state_after}")
        return ", ".join(bits) or "no result from Windows"


def parse_result(data, returncode: int = 0, stderr: str = "") -> HotspotResult:
    """Strict interpretation of a HOTSPOT_PS start/stop payload."""
    if not isinstance(data, dict):
        return HotspotResult(False, code="no_result", error=_first_line(stderr) or "PowerShell returned no result")
    status = str(data.get("status", "") or "")
    state = str(data.get("state_after", "") or data.get("state", "") or "")
    action = data.get("action", "start")
    if action == "stop":
        ok = bool(data.get("ok")) and state == "Off"
    else:
        # Both must hold. Never trust a bare "ok" or the mere presence of a status.
        ok = bool(data.get("ok")) and status == "Success" and state == "On"
    return HotspotResult(
        ok=ok, status=status, status_code=data.get("status_code"), state_after=state,
        message=str(data.get("message", "") or ""), code=str(data.get("code", "") or ""),
        error=str(data.get("error", "") or ""), stage=str(data.get("stage", "") or ""),
        source=data.get("source") or {}, applied_band=BAND_NAMES.get(str(data.get("applied_band", "")), ""),
        raw=data,
    )


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def describe_status(status: str) -> str:
    return OPERATION_STATUS.get(status, f"Windows returned tethering status '{status}'.")


# --------------------------------------------------------------------------- band planning

@dataclass
class BandPlan:
    ok: bool
    requested: str
    reason: str = ""
    alternatives: list[str] = field(default_factory=list)   # first = recommended
    note: str = ""                                          # non-fatal hint


def supported_bands(status_data: dict) -> dict[str, bool | None]:
    """{'2.4': True, '5': True, '6': None} from HOTSPOT_PS status 'bands'."""
    out: dict[str, bool | None] = {"2.4": None, "5": None, "6": None}
    for name, val in (status_data.get("bands") or {}).items():
        key = BAND_NAMES.get(name)
        if key and key != "auto":
            out[key] = val if isinstance(val, bool) else None
    return out


def plan_band(requested: str, uplink_band: str, supported: dict[str, bool | None]) -> BandPlan:
    """Decide whether the requested hotspot band can work *before* starting.

    uplink_band: band of the connected Wi-Fi station ('' when the uplink is not Wi-Fi).
    """
    if requested == "auto":
        return BandPlan(True, requested)
    label = BAND_LABEL.get(requested, requested)
    avail = [b for b in ("2.4", "5", "6") if supported.get(b) is True]
    if requested == "6" and supported.get("6") is None:
        return BandPlan(False, requested, "This Windows build/adapter does not expose a 6 GHz hotspot band.",
                        ["auto"] + [b for b in avail if b != requested])
    if supported.get(requested) is False:
        return BandPlan(False, requested, f"The Wi-Fi adapter reports that a {label} hotspot is not supported.",
                        ["auto"] + [b for b in avail if b != requested])
    if uplink_band and uplink_band != requested:
        # Not fatal: many adapters time-slice both bands (measured: AX201 hosts 5 GHz
        # while connected on 2.4 GHz). Some drivers/regions refuse -> WiFiDeviceOff.
        return BandPlan(True, requested, note=(
            f"Uplink is on {BAND_LABEL.get(uplink_band, uplink_band)} and the hotspot will use {label}; most adapters "
            f"handle this, some refuse it (WiFiDeviceOff). If so, WireSpot offers band auto."))
    return BandPlan(True, requested)


# --------------------------------------------------------------------------- operations

def status(tunnel_guid: str = "", wifi_guid: str = "", source: str = "any") -> tuple[dict | None, str]:
    r = winexec.powershell(scripts.HOTSPOT_PS, ["-Action", "status", "-Source", source,
                                                "-TunnelGuid", tunnel_guid, "-WifiGuid", wifi_guid],
                           name="hotspot-status", winrt=True, timeout=60)
    if not isinstance(r.data, dict):
        return None, r.error_text()
    if not r.data.get("ok"):
        return r.data, str(r.data.get("error", "hotspot status failed"))
    return r.data, ""


def start(*, ssid: str, passphrase: str, band: str, security: str, source: str,
          tunnel_guid: str, wifi_guid: str) -> HotspotResult:
    from . import log

    log.register_secret(passphrase)
    r = winexec.powershell(
        scripts.HOTSPOT_PS,
        ["-Action", "start", "-Source", source, "-TunnelGuid", tunnel_guid, "-WifiGuid", wifi_guid,
         "-Ssid", ssid, "-Band", band, "-Security", security],
        name="hotspot-start", winrt=True, timeout=150,
        secrets={"WIRESPOT_PASSPHRASE": passphrase},
    )
    if r.timed_out:
        return HotspotResult(False, code="timeout", error="Windows did not answer the hotspot request within 150 s.")
    return parse_result(r.data, r.returncode, r.stderr)


def stop(tunnel_guid: str = "", wifi_guid: str = "") -> HotspotResult:
    r = winexec.powershell(scripts.HOTSPOT_PS, ["-Action", "stop", "-Source", "any",
                                                "-TunnelGuid", tunnel_guid, "-WifiGuid", wifi_guid],
                           name="hotspot-stop", winrt=True, timeout=90)
    return parse_result(r.data, r.returncode, r.stderr)
