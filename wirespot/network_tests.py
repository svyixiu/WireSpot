"""Small, user-initiated connection checks for the desktop app.

An endpoint ping cannot authenticate a WireGuard peer. A recent WireGuard
handshake is the stronger signal when that profile is connected. The speed
test measures the laptop's current route against Cloudflare; it never sends
settings or VPN configuration data.
"""
from __future__ import annotations

import re
import socket
import statistics
import subprocess
import time
import urllib.request
from urllib.parse import urlencode


SPEED_HOST = "https://speed.cloudflare.com"


def endpoint_parts(endpoint: str) -> tuple[str, int]:
    """Return the host and UDP port from a WireGuard Endpoint value."""
    value = endpoint.strip()
    if value.startswith("["):
        match = re.fullmatch(r"\[([^]]+)\]:(\d+)", value)
        if not match:
            raise ValueError("This profile has no usable endpoint")
        host, port = match.groups()
    else:
        if value.count(":") != 1:
            raise ValueError("This profile has no usable endpoint")
        host, port = value.rsplit(":", 1)
    if not host or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError("This profile has no usable endpoint")
    return host, int(port)


def profile_health(endpoint: str, *, active: bool = False, handshake_age: float | None = None,
                   resolver=socket.getaddrinfo, runner=subprocess.run) -> dict:
    """Check DNS and optional ICMP; report VPN health only from a recent handshake."""
    host, port = endpoint_parts(endpoint)
    if active and handshake_age is not None and handshake_age < 180:
        return {"level": "good", "title": "VPN handshake is recent",
                "detail": f"Authenticated {int(handshake_age)}s ago. The tunnel is responding.", "latency_ms": None}
    try:
        addresses = resolver(host, port, type=socket.SOCK_DGRAM)
    except OSError as exc:
        return {"level": "bad", "title": "Endpoint name did not resolve",
                "detail": str(exc), "latency_ms": None}
    if not addresses:
        return {"level": "bad", "title": "Endpoint name did not resolve",
                "detail": "Check your internet connection or choose another profile.", "latency_ms": None}
    family, _kind, _proto, _canon, target = next(
        (item for item in addresses if item[0] == socket.AF_INET), addresses[0])
    address = target[0]
    try:
        result = runner(["ping", "-n", "2", "-w", "1200", "-6" if family == socket.AF_INET6 else "-4", address],
                        capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        matches = [int(x) for x in re.findall(r"time[=<]\s*(\d+)\s*ms", result.stdout, re.IGNORECASE)]
        latency = round(statistics.median(matches), 1) if matches else None
        title = f"Endpoint answered ping{f' · {latency:g} ms' if latency is not None else ''}"
        detail = "The host is reachable. Connect to verify a WireGuard handshake."
        level = "warn" if not active else "bad"
    else:
        latency = None
        title = "Endpoint resolved; no ping reply"
        detail = "Many VPN servers ignore ping. Connect to check the WireGuard handshake."
        level = "warn" if not active else "bad"
    if active:
        title = "No recent VPN handshake"
        detail = "The active tunnel has not authenticated recently. Try reconnecting or another server."
    return {"level": level, "title": title, "detail": detail, "latency_ms": latency}


def speed_test(progress=None, *, opener=None, clock=time.perf_counter,
               download_bytes: int = 5_000_000, upload_bytes: int = 1_000_000) -> dict:
    """Estimate route latency and throughput with bounded Cloudflare transfers."""
    progress = progress or (lambda _phase: None)
    # Use the Windows routing table rather than an environment/browser HTTP proxy.
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def request(path: str, *, data: bytes | None = None, timeout: int = 8):
        headers = {"User-Agent": "WireSpot connection check", "Cache-Control": "no-store"}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream"
        return opener(urllib.request.Request(SPEED_HOST + path, data=data, headers=headers), timeout=timeout)

    progress("Measuring latency")
    latency = []
    for _ in range(3):
        start = clock()
        with request("/__down?bytes=0") as response:
            response.read()
        latency.append((clock() - start) * 1000)

    progress("Measuring download")
    start = clock()
    received = 0
    with request("/__down?" + urlencode({"bytes": download_bytes}), timeout=15) as response:
        while received < download_bytes:
            chunk = response.read(min(64 * 1024, download_bytes - received))
            if not chunk:
                break
            received += len(chunk)
    down_time = max(clock() - start, 0.001)
    if received < download_bytes:
        raise OSError("The download test ended early")

    progress("Measuring upload")
    payload = bytes(upload_bytes)  # generated zero bytes; no personal data
    start = clock()
    with request("/__up", data=payload, timeout=15) as response:
        response.read()
    up_time = max(clock() - start, 0.001)
    return {"latency_ms": round(statistics.median(latency), 0),
            "download_mbps": round(received * 8 / down_time / 1_000_000, 1),
            "upload_mbps": round(upload_bytes * 8 / up_time / 1_000_000, 1),
            "download_bytes": received, "upload_bytes": upload_bytes}
