"""Reusable Tkinter widgets."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from time import monotonic
from typing import Callable

from PIL import Image, ImageDraw, ImageTk


class AnimatedToggle(tk.Canvas):
    """A compact switch whose caller approves and persists each state change."""

    def __init__(
        self, parent, *, value: bool, command: Callable[[], None], bg: str,
        accent: str = '#00E5A8', muted: str = '#A7ABB7',
        text_color: str = '#F4F6F8',
    ) -> None:
        super().__init__(
            parent, width=82, height=32, bg=bg, bd=0, takefocus=1,
            highlightthickness=0, cursor='hand2',
        )
        self._value = bool(value)
        self._command = command
        self._position = float(self._value)
        self._after_id = None
        self._destroyed = False
        self._mouse_focus = False
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
        # Draw curves at 4x size, then reduce them to blend edge pixels.
        scale = 4
        knob = Image.new('RGBA', (18 * scale, 18 * scale))
        ImageDraw.Draw(knob).ellipse(
            (0, 0, 18 * scale - 1, 18 * scale - 1),
            fill=tuple(component // 257 for component in self.winfo_rgb(text_color)),
        )
        self._knob_image = ImageTk.PhotoImage(
            knob.resize((18, 18), Image.Resampling.LANCZOS), master=self,
        )
        self.create_image(34, 2, anchor='nw', tags='track')
        self.create_image(39, 7, image=self._knob_image, anchor='nw', tags='knob')
        self._render()
        self.bind('<Button-1>', self._activate)
        self.bind('<space>', self._activate)
        self.bind('<Return>', self._activate)
        self.bind('<FocusIn>', self._on_focus)
        self.bind('<FocusOut>', self._on_focus)
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

    def _activate(self, event) -> str:
        self._mouse_focus = event.type == tk.EventType.ButtonPress
        self.focus_set()
        self._render()
        self.invoke()
        return 'break'

    def _on_focus(self, event) -> None:
        if self._destroyed:
            return
        if event.type == tk.EventType.FocusOut:
            self._mouse_focus = False
        self._render()

    def _render(self) -> None:
        left = 39 + 20 * self._position
        self.coords('knob', left, 7)
        color = tuple(
            round(off + (on - off) * self._position)
            for off, on in zip(self._off_rgb, self._accent_rgb)
        )
        scale = 4
        track = Image.new('RGBA', (48 * scale, 28 * scale))
        drawing = ImageDraw.Draw(track)
        if not self._mouse_focus and self.focus_get() is self:
            drawing.rounded_rectangle(
                (0, 0, 48 * scale - 1, 28 * scale - 1),
                radius=14 * scale, outline=self._accent_rgb, width=scale,
            )
        drawing.rounded_rectangle(
            (2 * scale, 2 * scale, 46 * scale - 1, 26 * scale - 1),
            radius=12 * scale, fill=color,
        )
        self._track_image = ImageTk.PhotoImage(
            track.resize((48, 28), Image.Resampling.LANCZOS), master=self,
        )
        self.itemconfigure('track', image=self._track_image)
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


class ChannelOptionsMenu(tk.Toplevel):
    """A small channel menu that stays inside the app and owns its input grab."""

    def __init__(
        self, parent, *, interval: int, edit_command: Callable[[], None],
        delete_command: Callable[[], None], bg: str, text_color: str,
        muted: str, danger: str, refresh_command: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, bg='#414550', bd=0, highlightthickness=0)
        self.withdraw()
        self.overrideredirect(True)
        self._owner = parent.winfo_toplevel()
        self.transient(self._owner)
        self._closed = False
        self._previous_focus = None
        self._previous_grab = None
        self._previous_grab_status = None
        self._focus_after_id = None
        self._commands = ((refresh_command,) if refresh_command is not None else ()) + (edit_command, delete_command)
        self._buttons = []
        self._active_index = 0
        self._bg = bg
        self._hover_bg = '#3A3D47'

        body = tk.Frame(self, bg=bg, padx=8, pady=8)
        body.pack(fill='both', expand=True, padx=1, pady=1)
        metadata = tk.Frame(body, bg=bg)
        metadata.pack(fill='x', padx=8, pady=(3, 6))
        tk.Label(
            metadata, text='확인 간격', bg=bg, fg=muted,
            font=('Malgun Gothic', 9),
        ).pack(side='left')
        tk.Label(
            metadata, text=f'{interval}초마다', bg=bg, fg=muted,
            font=('Malgun Gothic', 9),
        ).pack(side='right')
        tk.Frame(body, bg='#414550', height=1).pack(fill='x', padx=8, pady=(0, 6))
        entries = ([('지금 확인', text_color)] if refresh_command is not None else []) + [
            ('확인 간격 수정', text_color), ('채널 삭제', danger),
        ]
        for index, (label, color) in enumerate(entries):
            button = tk.Button(
                body, text=label, command=lambda value=index: self._invoke(value),
                bg=bg, fg=color, activebackground=self._hover_bg, activeforeground=color,
                font=('Malgun Gothic', 10), anchor='w', padx=10, pady=7,
                relief='flat', bd=0, highlightthickness=0, takefocus=1, cursor='hand2',
            )
            button.pack(fill='x')
            button.bind('<Enter>', lambda _event, value=index: self._select(value, focus=False))
            button.bind('<FocusIn>', lambda _event, value=index: self._select(value, focus=False))
            self._buttons.append(button)

        self.bind('<Escape>', self._dismiss)
        self.bind('<MouseWheel>', self._dismiss)
        self.bind('<Button-4>', self._dismiss)
        self.bind('<Button-5>', self._dismiss)
        self.bind('<ButtonPress>', self._on_click)
        self.bind('<FocusOut>', self._on_focus_out)
        self.bind('<Down>', lambda _event: self._move(1))
        self.bind('<Up>', lambda _event: self._move(-1))
        self.bind('<Tab>', lambda _event: self._move(1))
        self.bind('<Shift-Tab>', lambda _event: self._move(-1))
        self.bind('<Return>', lambda _event: self._invoke(self._active_index))

    def show(self, anchor: tk.Widget) -> None:
        """Align to the row's right edge, flipping upward near the app's bottom."""
        if self._closed:
            return
        self._owner.update_idletasks()
        self.update_idletasks()
        width, height = max(190, self.winfo_reqwidth()), self.winfo_reqheight()
        left, top = self._owner.winfo_rootx() + 8, self._owner.winfo_rooty() + 8
        right = self._owner.winfo_rootx() + self._owner.winfo_width() - 8
        bottom = self._owner.winfo_rooty() + self._owner.winfo_height() - 8
        x = anchor.winfo_rootx() + anchor.winfo_width() - width - 16
        y = anchor.winfo_rooty() + anchor.winfo_height() + 4
        if y + height > bottom:
            y = anchor.winfo_rooty() - height - 4
        x, y = max(left, min(x, right - width)), max(top, min(y, bottom - height))
        self.geometry(f'{width}x{height}+{x}+{y}')
        self._previous_focus = self._owner.focus_get()
        self._previous_grab = self._owner.grab_current()
        if self._previous_grab is not None:
            self._previous_grab_status = self._previous_grab.grab_status()
        self.deiconify()
        self.lift()
        self.grab_set()
        self._buttons[0].focus_force()

    def close(self) -> None:
        """Release menu input before a caller opens an editor or confirmation."""
        if self._closed:
            return
        self._closed = True
        if self._focus_after_id is not None:
            self.after_cancel(self._focus_after_id)
            self._focus_after_id = None
        focus = self.focus_get()
        restore_focus = focus is not None and focus.winfo_toplevel() is self
        if self.grab_current() is self:
            self.grab_release()
            previous_grab = self._previous_grab
            if previous_grab is not None and previous_grab.winfo_exists():
                if self._previous_grab_status == 'global':
                    previous_grab.grab_set_global()
                else:
                    previous_grab.grab_set()
        super().destroy()
        if restore_focus and self._previous_focus is not None and self._previous_focus.winfo_exists():
            self._previous_focus.focus_force()

    def destroy(self) -> None:
        self.close()

    def _select(self, index: int, *, focus: bool = True) -> None:
        self._active_index = index
        for position, button in enumerate(self._buttons):
            button.configure(bg=self._hover_bg if position == index else self._bg)
        if focus:
            self._buttons[index].focus_set()

    def _move(self, direction: int) -> str:
        self._select((self._active_index + direction) % len(self._buttons))
        return 'break'

    def _invoke(self, index: int) -> str:
        if not self._closed:
            command = self._commands[index]
            self.close()
            command()
        return 'break'

    def _dismiss(self, _event=None) -> str:
        self.close()
        return 'break'

    def _on_click(self, event) -> str | None:
        if not (
            self.winfo_rootx() <= event.x_root < self.winfo_rootx() + self.winfo_width()
            and self.winfo_rooty() <= event.y_root < self.winfo_rooty() + self.winfo_height()
        ):
            return self._dismiss()
        return None

    def _on_focus_out(self, _event) -> None:
        # Switching between menu rows also sends FocusOut; check after the move.
        if not self._closed and self._focus_after_id is None:
            self._focus_after_id = self.after_idle(self._dismiss_if_focus_left)

    def _dismiss_if_focus_left(self) -> None:
        self._focus_after_id = None
        focus = self.focus_get()
        if focus is None or focus.winfo_toplevel() is not self:
            self.close()

