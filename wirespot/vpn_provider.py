"""Provider contract for externally managed VPN connections."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .netid import Adapter


class ProviderState(str, Enum):
    NOT_CONNECTED = "not_connected"
    CONNECTED = "connected"
    READY = "ready"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class ProviderStatus:
    state: ProviderState
    reason: str
    adapter: Adapter | None = None
    protocol: str = "Unknown"
    route_valid: bool = False
    internet_valid: bool = False
    dns_valid: bool = False
    sharing_valid: bool = False

    @property
    def ready(self) -> bool:
        return self.state is ProviderState.READY


class VPNProvider(Protocol):
    name: str

    def detect(self, *, validate: bool = False) -> ProviderStatus: ...
