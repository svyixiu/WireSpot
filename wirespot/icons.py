"""WireSpot's SVG icon set and a tiny anti-aliased SVG rasterizer.

Every symbol in the desktop app, tray panel and toasts is one of the vector
icons below (24x24 viewBox, 2px round strokes) - no Unicode glyphs, which
render inconsistently in Tk and pick up ClearType colour fringes. Tk 8.6
cannot draw SVG, so ``render`` rasterizes the supported subset itself (path
with M/L/H/V/A/C/Q/Z, circle, rect with rx, line, polyline, polygon; fill,
stroke, stroke-width, opacity) with 4x4 supersampling, and hands Tk a PNG
with a real alpha channel so icons blend over any background (hover fades
included).

``python -m wirespot.icons assets/icons`` exports every icon as an .svg file.
"""
from __future__ import annotations

import base64
import math
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">')
F = 'fill="currentColor" stroke="none"'

ICONS: dict[str, str] = {
    "dot": f'<circle cx="12" cy="12" r="5" {F}/>',
    "ring": '<circle cx="12" cy="12" r="5"/>',
    "play": '<path d="M8 5.5 L18.5 12 L8 18.5 Z" fill="currentColor"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
    "pause": f'<rect x="6" y="5" width="4.5" height="14" rx="1.3" {F}/><rect x="13.5" y="5" width="4.5" height="14" rx="1.3" {F}/>',
    "refresh": '<path d="M21 12 A9 9 0 1 1 12 3 C14.5 3 16.9 4 18.7 5.7 L21 8"/><path d="M21 3 V8 H16"/>',
    "power": '<path d="M12 3 V11"/><path d="M18.4 6.6 A9 9 0 1 1 5.6 6.6"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12 H21"/><path d="M12 3 C15 6 15 18 12 21 C9 18 9 6 12 3 Z"/>',
    "pulse": '<path d="M3 12 H7 L10 5 L14 19 L17 12 H21"/>',
    "copy": ('<rect x="9" y="9" width="11" height="11" rx="2"/>'
             '<path d="M5 15 H4.5 A1.5 1.5 0 0 1 3 13.5 V4.5 A1.5 1.5 0 0 1 4.5 3 H13.5 A1.5 1.5 0 0 1 15 4.5 V5"/>'),
    "key": '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3 L20 3"/><path d="M16 7 L19 10"/><path d="M18.5 4.5 L21 7"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7 9 L10 12 L7 15"/><path d="M12.5 15 H17"/>',
    "window": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9 H21"/>',
    "x": '<path d="M6 6 L18 18"/><path d="M18 6 L6 18"/>',
    "minus": '<path d="M6 12 H18"/>',
    "check": '<path d="M5 12.5 L10 17.5 L19 7"/>',
    "checkbox": '<rect x="3" y="3" width="18" height="18" rx="4"/>',
    "checkbox-on": '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="M7 12.5 L10.5 16 L17 8"/>',
    "chev-right": '<path d="M9.5 5.5 L16 12 L9.5 18.5"/>',
    "chev-left": '<path d="M14.5 5.5 L8 12 L14.5 18.5"/>',
    "chev-down": '<path d="M5.5 9.5 L12 16 L18.5 9.5"/>',
    "wifi": ('<path d="M2 8.8 A15 15 0 0 1 22 8.8"/><path d="M5.2 12.4 A10 10 0 0 1 18.8 12.4"/>'
             f'<path d="M8.5 15.9 A5 5 0 0 1 15.5 15.9"/><circle cx="12" cy="19.5" r="1.4" {F}/>'),
    "shield": '<path d="M12 3 L19.5 6 V11.5 C19.5 16 16.3 19.5 12 21 C7.7 19.5 4.5 16 4.5 11.5 V6 Z"/>',
    "shield-check": ('<path d="M12 3 L19.5 6 V11.5 C19.5 16 16.3 19.5 12 21 C7.7 19.5 4.5 16 4.5 11.5 V6 Z"/>'
                     '<path d="M8.8 12 L11 14.2 L15.4 9.8"/>'),
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11 V7.5 A4 4 0 0 1 16 7.5 V11"/>',
    "folder": '<path d="M3 7 A2 2 0 0 1 5 5 H9.5 L11.5 7.5 H19 A2 2 0 0 1 21 9.5 V17 A2 2 0 0 1 19 19 H5 A2 2 0 0 1 3 17 Z"/>',
    "file": '<path d="M14 3 H7 A2 2 0 0 0 5 5 V19 A2 2 0 0 0 7 21 H17 A2 2 0 0 0 19 19 V8 Z"/><path d="M14 3 V8 H19"/>',
    "list": (f'<path d="M9 6 H20"/><path d="M9 12 H20"/><path d="M9 18 H20"/><circle cx="4.5" cy="6" r="1.3" {F}/>'
             f'<circle cx="4.5" cy="12" r="1.3" {F}/><circle cx="4.5" cy="18" r="1.3" {F}/>'),
    "sliders": ('<path d="M4 7 H13"/><path d="M19 7 H20"/><circle cx="16" cy="7" r="2.5"/>'
                '<path d="M4 17 H5"/><path d="M11 17 H20"/><circle cx="8" cy="17" r="2.5"/>'),
    "server": (f'<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/>'
               f'<circle cx="7" cy="7.5" r="1.2" {F}/><circle cx="7" cy="16.5" r="1.2" {F}/>'),
    "phone": '<rect x="6.5" y="2.5" width="11" height="19" rx="2.5"/><path d="M11 18 H13"/>',
    "laptop": '<rect x="4.5" y="5" width="15" height="10.5" rx="1.5"/><path d="M2.5 19 H21.5"/>',
    "plus": '<path d="M12 5 V19"/><path d="M5 12 H19"/>',
    "import": '<path d="M12 4 V15"/><path d="M7 10 L12 15 L17 10"/><path d="M4 19.5 H20"/>',
    "trash": '<path d="M4 7 H20"/><path d="M9.5 7 V4.5 H14.5 V7"/><path d="M6.5 7 L7.5 20 H16.5 L17.5 7"/>',
    "arrow-down": '<path d="M12 5 V19"/><path d="M6.5 13.5 L12 19 L17.5 13.5"/>',
    "arrow-up": '<path d="M12 19 V5"/><path d="M6.5 10.5 L12 5 L17.5 10.5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7 V12 L15.5 14"/>',
    "alert": f'<path d="M12 3.5 L21.5 20 H2.5 Z"/><path d="M12 10 V14"/><circle cx="12" cy="17" r="1.2" {F}/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5 C5.5 16.5 8.5 14.5 12 14.5 C15.5 14.5 18.5 16.5 19.5 20.5"/>',
    "ban": '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6 L18.4 18.4"/>',
    "logout": '<path d="M10 4 H6 A2 2 0 0 0 4 6 V18 A2 2 0 0 0 6 20 H10"/><path d="M15 8 L19 12 L15 16"/><path d="M19 12 H9"/>',
    "external": ('<path d="M14 4 H20 V10"/><path d="M20 4 L11 13"/>'
                 '<path d="M18 14 V18.5 A1.5 1.5 0 0 1 16.5 20 H5.5 A1.5 1.5 0 0 1 4 18.5 V7.5 A1.5 1.5 0 0 1 5.5 6 H10"/>'),
    "eye": ('<path d="M2.5 12 C5 7 8.5 5 12 5 C15.5 5 19 7 21.5 12 C19 17 15.5 19 12 19 C8.5 19 5 17 2.5 12 Z"/>'
            '<circle cx="12" cy="12" r="3"/>'),
    "eye-off": ('<path d="M2.5 12 C5 7 8.5 5 12 5 C15.5 5 19 7 21.5 12 C19 17 15.5 19 12 19 C8.5 19 5 17 2.5 12 Z"/>'
                '<circle cx="12" cy="12" r="3"/><path d="M4 4 L20 20"/>'),
    "info": f'<circle cx="12" cy="12" r="9"/><path d="M12 11 V16.5"/><circle cx="12" cy="7.8" r="1.2" {F}/>',
    "bolt": '<path d="M13 3 L5.5 13.5 H11.5 L10.5 21 L18.5 10 H12.5 Z" fill="currentColor"/>',
}


def svg(name: str, color: str = "currentColor") -> str:
    body = ICONS[name]
    doc = HEAD + body + "</svg>"
    return doc.replace("currentColor", color) if color != "currentColor" else doc


# ===================================================================== geometry
_TOK = re.compile(r"[MmLlHhVvAaCcQqZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _arc_points(x1, y1, rx, ry, phi, fa, fs, x2, y2):
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x2, y2)]
    c, s = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = c * dx + s * dy, -s * dx + c * dy
    rx, ry = abs(rx), abs(ry)
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if fa == fs:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx, cy = c * cxp - s * cyp + (x1 + x2) / 2, s * cxp + c * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    th1, dth = ang(1, 0, ux, uy), ang(ux, uy, vx, vy)
    if not fs and dth > 0:
        dth -= 2 * math.pi
    elif fs and dth < 0:
        dth += 2 * math.pi
    n = max(6, int(abs(dth) * max(rx, ry) * 1.5))
    out = []
    for i in range(1, n + 1):
        t = th1 + dth * i / n
        out.append((c * rx * math.cos(t) - s * ry * math.sin(t) + cx, s * rx * math.cos(t) + c * ry * math.sin(t) + cy))
    out[-1] = (x2, y2)
    return out


def _bezier(p0, p1, p2, p3=None, n=16):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        if p3 is None:
            x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
            y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        else:
            x = u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0]
            y = u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]
        out.append((x, y))
    return out


def parse_path(d: str) -> list[tuple[list[tuple[float, float]], bool]]:
    """SVG path data -> [(points, closed)] (curves and arcs flattened)."""
    toks = _TOK.findall(d)
    subs: list[tuple[list, bool]] = []
    pts: list = []
    cur = (0.0, 0.0)
    start = cur
    cmd = ""
    i = 0

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    def flush(closed=False):
        nonlocal pts
        if pts:
            subs.append((pts, closed))
        pts = []

    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                flush(True)
                cur = start
                continue
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = cur if rel else (0.0, 0.0)
        if c == "M":
            flush()
            cur = (ox + num(), oy + num())
            start = cur
            pts = [cur]
            cmd = "l" if rel else "L"            # implicit lineto after moveto
        elif c == "L":
            cur = (ox + num(), oy + num())
            pts.append(cur)
        elif c == "H":
            cur = ((cur[0] if rel else 0.0) + num(), cur[1])
            pts.append(cur)
        elif c == "V":
            cur = (cur[0], (cur[1] if rel else 0.0) + num())
            pts.append(cur)
        elif c == "C":
            p1 = (ox + num(), oy + num())
            p2 = (ox + num(), oy + num())
            p3 = (ox + num(), oy + num())
            pts += _bezier(cur, p1, p2, p3)
            cur = p3
        elif c == "Q":
            p1 = (ox + num(), oy + num())
            p2 = (ox + num(), oy + num())
            pts += _bezier(cur, p1, p2)
            cur = p2
        elif c == "A":
            rx, ry, phi, fa, fs = num(), num(), num(), num(), num()
            end = (ox + num(), oy + num())
            pts += _arc_points(cur[0], cur[1], rx, ry, phi, int(fa), int(fs), end[0], end[1])
            cur = end
        else:
            raise ValueError(f"unsupported path command {cmd!r}")
        if not pts:
            pts = [cur]
    flush()
    return subs


def _circle(cx, cy, r):
    n = max(24, int(r * 8))
    return [([(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)], True)]


def _rect(x, y, w, h, rx):
    rx = min(rx, w / 2, h / 2)
    if rx <= 0:
        return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)]
    d = (f"M{x + rx} {y} H{x + w - rx} A{rx} {rx} 0 0 1 {x + w} {y + rx} V{y + h - rx} "
         f"A{rx} {rx} 0 0 1 {x + w - rx} {y + h} H{x + rx} A{rx} {rx} 0 0 1 {x} {y + h - rx} "
         f"V{y + rx} A{rx} {rx} 0 0 1 {x + rx} {y} Z")
    return parse_path(d)


def _points(s):
    v = [float(x) for x in re.findall(r"-?\d*\.?\d+", s)]
    return list(zip(v[0::2], v[1::2]))


def shapes(doc: str) -> list[dict]:
    """Flatten an SVG document into [{subs, fill, stroke, width, opacity}]."""
    root = ET.fromstring(doc)
    base = {"fill": root.get("fill", "black"), "stroke": root.get("stroke", "none"),
            "width": float(root.get("stroke-width", "1")), "opacity": float(root.get("opacity", "1"))}
    out = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        f = lambda k, d=None: el.get(k, d)
        if tag == "path":
            subs = parse_path(f("d", ""))
        elif tag == "circle":
            subs = _circle(float(f("cx", 0)), float(f("cy", 0)), float(f("r", 0)))
        elif tag == "rect":
            subs = _rect(float(f("x", 0)), float(f("y", 0)), float(f("width", 0)), float(f("height", 0)),
                         float(f("rx", f("ry", 0))))
        elif tag == "line":
            subs = [([(float(f("x1", 0)), float(f("y1", 0))), (float(f("x2", 0)), float(f("y2", 0)))], False)]
        elif tag in ("polyline", "polygon"):
            subs = [(_points(f("points", "")), tag == "polygon")]
        else:
            continue
        out.append({"subs": subs, "fill": f("fill", base["fill"]), "stroke": f("stroke", base["stroke"]),
                    "width": float(f("stroke-width", base["width"])), "opacity": float(f("opacity", base["opacity"]))})
    return out


# ===================================================================== raster
SS = 4          # supersampling per axis


def _coverage(shape: dict, size: int, vb: float) -> bytearray:
    n = size * SS
    k = n / vb
    grid = bytearray(n * n)
    subs = [([(x * k, y * k) for x, y in pts], closed) for pts, closed in shape["subs"]]
    if shape["fill"] not in ("none", None):
        edges = []
        for pts, _ in subs:
            for a in range(len(pts)):
                edges.append((pts[a], pts[(a + 1) % len(pts)]))
        for j in range(n):
            y = j + 0.5
            xs = []
            for (x1, y1), (x2, y2) in edges:
                if (y1 > y) != (y2 > y):
                    xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
            xs.sort()
            row = j * n
            for a in range(0, len(xs) - 1, 2):
                lo = max(0, int(math.ceil(xs[a] - 0.5)))
                hi = min(n - 1, int(math.floor(xs[a + 1] - 0.5)))
                for i in range(lo, hi + 1):
                    grid[row + i] = 1
    if shape["stroke"] not in ("none", None):
        r = shape["width"] * k / 2
        r2 = r * r
        for pts, closed in subs:
            segs = [(pts[a], pts[a + 1]) for a in range(len(pts) - 1)]
            if closed and len(pts) > 2:
                segs.append((pts[-1], pts[0]))
            if not segs:
                segs = [(pts[0], pts[0])]
            for (x1, y1), (x2, y2) in segs:
                dx, dy = x2 - x1, y2 - y1
                ll = dx * dx + dy * dy
                i0, i1 = max(0, int(min(x1, x2) - r)), min(n - 1, int(max(x1, x2) + r) + 1)
                j0, j1 = max(0, int(min(y1, y2) - r)), min(n - 1, int(max(y1, y2) + r) + 1)
                for j in range(j0, j1 + 1):
                    py = j + 0.5
                    row = j * n
                    for i in range(i0, i1 + 1):
                        if grid[row + i]:
                            continue
                        px = i + 0.5
                        if ll:
                            t = ((px - x1) * dx + (py - y1) * dy) / ll
                            t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                            qx, qy = x1 + t * dx - px, y1 + t * dy - py
                        else:
                            qx, qy = x1 - px, y1 - py
                        if qx * qx + qy * qy <= r2:
                            grid[row + i] = 1
    return grid


def _hex(c: str) -> tuple[int, int, int]:
    c = {"black": "#000000", "white": "#FFFFFF"}.get(c.lower(), c).lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def rasterize(doc: str, size: int, color: str) -> list[tuple[int, int, int, int]]:
    """RGBA pixels (row-major) of an SVG document; currentColor -> ``color``."""
    vb = float(re.search(r'viewBox="[^"]*?([\d.]+)"', doc).group(1))
    out = [[0.0, 0.0, 0.0, 0.0] for _ in range(size * size)]
    n = size * SS
    for sh in shapes(doc):
        paint = sh["fill"] if sh["fill"] not in ("none", None) else sh["stroke"]
        rgb = _hex(color if paint == "currentColor" else paint)
        grid = _coverage(sh, size, vb)
        op = sh["opacity"]
        for py in range(size):
            for px in range(size):
                cov = 0
                for sj in range(SS):
                    row = (py * SS + sj) * n + px * SS
                    cov += sum(grid[row:row + SS])
                if not cov:
                    continue
                a = cov / (SS * SS) * op
                dst = out[py * size + px]
                da = dst[3]
                na = a + da * (1 - a)
                for ch in range(3):
                    dst[ch] = (rgb[ch] * a + dst[ch] * da * (1 - a)) / na if na else 0
                dst[3] = na
    return [(int(p[0]), int(p[1]), int(p[2]), int(round(p[3] * 255))) for p in out]


def png_bytes(w: int, h: int, px) -> bytes:
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for r, g, b, a in px[y * w:(y + 1) * w]:
            raw += bytes((r, g, b, a))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


# ===================================================================== Tk images
class IconSet:
    """PhotoImage cache: ``icons.get("wifi", 18, CLAY)``. Sizes are in 96-dpi pixels."""

    def __init__(self, root, scale: float | None = None):
        self.root = root
        if scale is None:
            try:
                scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
            except Exception:
                scale = 1.0
        self.scale = scale
        self.cache: dict = {}

    def px(self, size: float) -> int:
        return max(1, int(round(size * self.scale)))

    def _image(self, key, make):
        img = self.cache.get(key)
        if img is None:
            import tkinter as tk

            w, h, pixels = make()
            img = tk.PhotoImage(master=self.root, data=base64.b64encode(png_bytes(w, h, pixels)).decode(), format="png")
            self.cache[key] = img
        return img

    def get(self, name: str, size: float, color: str):
        s = self.px(size)
        return self._image(("icon", name, s, color), lambda: (s, s, rasterize(svg(name), s, color)))

    def doc(self, key: tuple, doc: str, size: float, color: str = "#000000"):
        """Any SVG document (switch frames, window dots, spinner frames)."""
        s = self.px(size)
        return self._image(("doc", key, s, color), lambda: (s, s, rasterize(doc, s, color)))

    def wide(self, key: tuple, doc: str, w: float, h: float):
        """Non-square SVG (viewBox 0 0 W H with W = w, H = h)."""
        sw, sh = self.px(w), self.px(h)

        def make():
            # rasterize on a square canvas, then crop: keeps one code path
            side = max(sw, sh)
            big = rasterize(doc.replace(f'viewBox="0 0 {w} {h}"', f'viewBox="0 0 {max(w, h)} {max(w, h)}"'),
                            side, "#000000")
            return sw, sh, [big[y * side + x] for y in range(sh) for x in range(sw)]
        return self._image(("wide", key, sw, sh), make)

    def logo(self, size: float, state: str = "live"):
        from . import icon as brand

        s = self.px(size)
        return self._image(("logo", state, s), lambda: (s, s, brand.render(s, state)))


def spinner_doc(frame: int, frames: int, color: str, track: str) -> str:
    """Rotating 100-degree arc over a faint ring."""
    a0 = 2 * math.pi * frame / frames - math.pi / 2
    a1 = a0 + math.radians(100)
    x0, y0 = 12 + 8 * math.cos(a0), 12 + 8 * math.sin(a0)
    x1, y1 = 12 + 8 * math.cos(a1), 12 + 8 * math.sin(a1)
    return (HEAD.replace("currentColor", color) + f'<circle cx="12" cy="12" r="8" stroke="{track}"/>'
            f'<path d="M{x0:.3f} {y0:.3f} A8 8 0 0 1 {x1:.3f} {y1:.3f}"/></svg>')


def switch_doc(p: float, track: str, knob: str) -> str:
    """Toggle switch (viewBox 36x20), knob position p in 0..1."""
    x = 10 + 16 * p
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 20">'
            f'<rect x="1" y="1" width="34" height="18" rx="9" fill="{track}"/>'
            f'<circle cx="{x:.2f}" cy="10" r="6.5" fill="{knob}"/></svg>')


def dot_doc(fill: str, glyph: str | None, ink: str) -> str:
    """macOS-style window dot (viewBox 24) with an optional x / minus on hover."""
    g = ""
    if glyph == "x":
        g = f'<path d="M8.6 8.6 L15.4 15.4 M15.4 8.6 L8.6 15.4" fill="none" stroke="{ink}" stroke-width="2.2" stroke-linecap="round"/>'
    elif glyph == "minus":
        g = f'<path d="M7.8 12 H16.2" fill="none" stroke="{ink}" stroke-width="2.2" stroke-linecap="round"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'<circle cx="12" cy="12" r="11" fill="{fill}"/>{g}</svg>')


def export(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name in ICONS:
        p = folder / f"{name}.svg"
        p.write_text(svg(name) + "\n", encoding="utf-8")
        out.append(p)
    return out


if __name__ == "__main__":
    files = export(Path(sys.argv[1] if len(sys.argv) > 1 else "assets/icons"))
    print(f"{len(files)} icons written")
