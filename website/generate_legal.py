"""Render the repository's versioned legal Markdown into static website pages."""
from __future__ import annotations

import html
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "website"


def inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\[([^]]+)\]\((https://[^)]+)\)",
                     lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', escaped)
    escaped = re.sub(r"(?<![=\"/])https://github\.com/svyixiu/WireSpot/issues",
                     '<a href="https://github.com/svyixiu/WireSpot/issues">GitHub Issues</a>', escaped)
    return escaped


def render(name: str, title: str) -> None:
    lines = (ROOT / f"{name.upper()}.md").read_text(encoding="utf-8").splitlines()
    chunks: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            chunks.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("Effective "):
            chunks.append(f'<p class="date">{inline(line)}</p>')
        else:
            chunks.append(f"<p>{inline(line)}</p>")
    content = "\n    ".join(chunks)
    page = f'''<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#141413"><meta name="color-scheme" content="dark"><title>{title} — WireSpot</title><link rel="icon" type="image/svg+xml" href="/assets/wirespot-6db1d396.svg"><link rel="stylesheet" href="/style.css"></head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="nav-wrap"><nav class="nav" aria-label="Main navigation"><a class="brand" href="/" aria-label="WireSpot home"><img src="/assets/wirespot-6db1d396.svg" width="30" height="30" alt=""><span>WireSpot</span></a><div class="nav-links"><a href="/">Home</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></div><div class="nav-actions"><a class="btn btn-ghost btn-sm nav-gh" href="https://github.com/svyixiu/WireSpot">GitHub</a><a class="btn btn-primary btn-sm" href="https://github.com/svyixiu/WireSpot/releases/latest/download/WireSpotSetup.exe">Download</a></div></nav></header>
<main id="main" class="page-main legal"><div class="wrap"><p class="eyebrow">WireSpot / Legal</p><h1>{title}</h1>
    {content}
</div></main>
<footer class="page-footer"><div class="wrap footer-bottom"><p>Version 0.2.1 · GPL-3.0-only</p><nav aria-label="Legal and source"><a href="/privacy">Privacy</a><a href="/terms">Terms of Use</a><a href="https://github.com/svyixiu/WireSpot">GitHub</a></nav></div></footer>
</body></html>
'''
    (SITE / f"{name}.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    render("privacy", "Privacy Policy")
    render("terms", "Terms of Use")
