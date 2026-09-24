"""WireSpot icon: a casual Wi-Fi mark struck by a lightning bolt.

One geometry drives both outputs, so they always match:
  * ``svg()``      - the vector master (assets/wirespot.svg)
  * ``render()``   - anti-aliased pixels -> multi-size .ico (exe + tray states)

A compact Wi-Fi mark (dot + two arcs) with a lightning bolt striking down
its right side; a thin gap in the badge colour separates the bolt from the
arc it cuts. Tray states recolour the same mark (Claude palette only);
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

ICON_VERSION = "2"   # bump when the artwork changes (invalidates cached tray icons)

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
ARC_CENTER = (0.40, 0.76)
DOT_R = 0.085
ARCS = ((0.18, 0.28), (0.36, 0.46))        # (inner, outer) radius bands
ARC_HALF_ANGLE = 46                          # degrees either side of straight up
BOLT = [(0.77, 0.28), (0.57, 0.60), (0.70, 0.60), (0.62, 0.92), (0.88, 0.52), (0.75, 0.52), (0.87, 0.28)]
BOLT_GAP = 0.045                             # knock-out around the bolt
PAUSE = ((0.62, 0.70), (0.78, 0.86))         # x ranges of the two pause bars (paused state)


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


def _dist_to_poly(x: float, y: float, poly) -> float:
    best = 9.0
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
    return best


def _arc(x: float, y: float) -> bool:
    cx, cy = ARC_CENTER
    dx, dy = x - cx, y - cy
    if math.hypot(dx, dy) <= DOT_R:
        return True
    if dy >= 0:
        return False
    if math.degrees(math.atan2(abs(dx), -dy)) > ARC_HALF_ANGLE:
        return False
    d = math.hypot(dx, dy)
    return any(r0 <= d <= r1 for r0, r1 in ARCS)


def _pause(x: float, y: float) -> bool:
    return 0.40 <= y <= 0.84 and any(a <= x <= b for a, b in PAUSE)


def _bang(x: float, y: float) -> bool:
    if 0.44 <= x <= 0.56 and 0.20 <= y <= 0.60:
        return True
    return math.hypot(x - 0.5, y - 0.76) <= 0.075


def classify(x: float, y: float, state: str) -> str:
    """'' outside, 'badge', 'mark', 'bolt' or 'extra' for a unit-square point."""
    if not _rounded_square(x, y):
        return ""
    if state == "error":
        return "bolt" if _bang(x, y) else "badge"
    if state == "paused":
        if _pause(x, y):
            return "bolt"
        near = 0.40 - BOLT_GAP <= y <= 0.84 + BOLT_GAP and any(a - BOLT_GAP <= x <= b + BOLT_GAP for a, b in PAUSE)
        return "mark" if (_arc(x, y) and not near) else "badge"
    if _in_poly(x, y, BOLT):
        return "bolt"
    if _dist_to_poly(x, y, BOLT) < BOLT_GAP:
        return "badge"
    if _arc(x, y):
        return "mark"
    return "badge"


def render(size: int, state: str = "live") -> list[tuple[int, int, int, int]]:
    """RGBA pixels, row-major top-down, 4x4 supersampled."""
    badge, mark, bolt = STYLES.get(state, STYLES["live"])
    colors = {"badge": badge, "mark": mark, "bolt": bolt, "extra": CREAM}
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
    badge, mark, bolt = STYLES.get(state, STYLES["live"])

    def hexc(c):
        return "#%02X%02X%02X" % c

    s = size
    cx, cy = ARC_CENTER[0] * s, ARC_CENTER[1] * s
    a = math.radians(ARC_HALF_ANGLE)
    arcs = []
    for r0, r1 in ARCS:
        r = (r0 + r1) / 2 * s
        w = (r1 - r0) * s
        x1, y1 = cx - r * math.sin(a), cy - r * math.cos(a)
        x2, y2 = cx + r * math.sin(a), cy - r * math.cos(a)
        arcs.append(f'<path d="M{x1:.1f} {y1:.1f} A{r:.1f} {r:.1f} 0 0 1 {x2:.1f} {y2:.1f}" '
                    f'fill="none" stroke="{hexc(mark)}" stroke-width="{w:.1f}"/>')
    arcs.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{DOT_R * s:.1f}" fill="{hexc(mark)}"/>')
    pts = " ".join(f"{x * s:.1f},{y * s:.1f}" for x, y in BOLT)
    m, r = MARGIN * s, CORNER * s
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {s} {s}" width="{s}" height="{s}">\n'
            f'  <title>WireSpot</title>\n'
            f'  <rect x="{m:.1f}" y="{m:.1f}" width="{s - 2 * m:.1f}" height="{s - 2 * m:.1f}" rx="{r:.1f}" '
            f'fill="{hexc(badge)}"/>\n'
            f'  <g>{"".join(arcs)}</g>\n'
            f'  <polygon points="{pts}" fill="{hexc(bolt)}" stroke="{hexc(badge)}" '
            f'stroke-width="{BOLT_GAP * 2 * s:.1f}" stroke-linejoin="round" paint-order="stroke"/>\n'
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
