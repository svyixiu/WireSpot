"""Writes the app's copies of WireSpot's artwork from the Python sources, so the
app and the engine never drift apart:

  * app/src/data/icons.ts - the icon set (wirespot/icons.py); the engine's panel
                            model refers to icons by name
  * app/src/data/logo.ts  - the logo's tile and bolt (wirespot/icon.py)
  * app/public/logo.svg   - the logo as a file (page icon)

    py -3 app/scripts/export-icons.py
"""
import json
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
from wirespot import icon  # noqa: E402
from wirespot.icons import ICONS  # noqa: E402

app = repo / "app"

out = app / "src" / "data" / "icons.ts"
body = ",\n".join(f"    {json.dumps(name)}: {json.dumps(svg)}" for name, svg in sorted(ICONS.items()))
out.write_text(
    "// Generated from wirespot/icons.py by app/scripts/export-icons.py. Don't edit by hand.\n"
    "// 24x24 viewBox, 2px round strokes (the <svg> wrapper is in Icon.vue).\n"
    f"export const ICONS: Record<string, string> = {{\n{body},\n}}\n",
    encoding="utf-8", newline="\n")
print(f"{len(ICONS)} icons -> {out}")

S = 24
m, size, rx = icon.MARGIN * S, (1 - 2 * icon.MARGIN) * S, icon.CORNER * S
logo = app / "src" / "data" / "logo.ts"
logo.write_text(
    "// Generated from wirespot/icon.py by app/scripts/export-icons.py. Don't edit by hand.\n"
    "// The WireSpot mark in a 24x24 box: a rounded tile and a soft, rounded bolt.\n"
    f"export const LOGO_TILE = {{ x: {m:.2f}, y: {m:.2f}, size: {size:.2f}, rx: {rx:.2f} }}\n"
    f"export const LOGO_BOLT = {json.dumps(icon.bolt_path(S))}\n"
    "/** the brand colors, where the accent doesn't apply (the page icon) */\n"
    "export const LOGO_COLORS = { tile: '#%02x%02x%02x', bolt: '#%02x%02x%02x' }\n" % (*icon.CLAY, *icon.CREAM),
    encoding="utf-8", newline="\n")
print(f"logo -> {logo}")

page_icon = app / "public" / "logo.svg"
page_icon.write_text(icon.svg(size=S).replace('width="24" height="24"', ""), encoding="utf-8", newline="\n")
print(f"page icon -> {page_icon}")
