"""Builds the static website (wirespot.vercel.app) from its sources.

    py -3 website/build.py

  * index.html              from index.src.html
  * privacy.html, terms.html from PRIVACY.md and TERMS.md
  * 404.html
  * assets/wirespot-<hash>.svg  the logo, drawn by wirespot/icon.py; it gets a new
                            name whenever the artwork changes, because assets are
                            cached for a year (vercel.json)

In the page sources, {{icon:name}} becomes that icon from wirespot/icons.py
(inline, so it takes the text colour), and {{logo}}, {{version}}, {{size}} and
{{download}} the logo file and the release details. The site runs no scripts.
"""
from __future__ import annotations

import hashlib
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "website"
sys.path.insert(0, str(ROOT))
from wirespot import VERSION, icon  # noqa: E402
from wirespot.icons import ICONS  # noqa: E402

DOWNLOAD = "https://github.com/svyixiu/WireSpot/releases/latest/download/WireSpot.exe"
GITHUB = "https://github.com/svyixiu/WireSpot"


def icon_svg(name: str) -> str:
    return (f'<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def write_logo() -> str:
    """The logo as a versioned asset; returns its URL."""
    svg = icon.svg()
    name = f"wirespot-{hashlib.sha256(svg.encode()).hexdigest()[:8]}.svg"
    assets = SITE / "assets"
    for old in assets.glob("wirespot-*.svg"):
        if old.name != name:
            old.unlink()
    (assets / name).write_text(svg, encoding="utf-8", newline="\n")
    (assets / "wirespot.svg").write_text(svg, encoding="utf-8", newline="\n")
    return f"/assets/{name}"


def release_size() -> str:
    exe = ROOT / "release" / "WireSpot.exe"
    return f"{exe.stat().st_size / 2 ** 20:.0f} MB" if exe.exists() else "about 30 MB"


def fill(text: str, values: dict[str, str]) -> str:
    text = re.sub(r"\{\{icon:([\w-]+)\}\}", lambda m: icon_svg(m.group(1)), text)
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], text)


def page(title: str, description: str, body: str, active: str, values: dict[str, str]) -> str:
    tabs = [("/", "Home"), ("/#how", "How it works"), ("/#features", "Features"), ("/#faq", "FAQ")]
    on = ' class="on"'
    nav = "".join(f'<a href="{href}"{on if label == active else ""}>{label}</a>' for href, label in tabs)
    frame = f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#1b1614">
  <meta name="description" content="{html.escape(description)}">
  <title>{html.escape(title)}</title>
  <link rel="icon" type="image/svg+xml" href="{{{{logo}}}}">
  <link rel="preload" href="/assets/fonts/archivo-latin-wght-normal.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <header class="site-header"><div class="wrap">
    <a class="brand" href="/" aria-label="WireSpot home"><img src="{{{{logo}}}}" width="32" height="32" alt=""> <span>WireSpot</span></a>
    <nav class="tabs" aria-label="Main navigation">{nav}</nav>
    <a class="text-link" href="{GITHUB}">GitHub {{{{icon:external}}}}</a>
  </div></header>
{body}
  <footer class="site-footer"><div class="wrap">
    <a class="brand" href="/"><img src="{{{{logo}}}}" width="26" height="26" alt=""> <span>WireSpot</span></a>
    <p>Version {{{{version}}}} · GPL-3.0-only</p>
    <nav aria-label="Legal and source"><a href="/privacy">Privacy</a><a href="/terms">Terms of Use</a><a href="{GITHUB}">GitHub</a></nav>
  </div></footer>
</body>
</html>
'''
    return fill(frame, values)


def inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\[([^]]+)\]\((https://[^)]+)\)", lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', escaped)
    escaped = re.sub(r"(?<![=\"/])https://github\.com/svyixiu/WireSpot/issues",
                     '<a href="https://github.com/svyixiu/WireSpot/issues">GitHub Issues</a>', escaped)
    return escaped


def legal(name: str, title: str, values: dict[str, str]) -> None:
    date, chunks = "", []
    for line in (ROOT / f"{name.upper()}.md").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("# "):
            continue
        if line.startswith("## "):
            chunks.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("Effective "):
            date = f'<p class="date">{inline(line)}</p>'
        else:
            chunks.append(f"<p>{inline(line)}</p>")
    content = "\n      ".join(chunks)
    body = f'''  <main class="legal-main wrap">
    <p class="eyebrow">WireSpot <span class="accent">/</span> Legal</p>
    <h1>{title}</h1>
    {date}
    <div class="legal-card glass">
      {content}
    </div>
  </main>'''
    (SITE / f"{name}.html").write_text(page(f"{title} · WireSpot", f"WireSpot {title}.", body, "", values),
                                       encoding="utf-8", newline="\n")


def main() -> None:
    values = {"logo": write_logo(), "version": VERSION, "size": release_size(), "download": DOWNLOAD}
    body = fill((SITE / "index.src.html").read_text(encoding="utf-8"), values)
    (SITE / "index.html").write_text(
        page("WireSpot · Your hotspot, through your VPN",
             "WireSpot routes a Windows Mobile Hotspot through WireGuard profiles or the NordVPN desktop app. "
             "Open source for Windows 10 and 11.", body, "Home", values),
        encoding="utf-8", newline="\n")
    notfound = '''  <main class="wrap notfound">
    <p class="eyebrow">404</p>
    <h1>Nothing here.</h1>
    <p>This page doesn't exist. The download, the guide and the answers are on the home page.</p>
    <a class="button cream" href="/">Back to WireSpot</a>
  </main>'''
    (SITE / "404.html").write_text(page("Not found · WireSpot", "This page doesn't exist.", notfound, "", values),
                                   encoding="utf-8", newline="\n")
    legal("privacy", "Privacy Policy", values)
    legal("terms", "Terms of Use", values)
    print(f"website built: v{VERSION}, logo {values['logo']}")


if __name__ == "__main__":
    main()
