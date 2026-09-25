"""Relay state machine, ownership record, and rollback transaction.

The state file (%ProgramData%\\WireSpot\\state.json) records exactly which
Windows objects WireSpot created or changed, so ``stop`` and crash recovery
only ever undo WireSpot's own changes.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable


class State(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    VPN_CONNECTING = "VPN_CONNECTING"
    VPN_CONNECTED = "VPN_CONNECTED"
    HOTSPOT_STARTING = "HOTSPOT_STARTING"
    HOTSPOT_ACTIVE = "HOTSPOT_ACTIVE"
    SHARING_CONFIGURING = "SHARING_CONFIGURING"
    READY = "READY"
    ERROR = "ERROR"
    STOPPING = "STOPPING"


S = State
TRANSITIONS: dict[State, set[State]] = {
    S.DISCONNECTED: {S.VPN_CONNECTING, S.STOPPING, S.ERROR},
    S.VPN_CONNECTING: {S.VPN_CONNECTED, S.DISCONNECTED, S.ERROR, S.STOPPING},
    S.VPN_CONNECTED: {S.HOTSPOT_STARTING, S.VPN_CONNECTING, S.STOPPING, S.ERROR, S.DISCONNECTED},
    S.HOTSPOT_STARTING: {S.HOTSPOT_ACTIVE, S.VPN_CONNECTED, S.ERROR, S.STOPPING},
    S.HOTSPOT_ACTIVE: {S.SHARING_CONFIGURING, S.VPN_CONNECTED, S.ERROR, S.STOPPING},
    S.SHARING_CONFIGURING: {S.READY, S.VPN_CONNECTED, S.ERROR, S.STOPPING},
    S.READY: {S.STOPPING, S.ERROR, S.VPN_CONNECTED, S.HOTSPOT_STARTING, S.VPN_CONNECTING},
    S.ERROR: {S.STOPPING, S.VPN_CONNECTED, S.DISCONNECTED, S.VPN_CONNECTING, S.HOTSPOT_STARTING},
    S.STOPPING: {S.DISCONNECTED, S.ERROR},
}


class InvalidTransition(RuntimeError):
    pass


@dataclass
class RelayRecord:
    state: str = State.DISCONNECTED.value
    profile: str = ""
    tunnel_name: str = ""
    tunnel_guid: str = ""
    runtime_conf: str = ""
    protection: str = ""
    provider: str = ""              # empty = WireSpot WireGuard, nordvpn = external ownership
    provider_protocol: str = ""
    forward_guard: bool = False
    hotspot_started_by_us: bool = False
    hotspot_guid: str = ""
    hotspot_source: str = ""
    band: str = ""
    wifi_guid: str = ""
    ics_changed: bool = False
    ics_guids: list[str] = field(default_factory=list)
    ics_journal: list[dict] = field(default_factory=list)
    dns_lock: bool = False
    no_connections_timeout_before: bool | None = None
    ready_since: float = 0.0
    last_error: str = ""
    updated: float = 0.0

    @classmethod
    def load(cls, path: Path) -> "RelayRecord":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        rec = cls(**known)
        if rec.state not in State.__members__:
            rec.state = State.ERROR.value
        return rec

    def save(self, path: Path) -> None:
        self.updated = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)


class Machine:
    """Validated state transitions, persisted on every change."""

    def __init__(self, record: RelayRecord, path: Path | None, on_change: Callable[[State, State], None] | None = None):
        self.record = record
        self.path = path
        self.on_change = on_change
        self.history: list[tuple[State, State]] = []

    @property
    def state(self) -> State:
        return State(self.record.state)

    def to(self, new: State, error: str = "") -> None:
        old = self.state
        if new != old and new not in TRANSITIONS[old]:
            raise InvalidTransition(f"{old.value} -> {new.value}")
        self.record.state = new.value
        if error:
            self.record.last_error = error
        self.history.append((old, new))
        self.persist()
        if self.on_change and new != old:
            self.on_change(old, new)

    def force(self, new: State) -> None:
        """Recovery path (startup reconciliation) - bypasses validation."""
        self.record.state = new.value
        self.persist()

    def persist(self) -> None:
        if self.path is not None:
            try:
                self.record.save(self.path)
            except OSError:
                pass


@dataclass
class Undo:
    layer: str                  # "vpn" | "share"
    description: str
    action: Callable[[], object]


class Transaction:
    """Undo stack grouped by layer.

    Rollback policy used by ``start``:
      * failure after the VPN is verified -> roll back layer "share" only
        (hotspot, ICS, DNS lock); the VPN stays up and the user is told.
      * failure before the VPN is verified -> roll back everything.
    """

    def __init__(self, report: Callable[[str, bool, str], None] | None = None):
        self.stack: list[Undo] = []
        self.report = report or (lambda desc, ok, err: None)
        self.committed = False

    def add(self, layer: str, description: str, action: Callable[[], object]) -> None:
        self.stack.append(Undo(layer, description, action))

    def rollback(self, layers: set[str] | None = None) -> list[str]:
        errors = []
        keep = []
        for u in reversed(self.stack):
            if layers is not None and u.layer not in layers:
                keep.append(u)
                continue
            try:
                result = u.action()
                failed = result is False
                self.report(u.description, not failed, "")
                if failed:
                    errors.append(u.description)
            except Exception as e:  # rollback must continue past failures
                self.report(u.description, False, str(e))
                errors.append(f"{u.description}: {e}")
        self.stack = list(reversed(keep))
        return errors

    def commit(self) -> None:
        self.committed = True
        self.stack.clear()
