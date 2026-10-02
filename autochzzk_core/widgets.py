"""Reusable Tkinter widgets."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from time import monotonic
from typing import Callable


class AnimatedToggle(tk.Canvas):
    """A compact switch whose caller approves and persists each state change."""

    def __init__(
        self, parent, *, value: bool, command: Callable[[], None], bg: str,
        accent: str = '#00E5A8', muted: str = '#A7ABB7',
        text_color: str = '#F4F6F8',
    ) -> None:
        super().__init__(
            parent, width=82, height=32, bg=bg, bd=0, takefocus=1,
            highlightthickness=1, highlightbackground=bg,
            highlightcolor=accent, cursor='hand2',
        )
        self._value = bool(value)
        self._command = command
        self._position = float(self._value)
        self._after_id = None
        self._destroyed = False
        self._accent = accent
        self._muted = muted
        self._accent_rgb = tuple(component // 257 for component in self.winfo_rgb(accent))
        background_rgb = self.winfo_rgb(bg)
        muted_rgb = self.winfo_rgb(muted)
        self._off_rgb = tuple(
            round((background * 0.7 + foreground * 0.3) / 257)
            for background, foreground in zip(background_rgb, muted_rgb)
        )

        self.create_text(
            16, 16, font=('Segoe UI', 8, 'bold'), tags='label',
        )
        self.create_oval(36, 4, 60, 28, width=0, tags='track')
        self.create_rectangle(48, 4, 68, 28, width=0, tags='track')
        self.create_oval(56, 4, 80, 28, width=0, tags='track')
        self.create_oval(39, 7, 57, 25, fill=text_color, width=0, tags='knob')
        self._render()
        self.bind('<Button-1>', self._activate)
        self.bind('<space>', self._activate)
        self.bind('<Return>', self._activate)
        self.bind('<Destroy>', self._on_destroy)

    def get_value(self) -> bool:
        return self._value

    def set_value(self, value: bool, *, animate: bool = True) -> None:
        """Show a confirmed state, continuing reversals from the visible position."""
        value = bool(value)
        if self._destroyed or (value == self._value and animate):
            return
        self._cancel_animation()
        self._value = value
        target = float(value)
        if not animate or self._position == target:
            self._position = target
            self._render()
            return
        self._start_position = self._position
        self._animation_started = monotonic()
        self._render()
        self._after_id = self.after(16, self._animate)

    def invoke(self) -> None:
        """Request a change; saving and calling set_value belong to the caller."""
        self._command()

    def _activate(self, _event) -> str:
        self.focus_set()
        self.invoke()
        return 'break'

    def _render(self) -> None:
        left = 39 + 20 * self._position
        self.coords('knob', left, 7, left + 18, 25)
        color = '#{:02x}{:02x}{:02x}'.format(*(
            round(off + (on - off) * self._position)
            for off, on in zip(self._off_rgb, self._accent_rgb)
        ))
        self.itemconfigure('track', fill=color)
        self.itemconfigure(
            'label', text='ON' if self._value else 'OFF',
            fill=self._accent if self._value else self._muted,
        )

    def _animate(self) -> None:
        self._after_id = None
        if self._destroyed:
            return
        progress = min(1.0, (monotonic() - self._animation_started) / 0.16)
        eased = 1 - (1 - progress) ** 3
        target = float(self._value)
        self._position = self._start_position + (target - self._start_position) * eased
        self._render()
        if progress < 1.0:
            self._after_id = self.after(16, self._animate)

    def _cancel_animation(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._destroyed = True
            self._cancel_animation()


class MarqueeText(tk.Canvas):
    """A single-line label that scrolls left only when its text is too long."""

    def __init__(self, parent, text: str, *, fg: str, bg: str, font, height: int = 22) -> None:
        super().__init__(parent, bg=bg, height=height, highlightthickness=0, bd=0, takefocus=0)
        self.text = text
        self.fg = fg
        self.text_font = tkfont.Font(font=font)
        self.text_width = self.text_font.measure(text)
        self.item = self.create_text(0, height // 2, text=text, fill=fg, font=font, anchor="w")
        self.scrolling = False
        self.after_id = None
        self.bind("<Configure>", self._fit_text)

    def set_text(self, text: str, *, fg: str | None = None) -> None:
        """Update the displayed value without replacing the canvas widget."""
        next_fg = self.fg if fg is None else fg
        if text == self.text and next_fg == self.fg:
            return
        if self.after_id is not None:
            try:
                self.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        self.text = text
        self.fg = next_fg
        self.text_width = self.text_font.measure(text)
        self.itemconfigure(self.item, text=text, fill=next_fg)
        self.scrolling = False
        self.coords(self.item, 0, self.winfo_height() // 2)
        self._fit_text()

    def _fit_text(self, _event=None) -> None:
        if not self.winfo_exists():
            return
        if self.text_width <= self.winfo_width():
            self.scrolling = False
            self.coords(self.item, 0, self.winfo_height() // 2)
            return
        self.scrolling = True
        if self.after_id is None:
            self.after_id = self.after(700, self._scroll)

    def _scroll(self) -> None:
        self.after_id = None
        try:
            if not self.winfo_exists() or not self.scrolling:
                return
            x, y = self.coords(self.item)
            x -= 1
            if x + self.text_width < 0:
                x = self.winfo_width() + 12
            self.coords(self.item, x, y)
            self.after_id = self.after(35, self._scroll)
        except tk.TclError:
            return

