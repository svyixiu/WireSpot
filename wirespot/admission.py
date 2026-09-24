"""Device approval: new hotspot devices get no network until you allow them.

Mobile Hotspot has no per-device API, so WireSpot enforces approval on the
laptop itself (see neighbors.py): every ~0.7 s the gatekeeper reads the
hotspot adapter's neighbour table; any device whose MAC is not approved has
its IP pinned to a sink hardware address, so replies (internet via the VPN,
DNS, DHCP renewals) never reach it. Approving deletes the pin and the real
address is learned again within a second. Blocking keeps the pin and stops
asking.

devices.json (ProgramData, shared by the app and the CLI):
    approved: {MAC: {name, t}}   blocked: {MAC: {name, t}}
    pending:  {MAC: {ips: [...], since, name}}   (written by the gatekeeper)
Only the process holding Local\\WireSpot.Gate runs the gatekeeper, so the
app and the CLI never fight over the table.

Limits (said plainly in the UI): a device has network for the fraction of a
second between its first packet and the pin; IPv6 is not used by Mobile
Hotspot clients here; a device can always re-join with a new random MAC -
it then simply shows up as a new request.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from . import clients as clients_mod, log, neighbors, oplock, paths

STORE_PATH = paths.PROGRAMDATA_DIR / "devices.json"
GATE_MUTEX = "Local\\WireSpot.Gate"
INTERVAL = 0.7

_lock = threading.RLock()


def _mac(mac: str) -> str:
    return clients_mod.norm_mac(mac)


# ===================================================================== store
def load(path: Path | None = None) -> dict:
    path = path or STORE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for k in ("approved", "blocked", "pending"):
        if not isinstance(data.get(k), dict):
            data[k] = {}
    data["exists"] = path.exists()
    return data


def save(data: dict, path: Path | None = None) -> None:
    path = path or STORE_PATH
    data = {k: v for k, v in data.items() if k != "exists"}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"devices.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.event("error", f"devices.json: {e}")


def status_of(mac: str, store: dict) -> str:
    m = _mac(mac)
    if m in store["approved"]:
        return "approved"
    if m in store["blocked"]:
        return "blocked"
    return "pending"


def decide(mac: str, verdict: str, name: str = "", path: Path | None = None) -> None:
    """verdict: 'approve' | 'block' | 'forget'."""
    m = _mac(mac)
    with _lock:
        s = load(path)
        info = s["pending"].pop(m, {}) or s["approved"].get(m) or s["blocked"].get(m) or {}
        s["approved"].pop(m, None)
        s["blocked"].pop(m, None)
        entry = {"name": name or info.get("name", ""), "t": time.time()}
        if verdict == "approve":
            s["approved"][m] = entry
        elif verdict == "block":
            s["blocked"][m] = entry
        save(s, path)


def approve_all_connected(macs, path: Path | None = None) -> int:
    with _lock:
        s = load(path)
        n = 0
        for mac in macs:
            m = _mac(mac)
            if m not in s["approved"] and m not in s["blocked"]:
                s["approved"][m] = {"name": "", "t": time.time(), "auto": True}
                s["pending"].pop(m, None)
                n += 1
        save(s, path)
        return n


# ===================================================================== planning (pure)
def plan(entries: list[dict], store: dict) -> tuple[list[tuple[str, str]], list[str], dict[str, list[str]]]:
    """Given the hotspot neighbour table and the store:
    -> (to_pin [(ip, mac)], to_release [ip], seen {mac: [ips]} of unapproved devices).

    * dynamic entry of a device that is not approved -> pin its IP to the sink
    * sink entry whose device is now approved -> release (delete) it
    """
    sink = neighbors.SINK_MAC
    approved = set(store["approved"])
    pending_ips = {ip: _mac(mac) for mac, p in store["pending"].items() for ip in p.get("ips", [])}
    blocked_ips = {ip: _mac(mac) for mac, p in store["blocked"].items() for ip in p.get("ips", [])}
    to_pin, to_release, seen = [], [], {}
    for e in entries:
        mac = neighbors.norm(e["mac"])
        ip = e["ip"]
        if mac == sink:
            owner = pending_ips.get(ip) or blocked_ips.get(ip)
            if owner is None or owner in approved:
                to_release.append(ip)
            elif owner:
                seen.setdefault(owner, []).append(ip)
            continue
        if e.get("permanent") or not neighbors.is_unicast(mac) or ip.endswith(".255"):
            continue
        cm = _mac(mac)
        if cm in approved:
            continue
        to_pin.append((ip, sink))
        seen.setdefault(cm, []).append(ip)
    return to_pin, to_release, seen


# ===================================================================== gatekeeper
class Gatekeeper(threading.Thread):
    """Enforces approval on the hotspot adapter while WireSpot is live."""

    def __init__(self, relay, interval: float = INTERVAL):
        super().__init__(daemon=True, name="wirespot-gate")
        self.relay = relay
        self.interval = interval
        self._stop = threading.Event()
        self.announced: set[str] = set()
        self.ifindex = 0
        self.guid = ""
        self._state_t = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        mutex = oplock.NamedMutex(GATE_MUTEX)
        owned = False
        try:
            while not self._stop.wait(self.interval):
                if not owned:
                    owned = mutex.acquire(0)
                    if not owned:
                        continue
                try:
                    self.tick()
                except Exception as e:
                    log.event("error", f"gatekeeper: {e}")
        finally:
            # Holds are NOT released here: quitting the app while the hotspot stays
            # up must not let waiting devices in. They are released when sharing stops.
            mutex.close()

    def _enabled(self) -> bool:
        from . import settings as settings_mod

        s, _ = settings_mod.load()
        return bool(s["behavior"].get("approve_devices", True))

    def tick(self) -> None:
        now = time.time()
        if now - self._state_t > 2:                   # re-read shared state every ~2 s
            self._state_t = now
            from .state import RelayRecord

            rec = RelayRecord.load(self.relay.state_path)     # never swap the relay's live record
            live = rec.state in ("READY", "HOTSPOT_ACTIVE", "SHARING_CONFIGURING") and rec.hotspot_guid
            guid = rec.hotspot_guid if live else ""
            if guid != self.guid:
                self.guid = guid
                self.ifindex = neighbors.ifindex_for_guid(guid) if guid else 0
            self.enabled = self._enabled()
        if not self.ifindex:
            return
        if not getattr(self, "enabled", True):
            release_all(self.ifindex)
            return
        enforce(self.ifindex, notify=self._announce)

    def _announce(self, mac: str, ips: list[str]) -> None:
        # The UIs react to devices.json (sync watcher); this only records it.
        if mac not in self.announced:
            self.announced.add(mac)
            log.event("info", f"approval: holding new device {mac} ({', '.join(ips)})")


def enforce(ifindex: int, notify: Callable[[str, list[str]], None] | None = None, path: Path | None = None) -> dict:
    """One enforcement pass (also called right after an approval for instant effect)."""
    entries = neighbors.table(ifindex)
    with _lock:
        store = load(path)
        if not store["exists"]:
            # First run: the devices already on the hotspot are yours - trust them once.
            connected = [e["mac"] for e in entries if not e["permanent"] and neighbors.is_unicast(e["mac"])
                         and neighbors.norm(e["mac"]) != neighbors.SINK_MAC]
            approve_all_connected(connected, path)
            store = load(path)
        to_pin, to_release, seen = plan(entries, store)
        for ip, mac in to_pin:
            rc = neighbors.pin(ifindex, ip, mac)
            if rc:
                log.event("error", f"approval: could not hold {ip} (error {rc})")
        for ip in to_release:
            neighbors.delete(ifindex, ip)
        changed = False
        for mac, ips in seen.items():
            bucket = store["blocked"] if mac in store["blocked"] else store["pending"]
            p = bucket.setdefault(mac, {"since": time.time()})
            new = sorted(set(p.get("ips", [])) | set(ips))
            if new != p.get("ips"):
                p["ips"] = new
                changed = True
            if bucket is store["pending"] and notify:
                notify(mac, ips)
        if changed or to_release:
            save(store, path)
    return {"pinned": to_pin, "released": to_release, "seen": seen}


def release_all(ifindex: int) -> int:
    """Remove every sink entry WireSpot created on this interface."""
    n = 0
    for e in neighbors.table(ifindex):
        if neighbors.norm(e["mac"]) == neighbors.SINK_MAC:
            if neighbors.delete(ifindex, e["ip"]) == 0:
                n += 1
    return n


def release_guid(guid: str) -> int:
    idx = neighbors.ifindex_for_guid(guid) if guid else 0
    return release_all(idx) if idx else 0
