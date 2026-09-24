"""Shared test fixtures. Keys are random per run - never real credentials."""
import base64
import os

from wirespot import ui


def key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def proton_conf(server="US-FREE#5", endpoint="149.40.62.21:51820", allowed="0.0.0.0/0, ::/0",
                extra_iface="", extra_peer="", private_key=None):
    return f"""[Interface]
# Bouncing = 2
# NAT-PMP (Port Forwarding) = off
# VPN Accelerator = on
PrivateKey = {private_key or key()}
Address = 10.2.0.2/32
DNS = 10.2.0.1
{extra_iface}
[Peer]
# {server}
PublicKey = {key()}
AllowedIPs = {allowed}
Endpoint = {endpoint}
{extra_peer}
PersistentKeepalive = 25"""


class Capture:
    """Collect ui output lines (ANSI stripped)."""

    def __enter__(self):
        ui.capture_begin()
        return self

    def __exit__(self, *exc):
        self.lines = ui.capture_end()

    def text(self):
        return "\n".join(self.lines)
