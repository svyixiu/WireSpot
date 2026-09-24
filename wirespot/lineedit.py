"""Claude-style input box with a live autocomplete menu (Windows console).

    ╭──────────────────────────────────────────────╮
    │ > sta                                         │
    ╰──────────────────────────────────────────────╯
      ❯ start [profile] [options]   Connect VPN, start the hotspot…
        status                      Show VPN, hotspot, sharing…

Keys: type to filter · ↑/↓ move in the menu (or walk history when it is
closed) · Tab / → accept · Enter runs (or accepts a partial command first)
· Esc closes the menu / clears the line · Ctrl+W deletes a word.

History is persisted without secrets. Background notifications print above
the box and the box is redrawn. Consoles without VT support fall back to
plain input().
"""
from __future__ import annotations

import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import ui

SECRET_LINE = re.compile(r"(?i)^\s*set\s+password\b")
MENU_ROWS = 7


@dataclass
class Suggestion:
    value: str          # full line after accepting
    label: str          # left column
    desc: str = ""      # right column (muted)
    runnable: bool = True   # Enter on an accepted exact match may run it


class LineEditor:
    def __init__(self, completer: Callable[[str], list], history_path: Path | None = None, max_history: int = 500):
        self.completer = completer
        self.history_path = history_path
        self.max_history = max_history
        self.history: list[str] = self._load()
        self.active = False
        self.drawn = False
        self.rows_below = 0
        self.buf: list[str] = []
        self.pos = 0
        self.menu: list[Suggestion] = []
        self.sel = 0
        self.menu_closed_for = None     # text for which Esc closed the menu
        self.status = ""
        self.runnable: Callable[[str], bool] = lambda text: False
        self.idle: Callable[[], None] | None = None     # called ~every 50 ms while waiting for keys
        self.drawn_width = 0
        self.drawn_col = 0
        ui.set_editor(self)

    # ------------------------------------------------------------ history
    def _load(self) -> list[str]:
        if not self.history_path:
            return []
        try:
            lines = self.history_path.read_text(encoding="utf-8").splitlines()
            return [ln for ln in lines if ln.strip() and not SECRET_LINE.match(ln)][-self.max_history:]
        except OSError:
            return []

    def remember(self, line: str) -> None:
        if not line.strip() or SECRET_LINE.match(line):
            return
        if self.history and self.history[-1] == line:
            return
        self.history.append(line)
        self.history = self.history[-self.max_history:]
        if self.history_path:
            try:
                self.history_path.parent.mkdir(parents=True, exist_ok=True)
                self.history_path.write_text("\n".join(self.history) + "\n", encoding="utf-8")
            except OSError:
                pass

    # ------------------------------------------------------------ suggestions
    @property
    def text(self) -> str:
        return "".join(self.buf)

    def _refresh_menu(self) -> None:
        text = self.text
        if not text.strip() or self.pos != len(self.buf) or self.menu_closed_for == text:
            self.menu = []
            return
        items = []
        for s in self.completer(text) or []:
            items.append(s if isinstance(s, Suggestion) else Suggestion(str(s), str(s)))
        # hide a menu that only repeats exactly what is typed
        if len(items) == 1 and items[0].value.rstrip() == text.rstrip():
            items = []
        self.menu = items
        self.sel = min(self.sel, len(items) - 1) if items else 0

    def _ghost(self) -> str:
        text = self.text
        if self.menu or not text or self.pos != len(self.buf):
            return ""
        for h in reversed(self.history):
            if h.startswith(text) and h != text:
                return h[len(text):]
        return ""

    # ------------------------------------------------------------ rendering
    def erase(self) -> None:
        """Remove the box + menu from the screen; the cursor ends where the box began."""
        if not self.drawn:
            return
        sys.stdout.write(f"\x1b[{self._rows_above()}A\r\x1b[J")
        sys.stdout.flush()
        self.drawn = False

    def _rows_above(self) -> int:
        """Rows between the cursor and the top of the box. The box was drawn ``drawn_width``
        columns wide; if the window got narrower since, the terminal re-wrapped those lines."""
        cols = ui.width() + 1
        if self.drawn_width < cols:
            return 1
        return self.drawn_col // cols + math.ceil(self.drawn_width / cols)

    def check_resize(self) -> None:
        """Redraw the box at the new width when the console is resized (full screen, snap…)."""
        if self.active and self.drawn and ui.width() != self.drawn_width:
            self.redraw()

    def redraw(self) -> None:
        if not self.active:
            return
        g = ui.glyphs()
        w = ui.width()
        inner = w - 2
        prompt = f"{g.prompt} "
        room = max(10, inner - 2 - len(prompt))
        text = self.text
        start = max(0, self.pos - room + 1)
        visible = text[start:start + room]
        ghost = self._ghost()[: max(0, room - len(visible))]
        pad = " " * max(0, room - len(visible) - len(ghost))
        border = ui.C.muted
        top = ui.color(g.tl + g.h * inner + g.tr, border)
        mid = (ui.color(g.v, border) + " " + ui.color(prompt, ui.C.text) + visible
               + (ui.color(ghost, ui.C.muted) if ghost else "") + pad + " " + ui.color(g.v, border))
        bottom = ui.color(g.bl + g.h * inner + g.br, border)
        below = [bottom] + self._menu_lines(w)
        out = []
        if self.drawn:
            out.append(f"\x1b[{self._rows_above()}A\r")
        out.append("\x1b[J")          # wipe the old box/menu first so no text is left behind
        out.append(top + "\n" + mid)
        for line in below:
            out.append("\n" + line)
        out.append("\x1b[J")
        if below:
            out.append(f"\x1b[{len(below)}A")
        col = 2 + len(prompt) + (self.pos - start)
        out.append(f"\r\x1b[{col}C" if col else "\r")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self.drawn = True
        self.drawn_width, self.drawn_col = w, col
        self.rows_below = len(below)

    def _menu_lines(self, w: int) -> list[str]:
        g = ui.glyphs()
        if not self.menu:
            left = f"  {self.status}" if self.status else ""
            right = "help for commands · tab completes · ↑↓ history" if ui.fancy() else \
                "help for commands · tab completes · up/down history"
            gap = max(2, w - ui.visible_len(left) - len(right) - 2)
            return [left + " " * gap + ui.color(right, ui.C.muted)]
        top = max(0, min(self.sel - MENU_ROWS + 1, len(self.menu) - MENU_ROWS))
        shown = self.menu[top:top + MENU_ROWS]
        lw = min(34, max(len(s.label) for s in shown) + 2)
        lines = []
        for i, s in enumerate(shown, start=top):
            label = s.label[:lw].ljust(lw)
            desc = s.desc[: max(0, w - lw - 8)]
            if i == self.sel:
                lines.append(f"  {ui.color(g.arrow, ui.C.claude)} {ui.color(label, ui.C.suggestion + ui.C.bold)} "
                             f"{ui.color(desc, ui.C.text)}")
            else:
                lines.append(f"    {label} {ui.color(desc, ui.C.muted)}")
        more = len(self.menu) - (top + len(shown))
        if more > 0:
            lines.append(ui.color(f"    … {more} more", ui.C.muted))
        return lines

    def _finish(self, echo: str) -> None:
        self.erase()
        self.active = False
        if echo.strip():
            sys.stdout.write(ui.color("> ", ui.C.muted) + ui.color(echo, ui.C.muted) + "\n")
        sys.stdout.flush()

    # ------------------------------------------------------------ input
    def read(self, fallback_prompt: str = "wirespot> ", status: str = "") -> str:
        if os.name != "nt" or not ui.use_color() or not ui._isatty(sys.stdin):
            return input(fallback_prompt)
        import msvcrt

        self.buf, self.pos, self.sel = [], 0, 0
        self.menu, self.menu_closed_for = [], None
        self.status = status
        hist_i = len(self.history)
        draft = ""
        self.active = True
        with ui._print_lock:
            self.redraw()
        try:
            while True:
                if not msvcrt.kbhit():
                    time.sleep(0.03)
                    with ui._print_lock:
                        self.check_resize()
                    if self.idle:
                        self.idle()
                    continue
                ch = msvcrt.getwch()
                with ui._print_lock:
                    if ch in ("\x00", "\xe0"):
                        code = msvcrt.getwch()
                        if code in ("H", "P") and self.menu:                      # menu navigation
                            self.sel = (self.sel + (-1 if code == "H" else 1)) % len(self.menu)
                            self.redraw()
                            continue
                        if code == "K" and self.pos > 0:
                            self.pos -= 1
                        elif code == "M":
                            if self.pos < len(self.buf):
                                self.pos += 1
                            elif self.menu:
                                self._accept()
                            else:
                                self.buf.extend(self._ghost())
                                self.pos = len(self.buf)
                        elif code == "G":
                            self.pos = 0
                        elif code == "O":
                            self.pos = len(self.buf)
                        elif code == "S" and self.pos < len(self.buf):
                            del self.buf[self.pos]
                        elif code in ("H", "P") and self.history:                 # history
                            if hist_i == len(self.history):
                                draft = self.text
                            hist_i = max(0, hist_i - 1) if code == "H" else min(len(self.history), hist_i + 1)
                            line = self.history[hist_i] if hist_i < len(self.history) else draft
                            self.buf, self.pos = list(line), len(line)
                            self.menu_closed_for = line
                    elif ch == "\r":
                        if self.menu and self._should_accept_on_enter():
                            self._accept()
                        else:
                            line = self.text
                            self._finish(line)
                            return line
                    elif ch == "\x03":
                        self._finish("")
                        raise KeyboardInterrupt
                    elif ch in ("\x04", "\x1a") and not self.buf:
                        self._finish("")
                        raise EOFError
                    elif ch == "\x08":
                        if self.pos > 0:
                            del self.buf[self.pos - 1]
                            self.pos -= 1
                        self.menu_closed_for = None
                    elif ch == "\x1b":
                        if self.menu:
                            self.menu_closed_for = self.text
                        else:
                            self.buf, self.pos = [], 0
                    elif ch == "\t":
                        if self.menu:
                            self._accept()
                        else:
                            self.buf.extend(self._ghost())
                            self.pos = len(self.buf)
                    elif ch == "\x17":
                        left = self.text[: self.pos].rstrip()
                        cut = left.rfind(" ") + 1
                        self.buf = list(left[:cut]) + self.buf[self.pos:]
                        self.pos = cut
                    elif ch >= " ":
                        self.buf.insert(self.pos, ch)
                        self.pos += 1
                        self.menu_closed_for = None
                        self.sel = 0
                    self._refresh_menu()
                    self.redraw()
        finally:
            if self.active:
                self._finish("")

    def _should_accept_on_enter(self) -> bool:
        """Enter on a partial word fills in the highlighted suggestion first
        (so 'sto' never silently runs 'stop'); Enter on a complete line runs it."""
        text = self.text.rstrip()
        if self.runnable(text) or any(s.value.rstrip() == text for s in self.menu):
            return False
        return True

    def _accept(self) -> None:
        s = self.menu[self.sel]
        self.buf = list(s.value)
        self.pos = len(self.buf)
        self.sel = 0
        self.menu_closed_for = None
