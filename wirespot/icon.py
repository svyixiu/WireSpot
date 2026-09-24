"""WireSpot icon: one clear lightning mark on a warm rounded tile.

One geometry drives both outputs, so they always match:
  * ``svg()``      - the vector master (assets/wirespot.svg)
  * ``render()``   - anti-aliased pixels -> multi-size .ico (exe + tray states)

A single bolt stays legible in the taskbar and notification area. Tray states
recolour the same mark (Claude palette only);
"paused" swaps the bolt for pause bars, "error" shows "!".
``python -m wirespot.icon [out.ico] [out.svg]`` writes both files.
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

CLAY = (217, 119, 87)
SHIMMER = (235, 159, 127)
CRAIL = (193, 95, 60)
CREAM = (250, 249, 245)
DARK = (31, 30, 29)
STONE = (176, 174, 165)

ICON_VERSION = "3"   # bump when the artwork changes (invalidates cached tray icons)

# state -> (badge colour, mark colour, bolt colour)
STYLES = {
    "live": (CLAY, CREAM, CREAM),
    "vpn": (DARK, CLAY, CLAY),
    "idle": (DARK, STONE, STONE),
    "busy": (DARK, SHIMMER, SHIMMER),
    "paused": (DARK, SHIMMER, SHIMMER),
    "error": (CRAIL, CREAM, CREAM),
}

# ------------------------------------------------------------------ geometry (unit square, y down)
CORNER, MARGIN = 0.22, 0.04
BOLT = [(0.58, 0.12), (0.29, 0.55), (0.47, 0.55),
        (0.38, 0.89), (0.73, 0.43), (0.55, 0.43), (0.66, 0.12)]
PAUSE = ((0.34, 0.45), (0.55, 0.66))         # x ranges of the two pause bars


def _rounded_square(x: float, y: float) -> bool:
    lo, hi, r = MARGIN, 1 - MARGIN, CORNER
    if not (lo <= x <= hi and lo <= y <= hi):
        return False
    cx = min(max(x, lo + r), hi - r)
    cy = min(max(y, lo + r), hi - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _in_poly(x: float, y: float, poly) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _pause(x: float, y: float) -> bool:
    return 0.30 <= y <= 0.72 and any(a <= x <= b for a, b in PAUSE)


def _bang(x: float, y: float) -> bool:
    if 0.44 <= x <= 0.56 and 0.20 <= y <= 0.60:
        return True
    return math.hypot(x - 0.5, y - 0.76) <= 0.075


def classify(x: float, y: float, state: str) -> str:
    """'' outside, 'badge' or 'bolt' for a unit-square point."""
    if not _rounded_square(x, y):
        return ""
    if state == "error":
        return "bolt" if _bang(x, y) else "badge"
    if state == "paused":
        return "bolt" if _pause(x, y) else "badge"
    if _in_poly(x, y, BOLT):
        return "bolt"
    return "badge"


def render(size: int, state: str = "live") -> list[tuple[int, int, int, int]]:
    """RGBA pixels, row-major top-down, 4x4 supersampled."""
    badge, _mark, bolt = STYLES.get(state, STYLES["live"])
    colors = {"badge": badge, "bolt": bolt}
    n = 4
    px = []
    for j in range(size):
        for i in range(size):
            acc = [0.0, 0.0, 0.0]
            cov = 0
            for sj in range(n):
                for si in range(n):
                    part = classify((i + (si + 0.5) / n) / size, (j + (sj + 0.5) / n) / size, state)
                    if part:
                        c = colors[part]
                        acc[0] += c[0]
                        acc[1] += c[1]
                        acc[2] += c[2]
                        cov += 1
            if cov == 0:
                px.append((0, 0, 0, 0))
            else:
                px.append((int(acc[0] / cov), int(acc[1] / cov), int(acc[2] / cov), int(255 * cov / (n * n))))
    return px


def svg(state: str = "live", size: int = 256) -> str:
    """Vector master of the same geometry."""
    badge, _mark, bolt = STYLES.get(state, STYLES["live"])

    def hexc(c):
        return "#%02X%02X%02X" % c

    s = size
    pts = " ".join(f"{x * s:.1f},{y * s:.1f}" for x, y in BOLT)
    m, r = MARGIN * s, CORNER * s
    if state == "paused":
        symbol = "".join(f'<rect x="{a * s:.1f}" y="{0.30 * s:.1f}" width="{(b-a) * s:.1f}" '
                         f'height="{0.42 * s:.1f}" rx="{0.025 * s:.1f}" fill="{hexc(bolt)}"/>'
                         for a, b in PAUSE)
    elif state == "error":
        symbol = (f'<rect x="{0.44 * s:.1f}" y="{0.20 * s:.1f}" width="{0.12 * s:.1f}" '
                  f'height="{0.40 * s:.1f}" rx="{0.02 * s:.1f}" fill="{hexc(bolt)}"/>'
                  f'<circle cx="{0.5 * s:.1f}" cy="{0.76 * s:.1f}" r="{0.075 * s:.1f}" fill="{hexc(bolt)}"/>')
    else:
        symbol = f'<polygon points="{pts}" fill="{hexc(bolt)}"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {s} {s}" width="{s}" height="{s}">\n'
            f'  <title>WireSpot</title>\n'
            f'  <rect x="{m:.1f}" y="{m:.1f}" width="{s - 2 * m:.1f}" height="{s - 2 * m:.1f}" rx="{r:.1f}" '
            f'fill="{hexc(badge)}"/>\n'
            f'  {symbol}\n'
            f'</svg>\n')


def _dib(size: int, px) -> bytes:
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = []
    for j in range(size - 1, -1, -1):                   # bottom-up
        row = bytearray()
        for i in range(size):
            r, g, b, a = px[j * size + i]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    mask_row = ((size + 31) // 32) * 4
    return header + b"".join(rows) + b"\x00" * mask_row * size


def ico_bytes(state: str = "live", sizes=(16, 20, 24, 32, 40, 48, 64, 256)) -> bytes:
    images = [_dib(s, render(s, state)) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    for s, img in zip(sizes, images):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    return out + b"".join(images)


def write_ico(path: Path, state: str = "live", sizes=(16, 20, 24, 32, 40, 48, 64, 256)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ico_bytes(state, sizes))
    return path


def write_splash(path: Path, w: int = 260, h: int = 200, logo: int = 104) -> Path:
    """Setup's splash screen (shown while the installer unpacks): the logo on a dark card."""
    from .icons import png_bytes

    px = [(*DARK, 255)] * (w * h)
    mark = render(logo, "live")
    ox, oy = (w - logo) // 2, (h - logo) // 2
    for j in range(logo):
        for i in range(logo):
            r, g, b, a = mark[j * logo + i]
            if a:
                k = a / 255
                px[(oy + j) * w + ox + i] = (int(r * k + DARK[0] * (1 - k)), int(g * k + DARK[1] * (1 - k)),
                                             int(b * k + DARK[2] * (1 - k)), 255)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(w, h, px))
    return path


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "splash":
        write_splash(Path(sys.argv[2]))
        print("splash written")
        raise SystemExit(0)
    ico = Path(sys.argv[1] if len(sys.argv) > 1 else "wirespot.ico")
    write_ico(ico)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)
        Path(sys.argv[2]).write_text(svg(), encoding="utf-8")
    print("icon written")
