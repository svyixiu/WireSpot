"""Terminal UI in the Claude Code style: palette, glyphs, shimmer spinner,
boxes, numbered choice menus.

Palette - Claude's clay family and warm neutrals only (no green, no blue):
    claude  #D97757  accent, success, spinner, step markers, boxes
    shimmer #EB9F7F  warnings, moving highlight in spinner text
    crail   #C15F3C  errors (always paired with the ✖/× shape)
    cream   #FAF9F5  selected menu rows / choices
    light   #E8E6DC  normal text
    stone   #B0AEA5  secondary text, borders
Status is carried by shape as well as tone: ● ok/info, ▲ warning, ✖ error.

Glyph tiers:
    fancy  Windows Terminal / VS Code / ConEmu: ✻ spinner, ⎿ connectors, ╭╮ boxes, ❯
    safe   any console with VT support (legacy conhost + Consolas): only WGL4
           glyphs (● • · └ ┌ ─ › ▸), so nothing renders as a box
    ascii  no VT support: plain ASCII, no colour, no animation
Every printed message is mirrored to the debug log.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass

from . import log

AUTO_YES = False          # --yes: take the recommended choice without asking
INTERACTIVE = True        # False when stdin is not a terminal
FORCE_TIER: str | None = None   # the tray renders captured output with the "fancy" glyphs


# --------------------------------------------------------------------------- terminal capability

_VT_READY: bool | None = None


def _windows_vt_ready() -> bool:
    global _VT_READY
    if _VT_READY is not None:
        return _VT_READY
    if os.name != "nt":
        _VT_READY = True
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            _VT_READY = False
            return False
        _VT_READY = bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        _VT_READY = False
    return _VT_READY


def _isatty(stream) -> bool:
    try:
        return stream.isatty()
    except Exception:
        return False


def use_color() -> bool:
    if not _isatty(sys.stdout) or "NO_COLOR" in os.environ:
        return False
    return _windows_vt_ready()


def fancy() -> bool:
    """Full Unicode glyphs. Legacy conhost fonts (Consolas) lack ✻ ⎿ ╭ ❯."""
    if os.environ.get("WIRESPOT_ASCII") or not use_color():
        return False
    return bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM")
                or os.environ.get("WIRESPOT_FANCY") or os.environ.get("ConEmuANSI"))


def tier() -> str:
    if FORCE_TIER:
        return FORCE_TIER
    if fancy():
        return "fancy"
    return "safe" if use_color() else "ascii"


def width() -> int:
    return max(40, shutil.get_terminal_size((100, 30)).columns - 1)


def set_title(title: str) -> None:
    """Console window/tab title (otherwise Windows shows the exe path or the current folder)."""
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
    if _isatty(sys.stdout) and use_color():
        sys.stdout.write(f"\x1b]0;{title}\x07")
        sys.stdout.flush()


# --------------------------------------------------------------------------- palette

def rgb(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"


CLAUDE_RGB = (217, 119, 87)     # #D97757
SHIMMER_RGB = (235, 159, 127)   # #EB9F7F
CRAIL_RGB = (193, 95, 60)       # #C15F3C
CREAM_RGB = (250, 249, 245)     # #FAF9F5
LIGHT_RGB = (232, 230, 220)     # #E8E6DC
STONE_RGB = (176, 174, 165)     # #B0AEA5
PALETTE = (CLAUDE_RGB, SHIMMER_RGB, CRAIL_RGB, CREAM_RGB, LIGHT_RGB, STONE_RGB)


class C:
    claude = rgb(*CLAUDE_RGB)
    shimmer = rgb(*SHIMMER_RGB)
    success = rgb(*CLAUDE_RGB)
    error = rgb(*CRAIL_RGB)
    warning = rgb(*SHIMMER_RGB)
    suggestion = rgb(*CREAM_RGB)
    muted = rgb(*STONE_RGB)
    text = rgb(*LIGHT_RGB)
    bold = "\x1b[1m"
    dim = "\x1b[2m"
    reset = "\x1b[0m"
    accent, accent2 = claude, shimmer


def color(s: str, code: str) -> str:
    return f"{code}{s}{C.reset}" if use_color() else s


def gradient(text: str, start=CLAUDE_RGB, end=SHIMMER_RGB) -> str:
    if not use_color():
        return text
    n = max(len(text) - 1, 1)
    out = []
    for i, ch in enumerate(text):
        t = i / n
        r, g, b = (int(start[k] + (end[k] - start[k]) * t) for k in range(3))
        out.append(f"{rgb(r, g, b)}{ch}")
    return "".join(out) + C.reset


def shimmer(text: str, frame: int) -> str:
    """Claude-style: text in the accent colour with a lighter band sweeping across."""
    if not use_color():
        return text
    pos = frame % (len(text) + 8) - 4
    out = []
    for i, ch in enumerate(text):
        out.append((C.shimmer if abs(i - pos) <= 1 else C.claude) + ch)
    return "".join(out) + C.reset


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def visible_len(s: str) -> int:
    return len(_ANSI.sub("", s))


# --------------------------------------------------------------------------- glyphs

@dataclass(frozen=True)
class Glyphs:
    dot: str
    ok: str
    fail: str
    info: str
    warn: str
    sub: str
    arrow: str
    step: str
    spinner: tuple[str, ...]
    prompt: str
    tl: str
    tr: str
    bl: str
    br: str
    h: str
    v: str
    ellipsis: str
    mask: str


FANCY = Glyphs("●", "●", "✖", "●", "▲", "⎿", "❯", "✻", ("·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"),
               ">", "╭", "╮", "╰", "╯", "─", "│", "…", "•")
SAFE = Glyphs("●", "●", "×", "●", "▲", "└", "›", "●", ("·", "•", "●", "•"),
              ">", "┌", "┐", "└", "┘", "─", "│", "…", "•")
ASCII = Glyphs("*", "+", "-", "*", "!", "|", ">", "*", ("|", "/", "-", "\\"),
               ">", "+", "+", "+", "+", "-", "|", "...", "*")


def glyphs() -> Glyphs:
    return {"fancy": FANCY, "safe": SAFE}.get(tier(), ASCII)


# --------------------------------------------------------------------------- output core

_print_lock = threading.RLock()
_active: "Spinner | None" = None
_capture: list[str] | None = None
# Set by the line editor while it owns the prompt; background notifications
# (guard, inbox) are printed above the input box and the box is redrawn.
_editor = None


def set_editor(editor) -> None:
    global _editor
    _editor = editor


def capture_begin() -> None:
    global _capture
    _capture = []


def capture_end() -> list[str]:
    global _capture
    lines, _capture = _capture or [], None
    return lines


def _clear_line() -> None:
    if use_color():
        sys.stdout.write("\r\x1b[2K")
    else:
        sys.stdout.write("\r" + " " * (width() - 1) + "\r")


# tee(line) receives every printed line (ANSI stripped) outside capture mode;
# the app and CLI use it to feed the shared activity journal (sync.journal).
tee = None


def _emit(line: str) -> None:
    if _capture is not None:
        _capture.append(_ANSI.sub("", line))
    elif tee is not None:
        try:
            tee(_ANSI.sub("", line))
        except Exception:
            pass
    with _print_lock:
        if _active is not None and _active.drawn:
            _clear_line()
            _active.drawn = False
        editing = _editor is not None and _editor.active
        if editing:
            _editor.erase()
        print(line, flush=True)
        if editing:
            _editor.redraw()


# --------------------------------------------------------------------------- spinner

class Spinner:
    """Animated status line: ``with ui.task("Starting tunnel") as t:``."""

    def __init__(self, text: str):
        self.text = text
        self.drawn = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start = time.monotonic()
        self._animate = _isatty(sys.stdout)

    def update(self, text: str) -> None:
        log.event("debug", f"   … {text}")
        self.text = text

    def _frame(self, i: int) -> str:
        g = glyphs()
        frames = g.spinner
        # Claude's glyph breathes forward then back
        seq = frames if len(frames) > 4 else frames
        f = seq[(i // 1) % len(seq)]
        secs = int(time.monotonic() - self._start)
        tail = f"({secs}s · ctrl+c to interrupt)" if secs >= 2 else ""
        if use_color():
            return f"{color(f, C.claude)} {shimmer(self.text + g.ellipsis, i)} {color(tail, C.muted)}"
        return f"{f} {self.text}{g.ellipsis} {tail}"

    def _run(self) -> None:
        i = 0
        while not self._stop.is_set():
            with _print_lock:
                _clear_line()
                sys.stdout.write(self._frame(i))
                sys.stdout.flush()
                self.drawn = True
            i += 1
            self._stop.wait(0.11)

    def __enter__(self) -> "Spinner":
        global _active
        log.event("info", f"== {self.text}")
        with _print_lock:
            _active = self
        if self._animate:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            print(f"{glyphs().info} {self.text}{glyphs().ellipsis}", flush=True)
        return self

    def stop(self) -> None:
        global _active
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        with _print_lock:
            if self.drawn:
                _clear_line()
                sys.stdout.flush()
                self.drawn = False
            _active = None

    def __exit__(self, *exc) -> None:
        self.stop()

    def done(self, msg: str | None = None) -> None:
        self.stop()
        ok(msg or self.text)

    def fail(self, msg: str | None = None) -> None:
        self.stop()
        err(msg or self.text)


def task(text: str) -> Spinner:
    return Spinner(text)


# --------------------------------------------------------------------------- messages

def _line(glyph: str, code: str, msg: str, level: str) -> None:
    log.event(level, f"[{glyph}] {msg}")
    _emit(f"{color(glyph, code)} {msg}")


def ok(msg: str) -> None:
    _line(glyphs().ok, C.success, msg, "info")


def info(msg: str) -> None:
    _line(glyphs().info, C.text, msg, "info")


def warn(msg: str) -> None:
    _line(glyphs().warn, C.warning, msg, "warning")


def err(msg: str) -> None:
    _line(glyphs().fail, C.error + C.bold, msg, "error")


def detail(msg: str) -> None:
    """Indented sub-line belonging to the previous message (Claude's ⎿)."""
    log.event("info", f"    {msg}")
    _emit(f"  {color(glyphs().sub, C.muted)} {msg}")


def hint(msg: str) -> None:
    log.event("info", f"    hint: {msg}")
    _emit(f"  {color(msg, C.muted)}")


def step(n: int, total: int, msg: str) -> None:
    log.event("info", f"== [{n}/{total}] {msg}")
    _emit("")
    _emit(f"{color(glyphs().step, C.claude)} {color(msg, C.bold)} {color(f'{n}/{total}', C.muted)}")


def heading(msg: str) -> None:
    log.event("info", f"## {msg}")
    _emit("")
    _emit(f"{color(glyphs().step, C.claude)} {color(msg, C.bold)}")


def kv(key: str, value, key_width: int = 22) -> None:
    log.event("info", f"  {key}: {value}")
    pad = " " * max(1, key_width - len(key))
    _emit(f"  {color(key, C.muted)}{pad}{value}")


def blank() -> None:
    _emit("")


def mask(secret: str) -> str:
    return glyphs().mask * len(secret) if secret else "(not set)"


def rule(title: str = "", code: str | None = None) -> None:
    g = glyphs()
    w = min(width(), 78)
    if title:
        head = f"{g.h * 2} "
        tail = g.h * max(0, w - len(title) - 4)
        _emit(f"{color(head, C.muted)}{color(title, (code or C.claude) + C.bold)} {color(tail, C.muted)}")
    else:
        _emit(color(g.h * w, C.muted))


def box(lines: list[str], title: str = "", code: str | None = None, min_width: int = 50) -> None:
    """Rounded box (Claude's welcome panel). Lines may contain ANSI colour."""
    g = glyphs()
    code = code or C.claude
    inner = max([min_width] + [visible_len(x) + 2 for x in lines] + [len(title) + 4])
    inner = min(inner, width() - 2)
    top = g.h * inner
    if title:
        t = f" {title} "
        top = g.h + t + g.h * max(0, inner - len(t) - 1)
        top_line = color(g.tl + g.h, code) + color(t, code + C.bold) + color(g.h * max(0, inner - len(t) - 1) + g.tr, code)
    else:
        top_line = color(g.tl + top + g.tr, code)
    _emit(top_line)
    for x in lines:
        pad = " " * max(0, inner - 1 - visible_len(x))
        _emit(f"{color(g.v, code)} {x}{pad}{color(g.v, code)}")
    _emit(color(g.bl + g.h * inner + g.br, code))


def table(headers: list[str], rows: list[list[str]], indent: int = 2) -> None:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(str(cell)))
    pad = " " * indent
    _emit(pad + color("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)), C.muted))
    for r in rows:
        _emit(pad + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)))


LOGO = [
    r" _      ___         ____          __ ",
    r"| | /| / (_)______ / __/__  ___  / /_",
    r"| |/ |/ / / __/ -_)\ \/ _ \/ _ \/ __/",
    r"|__/|__/_/_/  \__/___/ .__/\___/\__/ ",
    r"                    /_/              ",
]


def banner(app: str, version: str, tagline: str, rows: list[str] | None = None) -> None:
    for line in LOGO:
        print(gradient(line) if use_color() else line)
    g = glyphs()
    lines = [f"{color(g.step, C.claude)} Welcome to {color(app, C.bold)}  {color('v' + version, C.muted)}",
             color(tagline, C.muted)]
    if rows:
        lines.append("")
        lines += rows
    box(lines)


# --------------------------------------------------------------------------- choices

@dataclass
class Choice:
    key: str
    label: str
    hint: str = ""


def _read_key() -> str:
    import msvcrt  # Windows only; callers guard with os.name

    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return {"H": "up", "P": "down"}.get(code, "")
    if ch == "\r":
        return "enter"
    if ch == "\x1b":
        return "esc"
    if ch == "\x03":
        raise KeyboardInterrupt
    return ch


def choose(question: str, choices: list[Choice], default: int = 0, cancel: str | None = None) -> str:
    """Claude-style numbered menu. The default is marked 'recommended'.

    Returns the chosen Choice.key; ``cancel`` is returned for Esc."""
    g = glyphs()
    log.event("info", f"?? {question} options={[c.key for c in choices]} default={choices[default].key}")
    if AUTO_YES or not INTERACTIVE or not _isatty(sys.stdin):
        pick = choices[default]
        _emit(f"{color('?', C.claude)} {question}")
        _emit(f"  {color(g.arrow, C.claude)} {pick.label} {color('(auto: recommended)', C.muted)}")
        log.event("info", f"?? auto -> {pick.key}")
        return pick.key

    _emit("")
    _emit(f"{color('?', C.claude)} {color(question, C.bold)}")
    arrow_ui = os.name == "nt" and use_color()
    if not arrow_ui:
        for i, c in enumerate(choices, 1):
            rec = "  (recommended)" if i - 1 == default else ""
            _emit(f"   {i}. {c.label}{rec}")
            if c.hint:
                _emit(f"      {c.hint}")
        while True:
            raw = input(f"   Choose 1-{len(choices)} [{default + 1}]: ").strip()
            if not raw:
                idx = default
            elif raw.isdigit() and 1 <= int(raw) <= len(choices):
                idx = int(raw) - 1
            else:
                continue
            log.event("info", f"?? user -> {choices[idx].key}")
            return choices[idx].key

    idx = default
    n_lines = len(choices) + 2

    def render(first: bool) -> None:
        lines = []
        for i, c in enumerate(choices):
            rec = color("  recommended", C.claude) if i == default else ""
            num = f"{i + 1}."
            if i == idx:
                lines.append(f"  {color(g.arrow, C.claude)} {color(num + ' ' + c.label, C.suggestion + C.bold)}{rec}")
            else:
                lines.append(f"    {color(num, C.muted)} {c.label}{rec}")
        sel = choices[idx]
        lines.append(f"    {color(sel.hint, C.muted)}" if sel.hint else "")
        lines.append(color("    ↑↓ to move · enter to select · 1-9 to pick · esc to cancel" if fancy()
                           else "    up/down move · enter select · 1-9 pick · esc cancel", C.muted))
        with _print_lock:
            if not first:
                sys.stdout.write(f"\x1b[{n_lines}F")
            for line in lines:
                sys.stdout.write("\x1b[2K" + line + "\n")
            sys.stdout.flush()

    render(True)
    while True:
        key = _read_key()
        if key == "up":
            idx = (idx - 1) % len(choices)
        elif key == "down":
            idx = (idx + 1) % len(choices)
        elif key.isdigit() and 1 <= int(key) <= len(choices):
            idx = int(key) - 1
            render(False)
            break
        elif key == "enter":
            break
        elif key == "esc" and cancel:
            log.event("info", f"?? user cancelled -> {cancel}")
            return cancel
        render(False)
    log.event("info", f"?? user -> {choices[idx].key}")
    return choices[idx].key


def confirm(question: str, default: bool = True) -> bool:
    return choose(question, [Choice("yes", "Yes"), Choice("no", "No")], 0 if default else 1, cancel="no") == "yes"


def ask_secret(label: str) -> str:
    import getpass

    return getpass.getpass(f"  {label}: ")
