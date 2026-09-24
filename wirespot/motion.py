"""Motion helpers shared by the window, the tray panel, toasts and setup:
eased tweens on the Tk clock and a smooth-scrolling container."""
from __future__ import annotations

import time
import tkinter as tk

from .theme import BG

FRAME_MS = 12


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def lerp_color(a: str, b: str, t: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(ca[k] + (cb[k] - ca[k]) * t) for k in range(3))


class Animator:
    """Frame-driven tweens keyed by name (a new tween with the same key replaces the old one)."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self.jobs: dict = {}

    def run(self, key, duration_ms: int, step, done=None, curve=ease_out) -> None:
        self.cancel(key)
        start = time.perf_counter()

        def tick():
            t = min(1.0, (time.perf_counter() - start) * 1000 / duration_ms)
            try:
                step(curve(t))
            except tk.TclError:
                self.jobs.pop(key, None)
                return
            if t < 1.0:
                self.jobs[key] = self.root.after(FRAME_MS, tick)
            else:
                self.jobs.pop(key, None)
                if done:
                    done()

        tick()

    def cancel(self, key) -> None:
        job = self.jobs.pop(key, None)
        if job:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass

    def active(self, key) -> bool:
        return key in self.jobs

    def fade(self, widgets, a: str, b: str, ms: int = 110, key=None) -> None:
        key = key or ("fade", id(widgets[0]))

        def step(t):
            c = lerp_color(a, b, t)
            for w in widgets:
                w.configure(bg=c)
        self.run(key, ms, step)


class ScrollArea(tk.Frame):
    """Vertical scroll container with eased wheel scrolling and no chunky scrollbar.
    A thin clay thumb appears while the content is taller than the view."""

    def __init__(self, master, anim: Animator, bg=BG, thumb="#3D3C38"):
        super().__init__(master, bg=bg)
        self.anim = anim
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, yscrollincrement=1)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self._region())
        self.canvas.bind("<Configure>", lambda e: (self.canvas.itemconfigure(self.win, width=e.width), self._region()))
        self.thumb = tk.Frame(self, bg=thumb, width=3)
        self.target = 0.0

    def _region(self) -> None:
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), self.inner.winfo_reqheight()))
        self._thumb()

    def _thumb(self) -> None:
        h, view = self.inner.winfo_reqheight(), self.canvas.winfo_height()
        if h <= view + 1 or view <= 1:
            self.thumb.place_forget()
            return
        top = self.canvas.yview()[0]
        th = max(24, int(view * view / h))
        self.thumb.place(relx=1.0, x=-5, y=int(top * view) + 2, height=th - 4)

    def wheel(self, delta: int) -> None:
        h = self.inner.winfo_reqheight()
        view = self.canvas.winfo_height()
        if h <= view:
            return
        start = self.canvas.yview()[0]
        base = self.target if self.anim.active(("scroll", id(self))) else start
        self.target = min(max(0.0, base - delta / 120 * 110 / h), 1 - view / h)
        end = self.target

        def step(t):
            self.canvas.yview_moveto(start + (end - start) * t)
            self._thumb()
        self.anim.run(("scroll", id(self)), 240, step)

    def to_top(self) -> None:
        self.canvas.yview_moveto(0)
        self._thumb()


def hover(group: tk.Misc, widgets, on_enter, on_leave) -> None:
    """Hover for a group of widgets (a row = frame + icon + labels) as ONE area.

    Tk sends <Leave> to a row's frame when the pointer merely moves onto one of
    its own labels; reacting to that made rows blink (fade out, fade back in)
    while the mouse rested on the menu. Here on_leave only runs once the
    pointer is really outside ``group``, and on_enter only on the way in."""
    state = {"in": False}

    def inside() -> bool:
        try:
            x, y = group.winfo_pointerxy()
            w = group.winfo_containing(x, y)
        except (tk.TclError, KeyError):
            return False
        while w is not None:
            if w is group:
                return True
            w = w.master
        return False

    def enter(_):
        if not state["in"]:
            state["in"] = True
            on_enter()

    def leave(_):
        if state["in"] and not inside():
            state["in"] = False
            on_leave()

    for w in widgets:
        w.bind("<Enter>", enter, add="+")
        w.bind("<Leave>", leave, add="+")
