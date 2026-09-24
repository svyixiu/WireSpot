"""Complete desktop snapshots. Persisted ownership is not runtime evidence."""
from copy import deepcopy
from math import isfinite

from . import settings


DEFAULT_STATUS = {
    "state": "UNKNOWN", "saved_state": "", "fresh": False, "busy": "",
    "ssid": "—", "band": "auto", "security": "wpa2", "protection": "balanced",
    "dns_lock": False, "tunnel": "", "profile": "", "profile_label": "(no profile)",
    "ready_since": 0, "last_error": "", "paused_until": 0, "autostart": False,
    "clients": [], "clients_full": [], "profiles": [], "profile_objs": [], "bad_profiles": [],
    "rx": 0, "tx": 0, "handshake": None, "hotspot_state": "", "endpoint": "",
    "uplink": "", "uplink_t": 0, "exit_ip": "", "settings": {},
    "pending": [], "blocked_n": 0,
}


def normalize_status(raw=None, *, configured=None):
    """Return an independent, complete value; never mutate a published snapshot."""
    raw = raw or {}
    s, _ = settings.migrate(configured if configured is not None else raw.get("settings"))
    result = deepcopy(DEFAULT_STATUS)
    result.update(ssid=s["hotspot"]["ssid"] or "—", band=s["hotspot"]["band"],
                  security=s["hotspot"]["security"], protection=s["vpn"]["protection"],
                  profile=s["vpn"]["default_profile"])
    for key, value in raw.items():
        if key not in result or key == "settings":
            continue
        default = result[key]
        if isinstance(default, str):
            if isinstance(value, str) and value:
                result[key] = value
        elif isinstance(default, bool):
            if isinstance(value, bool):
                result[key] = value
        elif isinstance(default, (int, float)):
            if isinstance(value, (int, float)) and isfinite(value):
                result[key] = max(0, value)
        elif isinstance(default, list):
            if isinstance(value, (list, tuple)):
                result[key] = list(value)
        else:
            result[key] = value
    result["settings"] = s
    return result
