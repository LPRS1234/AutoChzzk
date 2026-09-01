"""Reusable Tkinter widgets."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont


class MarqueeText(tk.Canvas):
    """A single-line label that scrolls left only when its text is too long."""

    def __init__(self, parent, text: str, *, fg: str, bg: str, font, height: int = 22) -> None:
        super().__init__(parent, bg=bg, height=height, highlightthickness=0, bd=0, takefocus=0)
        self.text_width = tkfont.Font(font=font).measure(text)
        self.item = self.create_text(0, height // 2, text=text, fill=fg, font=font, anchor="w")
        self.scrolling = False
        self.after_id = None
        self.bind("<Configure>", self._fit_text)

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

