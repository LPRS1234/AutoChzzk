"""AutoChzzk - open saved CHZZK channels when they start a live broadcast."""
from __future__ import annotations

import json
from math import ceil
import os
from queue import SimpleQueue
import subprocess
import sys
import threading
import tkinter as tk
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from autochzzk_core.chrome_profiles import ProfileReadError, get_chrome_profiles
from autochzzk_core.changelog import RELEASE_NOTES
from autochzzk_core.chzzk_api import (
    extract_channel_id,
    get_channel_name,
    get_latest_release,
    get_live_status,
)
from autochzzk_core.config import (
    APP_NAME,
    APP_VERSION,
    EXTENSION_CONNECTION_GRACE_SECONDS,
    EXTENSION_INITIAL_SYNC_SECONDS,
    EXTENSION_LAUNCH_CONNECTION_GRACE_SECONDS,
    EXTENSION_RELOAD_GRACE_SECONDS,
    ICO_PATH,
    LIVE_URL,
    LOGO_PATH,
    MUTEX_NAME,
    REQUIRED_EXTENSION_VERSION,
    UPDATE_CHECK_INTERVAL_SECONDS,
    enable_windows_dpi_awareness,
)
from autochzzk_core.extension import (
    CHROME_TABS,
    clear_show_window_callback,
    get_pairing_secret,
    request_show_window,
    start_extension_server,
)
from autochzzk_core.storage import StorageError, load_channels, load_settings, save_channels, save_settings
from autochzzk_core.monitor import LookupPool
from autochzzk_core.updater import (
    UpdateCancelled,
    UpdateError,
    UpdateInfo,
    download_update,
    find_available_update,
    get_release_version,
    launch_installer,
    verify_installer,
)
from autochzzk_core.widgets import AnimatedToggle, ChannelOptionsMenu, MarqueeText

try:
    import pystray
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    pystray = None

class AutoChzzkApp:
    BG, SURFACE, INPUT = "#16171D", "#22242C", "#2C2F38"
    ACCENT, TEXT, MUTED, DANGER = "#00E5A8", "#F4F6F8", "#A7ABB7", "#FF6B7A"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.ui_queue = SimpleQueue()
        self.storage_errors = []
        self.channels_read_only = self.settings_read_only = False
        root.title(APP_NAME)
        root.geometry("620x650")
        root.minsize(540, 540)
        root.configure(bg=self.BG)
        self.input_value, self.status_value = tk.StringVar(), tk.StringVar()
        self.status_clear_token = 0
        self.version_value = tk.StringVar(value=f"현재 버전 {APP_VERSION} · 최신 버전 확인 중…")
        self.extension_status_value = tk.StringVar(value="Chrome 확장 프로그램 연결 확인 중…")
        self.settings = self._load_settings()
        self._scan_chrome_profiles()
        self.profile_value = tk.StringVar(value=self.selected_chrome_profile["name"])
        self._apply_selected_profile()
        self.channels = self._load_channels()
        self.was_live: dict[str, bool] = {}
        self.live_info: dict[str, tuple[bool, str]] = {}
        self.channel_rows: dict[str, tk.Frame] = {}
        self.detection_buttons: dict[str, AnimatedToggle] = {}
        self.interval_labels: dict[str, tk.Label] = {}
        self.interval_editors: dict[str, tk.Frame] = {}
        self.live_status_widgets: dict[str, MarqueeText] = {}
        self.watching_indicators: dict[str, tk.Label] = {}
        self.editing_channel_id: str | None = None
        self.last_checked = {}
        self.last_successful_check: dict[str, float] = {}
        self.check_errors: set[str] = set()
        self.check_status_widgets: dict[str, tk.Label] = {}
        self.manual_checks: set[str] = set()
        self.refresh_batch: set[str] = set()
        self.refresh_failed: set[str] = set()
        self.refresh_total = 0
        self.pause_until = 0.0
        self.lookup_pool = LookupPool()
        self.pending_additions = set()
        self.channel_generations = {}
        self.initial_checks = {channel["id"] for channel in self.channels}
        self.force_open_checks = set()
        self.retry_open_checks = set()
        self.stop_event = threading.Event()
        self.tray_icon = None
        self.active_dialog = None
        self.changelog_dialog = None
        self.update_download_in_progress = False
        self.update_prompted_version: str | None = None
        self.extension_setup_prompted = False
        self.extension_update_prompted = False
        self.extension_reload_deadline: float | None = None
        self.extension_connected = False
        self.chrome_launch_requested = False
        self.extension_connection_deadline = time.monotonic() + EXTENSION_CONNECTION_GRACE_SECONDS
        self.extension_server = start_extension_server(lambda: self._ui(self._restore_window))
        self.window_icon = None
        self.header_icon = None
        self._load_brand_icons()
        # Chrome extensions can be asleep while the desktop app starts. Wait
        # for one periodic tab report before opening any startup-detected live.
        self.allow_browser_open_after = time.monotonic() + EXTENSION_INITIAL_SYNC_SECONDS
        self._configure_styles()
        self._build_ui()
        self._start_tray_icon()
        self._refresh_list()
        root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        root.after(500, self._check_extension_connection)
        root.after(1_000, self._refresh_extension_status)
        root.after(5_000, self._check_selected_profile_exists)
        root.after(50, self._drain_ui_queue)
        root.after(1_000, self._refresh_check_indicators)
        self._monitor()
        if self.storage_errors:
            self._show_app_dialog("저장 파일 확인", "\n".join(self.storage_errors))
        self._schedule_update_check()

    def _load_brand_icons(self) -> None:
        if not LOGO_PATH.is_file(): return
        try:
            if ICO_PATH.is_file(): self.root.iconbitmap(default=str(ICO_PATH))
            self.window_icon = tk.PhotoImage(file=LOGO_PATH)
            self.root.iconphoto(True, self.window_icon)
            header_image = Image.open(LOGO_PATH).convert("RGBA")
            header_image.thumbnail((34, 34), Image.Resampling.LANCZOS)
            self.header_icon = ImageTk.PhotoImage(header_image)
        except (tk.TclError, OSError):
            self.window_icon = None
            self.header_icon = None

    def _configure_styles(self) -> None:
        style = ttk.Style(); style.theme_use("clam")
        style.configure("Accent.TButton", background=self.ACCENT, foreground="#08251D", borderwidth=0, font=("Malgun Gothic", 10, "bold"), padding=(13, 9))
        style.map("Accent.TButton", background=[("active", "#38EDBB")])
        style.configure("Dark.TButton", background="#3A3D47", foreground=self.TEXT, borderwidth=0, font=("Malgun Gothic", 9, "bold"), padding=(10, 7))
        style.map("Dark.TButton", background=[("active", "#50545F")])
        style.configure("Small.TButton", background="#3A3D47", foreground=self.TEXT, borderwidth=0, font=("Malgun Gothic", 8, "bold"), padding=(5, 5))
        style.map("Small.TButton", background=[("active", "#50545F")])
        style.configure("HeaderRefresh.TButton", background=self.BG, foreground=self.TEXT, borderwidth=0, font=("Malgun Gothic", 8, "bold"), padding=(3, 3))
        style.map("HeaderRefresh.TButton", background=[("active", self.BG), ("pressed", self.BG)])
        style.configure("Exit.TButton", background=self.INPUT, foreground=self.TEXT, borderwidth=0, font=("Malgun Gothic", 9), padding=(10, 5))
        style.map("Exit.TButton", background=[("active", "#3A3D47"), ("pressed", "#3A3D47")])
        style.configure("SmallAccent.TButton", background=self.ACCENT, foreground="#08251D", borderwidth=0, font=("Malgun Gothic", 8, "bold"), padding=(5, 5))
        style.map("SmallAccent.TButton", background=[("active", "#38EDBB")])
        style.configure("DialogAccent.TButton", background=self.ACCENT, foreground="#08251D", borderwidth=0, font=("Malgun Gothic", 9, "bold"), padding=(10, 7))
        style.map("DialogAccent.TButton", background=[("active", "#38EDBB")])
        style.configure("DialogDark.TButton", background="#3A3D47", foreground=self.TEXT, borderwidth=0, font=("Malgun Gothic", 9, "bold"), padding=(10, 7))
        style.map("DialogDark.TButton", background=[("active", "#50545F")])
        style.configure("Dark.TCombobox", fieldbackground=self.INPUT, background="#3A3D47", foreground=self.TEXT, bordercolor="#40444F", lightcolor="#40444F", darkcolor="#40444F", arrowcolor=self.TEXT, padding=5)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", self.INPUT), ("focus", self.INPUT)], background=[("active", "#50545F")], bordercolor=[("focus", self.ACCENT)], arrowcolor=[("active", self.ACCENT)])
        style.configure("Dark.Vertical.TScrollbar", background="#3A3D47", troughcolor=self.SURFACE, bordercolor=self.SURFACE, lightcolor=self.SURFACE, darkcolor=self.SURFACE, arrowcolor=self.MUTED, arrowsize=0, width=8)
        style.map("Dark.Vertical.TScrollbar", background=[("active", "#50545F"), ("pressed", self.ACCENT)])
        self.root.option_add("*TCombobox*Listbox.background", self.INPUT)
        self.root.option_add("*TCombobox*Listbox.foreground", self.TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#08251D")

    def _build_ui(self) -> None:
        self.add_dialog = None
        self.add_status_label = None
        self.channel_menu = None
        self.connection_state_value = tk.StringVar(value="확인 중")
        self.version_summary_value = tk.StringVar(value=f"v{APP_VERSION}")
        outer = tk.Frame(self.root, bg=self.BG, padx=26, pady=20)
        outer.pack(fill="both", expand=True)
        brand = tk.Frame(outer, bg=self.BG)
        brand.pack(fill="x", pady=(0, 23))
        tk.Label(brand, text="▎", fg=self.ACCENT, bg=self.BG, font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Label(brand, text=APP_NAME, fg=self.TEXT, bg=self.BG, font=("Segoe UI", 10, "bold")).pack(side="left")
        heading = tk.Frame(outer, bg=self.BG)
        heading.pack(fill="x", pady=(0, 20))
        actions = tk.Frame(heading, bg=self.BG)
        actions.pack(side="right", anchor="center")
        ttk.Button(actions, text="＋ 채널 추가", style="Accent.TButton", command=self.show_add_channel_dialog, cursor="hand2").pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="설정", style="HeaderRefresh.TButton", command=self.show_settings, cursor="hand2").pack(side="left")
        titles = tk.Frame(heading, bg=self.BG)
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(titles, text="내 채널", fg=self.TEXT, bg=self.BG, font=("Malgun Gothic", 18, "bold")).pack(anchor="w")
        self.count_label = tk.Label(titles, fg=self.MUTED, bg=self.BG, font=("Malgun Gothic", 8))
        self.count_label.pack(anchor="w", pady=(4, 0))

        monitor_controls = tk.Frame(outer, bg=self.BG)
        monitor_controls.pack(fill="x", pady=(0, 12))
        self.refresh_all_button = ttk.Button(monitor_controls, text="전체 갱신", style="Small.TButton", command=self.refresh_all_channels, cursor="hand2")
        self.refresh_all_button.pack(side="left")
        self.pause_30_button = ttk.Button(monitor_controls, text="30분 쉬기", style="Small.TButton", command=lambda: self.pause_monitoring(30), cursor="hand2")
        self.pause_30_button.pack(side="left", padx=(8, 0))
        self.pause_60_button = ttk.Button(monitor_controls, text="1시간 쉬기", style="Small.TButton", command=lambda: self.pause_monitoring(60), cursor="hand2")
        self.pause_60_button.pack(side="left", padx=(8, 0))
        self.resume_button = ttk.Button(monitor_controls, text="다시 시작", style="SmallAccent.TButton", command=self.resume_monitoring, cursor="hand2")
        self.pause_value = tk.StringVar()
        self.pause_label = tk.Label(outer, textvariable=self.pause_value, fg=self.ACCENT, bg=self.BG, font=("Malgun Gothic", 9), anchor="w")
        self.monitor_controls = monitor_controls

        self.extension_notice = tk.Frame(outer, bg=self.SURFACE, padx=13, pady=10)
        ttk.Button(self.extension_notice, text="연결 설정", style="Small.TButton", command=self.show_settings, cursor="hand2").pack(side="right", padx=(10, 0))
        tk.Label(self.extension_notice, textvariable=self.extension_status_value, fg=self.DANGER, bg=self.SURFACE, font=("Malgun Gothic", 8), wraplength=340, justify="left").pack(side="left", fill="x", expand=True)
        self.list_heading = tk.Frame(outer, bg=self.BG)
        self.list_heading.pack(fill="x", pady=(0, 8))
        tk.Label(self.list_heading, text="채널 / 방송 상태", fg=self.MUTED, bg=self.BG, font=("Malgun Gothic", 8)).pack(side="left", padx=(16, 0))
        tk.Label(self.list_heading, text="자동 감지", fg=self.MUTED, bg=self.BG, font=("Malgun Gothic", 8)).pack(side="right", padx=(0, 42))

        footer = tk.Frame(outer, bg=self.BG)
        footer.pack(fill="x", side="bottom", pady=(14, 0))
        self.status_frame = tk.Frame(footer, bg="#1D2C29", padx=12, pady=9)
        self.status_dot = tk.Canvas(self.status_frame, width=10, height=10, bg="#1D2C29", highlightthickness=0)
        self.status_dot_item = self.status_dot.create_oval(3, 3, 7, 7, fill=self.ACCENT, outline="")
        self.status_dot.pack(side="left", padx=(0, 7))
        tk.Label(self.status_frame, textvariable=self.status_value, fg=self.TEXT, bg="#1D2C29", font=("Malgun Gothic", 8), wraplength=460, justify="left", anchor="w").pack(side="left", fill="x", expand=True)
        self.version_row = tk.Frame(footer, bg=self.BG)
        self.version_row.pack(fill="x", pady=(8, 0))
        self.extension_status_dot = tk.Label(self.version_row, text="●", fg=self.MUTED, bg=self.BG, font=("Segoe UI", 7))
        self.extension_status_dot.pack(side="left", padx=(0, 6))
        self.current_profile_label = tk.Label(self.version_row, text=self.profile_value.get(), fg=self.MUTED, bg=self.BG, font=("Malgun Gothic", 8), width=13, anchor="w", cursor="hand2")
        self.current_profile_label.pack(side="left")
        self.current_profile_label.bind("<Button-1>", lambda _event: self.show_settings())
        tk.Button(self.version_row, textvariable=self.connection_state_value, command=self.show_settings, bg=self.BG, fg=self.MUTED, activebackground=self.BG, activeforeground=self.TEXT, relief="flat", bd=0, font=("Malgun Gothic", 8), cursor="hand2").pack(side="left")
        self.quit_button = ttk.Button(self.version_row, text="앱 종료", command=self.on_close, style="Exit.TButton", cursor="hand2")
        self.quit_button.pack(side="right", padx=(12, 0))
        tk.Button(self.version_row, textvariable=self.version_summary_value, command=self.show_changelog, bg=self.BG, fg=self.MUTED, activebackground=self.BG, activeforeground=self.TEXT, relief="flat", bd=0, font=("Segoe UI", 8), cursor="hand2").pack(side="right")

        list_box = tk.Frame(outer, bg=self.SURFACE)
        list_box.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(list_box, bg=self.SURFACE, highlightthickness=0, height=350)
        scrollbar = ttk.Scrollbar(list_box, orient="vertical", command=self.canvas.yview, style="Dark.Vertical.TScrollbar")
        self.list_frame = tk.Frame(self.canvas, bg=self.SURFACE)
        self.list_window = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.list_frame.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.list_window, width=event.width))
        self.root.bind_all("<MouseWheel>", self._on_list_mousewheel, add="+")
        self._build_settings_dialog()
        self._update_pause_controls()
        self._update_refresh_controls()

    def _center_dialog(self, dialog: tk.Toplevel) -> None:
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - dialog.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")

    def _build_settings_dialog(self) -> None:
        dialog = self.settings_dialog = tk.Frame(self.root, bg="#101116")
        self.settings_previous_focus = None
        panel = tk.Frame(dialog, bg=self.SURFACE, padx=22, pady=20, highlightthickness=1, highlightbackground="#3A3D47")
        tk.Label(panel, text="설정", fg=self.TEXT, bg=self.SURFACE, font=("Malgun Gothic", 14, "bold")).pack(anchor="w", pady=(0, 16))
        ttk.Button(panel, text="닫기", style="DialogAccent.TButton", command=self._close_settings_dialog, cursor="hand2").pack(side="bottom", anchor="e", pady=(16, 0))
        body = tk.Frame(panel, bg=self.SURFACE)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, bg=self.SURFACE, highlightthickness=0, height=1, yscrollincrement=20)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview, style="Dark.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        card = tk.Frame(canvas, bg=self.SURFACE)
        content_window = canvas.create_window((0, 0), window=card, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        profile_section = tk.Frame(card, bg=self.SURFACE)
        profile_section.pack(fill="x")
        tk.Label(profile_section, text="사용할 Chrome 프로필", fg=self.TEXT, bg=self.SURFACE, font=("Malgun Gothic", 9, "bold")).pack(anchor="w", pady=(0, 9))
        self.profile_single_label = tk.Label(profile_section, textvariable=self.profile_value, fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 9))
        self.profile_editor = tk.Frame(profile_section, bg=self.SURFACE)
        self.profile_selector = ttk.Combobox(self.profile_editor, textvariable=self.profile_value, values=list(self.profile_labels), state="readonly", width=26, font=("Malgun Gothic", 9), style="Dark.TCombobox")
        self.profile_selector.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.profile_selector.bind("<<ComboboxSelected>>", lambda _event: self.root.after_idle(self._clear_profile_selector_highlight))
        ttk.Button(self.profile_editor, text="적용", style="DialogDark.TButton", command=self.select_chrome_profile, cursor="hand2").pack(side="right")
        self._refresh_profile_controls()

        tk.Frame(card, bg="#3A3D47", height=1).pack(fill="x", pady=20)
        tk.Label(card, text="확장 프로그램", fg=self.TEXT, bg=self.SURFACE, font=("Malgun Gothic", 9, "bold")).pack(anchor="w")
        extension_label = tk.Label(card, textvariable=self.extension_status_value, fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 8), wraplength=390, justify="left")
        extension_label.pack(anchor="w", pady=(5, 12))
        extension_actions = tk.Frame(card, bg=self.SURFACE)
        extension_actions.pack(fill="x")
        for label, command in (("연결 재확인", self.recheck_extension_status), ("설치 안내", self.show_extension_install_guide), ("연결 코드", self.show_extension_pairing)):
            ttk.Button(extension_actions, text=label, style="DialogDark.TButton", command=command, cursor="hand2").pack(side="left", padx=(0, 7))
        tk.Label(card, text="방송 종료 시 앱이 자동으로 연 탭만 닫습니다.", fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 8)).pack(anchor="w", pady=(10, 0))
        tk.Frame(card, bg="#3A3D47", height=1).pack(fill="x", pady=20)
        tk.Label(card, text=f"{APP_NAME} {APP_VERSION}", fg=self.TEXT, bg=self.SURFACE, font=("Malgun Gothic", 9, "bold")).pack(anchor="w")
        self.version_label = tk.Label(card, textvariable=self.version_value, fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 8), wraplength=390, justify="left")
        self.version_label.pack(anchor="w", pady=(5, 10))
        ttk.Button(card, text="업데이트 내역", style="DialogDark.TButton", command=self.show_changelog, cursor="hand2").pack(anchor="w")
        tk.Frame(card, bg="#3A3D47", height=1).pack(fill="x", pady=20)
        window_actions = tk.Frame(card, bg=self.SURFACE)
        window_actions.pack(fill="x")
        ttk.Button(window_actions, text="트레이로 숨기기", style="DialogDark.TButton", command=self._hide_from_settings, cursor="hand2").pack(side="left")

        def resize_panel(event) -> None:
            panel.place(relx=0.5, rely=0.5, anchor="center", width=min(484, max(1, event.width - 48)), height=min(600, max(1, event.height - 48)))

        def resize_content(event) -> None:
            canvas.itemconfigure(content_window, width=event.width)
            extension_label.configure(wraplength=max(1, event.width - 4))
            self.version_label.configure(wraplength=max(1, event.width - 4))
            update_scrollregion(event)

        def update_scrollregion(_event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            if card.winfo_reqheight() > canvas.winfo_height():
                scrollbar.pack(side="right", fill="y")
            else:
                scrollbar.pack_forget()
                canvas.yview_moveto(0)

        def settings_active(event) -> bool:
            return dialog.winfo_ismapped() and event.widget.winfo_toplevel() is self.root and self.root.grab_current() is dialog

        def close_on_escape(event):
            if settings_active(event):
                self._close_settings_dialog()
                return "break"
            return None

        def cycle_focus(event):
            if not settings_active(event):
                return None
            direction = "tk_focusPrev" if event.state & 1 else "tk_focusNext"
            current = str(self.root.focus_get() or dialog)
            first = current
            while True:
                current = str(self.root.tk.call(direction, current))
                if current.startswith(str(dialog) + "."):
                    focused = self.root.nametowidget(current)
                    focused.focus_set()
                    if current.startswith(str(card) + "."):
                        top = focused.winfo_rooty() - card.winfo_rooty()
                        bottom = top + focused.winfo_height()
                        visible_top = canvas.canvasy(0)
                        if top < visible_top:
                            canvas.yview_moveto(max(0, top - 20) / max(1, card.winfo_height()))
                        elif bottom > visible_top + canvas.winfo_height():
                            canvas.yview_moveto((bottom - canvas.winfo_height() + 20) / max(1, card.winfo_height()))
                    break
                if current == first:
                    dialog.focus_set()
                    break
            return "break"

        def scroll_settings(event):
            if not settings_active(event):
                return None
            delta = int(getattr(event, "delta", 0))
            if delta:
                units = max(1, abs(delta) // 120)
                canvas.yview_scroll(-units if delta > 0 else units, "units")
            return "break"

        dialog.bind("<Configure>", resize_panel)
        canvas.bind("<Configure>", resize_content)
        card.bind("<Configure>", update_scrollregion)
        self.root.bind("<Escape>", close_on_escape, add="+")
        self.root.bind("<Tab>", cycle_focus, add="+")
        self.root.bind("<Shift-Tab>", cycle_focus, add="+")
        self.root.bind("<MouseWheel>", scroll_settings, add="+")

    def _refresh_profile_controls(self) -> None:
        self.profile_selector.configure(values=list(self.profile_labels))
        if len(self.chrome_profiles) > 1:
            self.profile_single_label.pack_forget()
            self.profile_editor.pack(fill="x")
        else:
            self.profile_editor.pack_forget()
            self.profile_single_label.pack(anchor="w")

    def show_settings(self) -> None:
        if self.active_dialog is not None and self.active_dialog.winfo_exists():
            self.active_dialog.lift()
            return
        if self.changelog_dialog is not None and self.changelog_dialog.winfo_exists():
            self.changelog_dialog.lift()
            return
        self.profile_value.set(self.selected_chrome_profile["name"])
        if not self.settings_dialog.winfo_ismapped():
            self.settings_previous_focus = self.root.focus_get()
        self.settings_dialog.place(x=0, y=0, relwidth=1, relheight=1)
        self.settings_dialog.lift()
        self.root.update_idletasks()
        self.settings_dialog.grab_set()
        self.settings_dialog.focus_set()

    def _close_settings_dialog(self) -> None:
        if self.root.grab_current() is self.settings_dialog:
            self.settings_dialog.grab_release()
        self.settings_dialog.place_forget()
        previous_focus = self.settings_previous_focus
        self.settings_previous_focus = None
        if previous_focus is not None and previous_focus.winfo_exists() and previous_focus.winfo_viewable():
            previous_focus.focus_set()
        else:
            self.root.focus_set()

    def _hide_from_settings(self) -> None:
        self._close_settings_dialog()
        self.hide_to_tray()

    def show_add_channel_dialog(self) -> None:
        if self.active_dialog is not None and self.active_dialog.winfo_exists():
            self.active_dialog.lift()
            return
        if self.add_dialog is not None and self.add_dialog.winfo_exists():
            self.add_dialog.lift()
            return
        dialog = self.add_dialog = tk.Toplevel(self.root, bg=self.SURFACE)
        dialog.title(f"{APP_NAME} · 채널 추가")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        card = tk.Frame(dialog, bg=self.SURFACE, padx=24, pady=22)
        card.pack(fill="both", expand=True)
        tk.Label(card, text="채널 추가", fg=self.TEXT, bg=self.SURFACE, font=("Malgun Gothic", 13, "bold")).pack(anchor="w")
        tk.Label(card, text="치지직 채널 URL 또는 32자리 채널 ID", fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 9)).pack(anchor="w", pady=(10, 12))
        entry = tk.Entry(card, textvariable=self.input_value, width=40, bg=self.INPUT, fg=self.TEXT, insertbackground=self.TEXT, relief="flat", font=("Consolas", 10), highlightthickness=1, highlightbackground="#40444F", highlightcolor=self.ACCENT)
        entry.pack(fill="x", ipady=9)
        entry.bind("<Return>", lambda _event: self.add_channel())
        self.add_status_label = tk.Label(card, textvariable=self.status_value, fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 8), wraplength=360, justify="left")
        self.add_status_label.pack(anchor="w", pady=(9, 0))
        buttons = tk.Frame(card, bg=self.SURFACE)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text="취소", style="DialogDark.TButton", command=self._close_add_channel_dialog, cursor="hand2").pack(side="right")
        ttk.Button(buttons, text="등록", style="DialogAccent.TButton", command=self.add_channel, cursor="hand2").pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", self._close_add_channel_dialog)
        dialog.bind("<Escape>", lambda _event: self._close_add_channel_dialog())
        self._center_dialog(dialog)
        dialog.grab_set()
        entry.focus_set()

    def _close_add_channel_dialog(self) -> None:
        dialog = getattr(self, "add_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.grab_release()
            dialog.destroy()
        self.add_dialog = None
        self.add_status_label = None


    def _on_list_mousewheel(self, event) -> str | None:
        """Scroll the channel list when the pointer is over its visible area."""
        settings_dialog = getattr(self, "settings_dialog", None)
        if settings_dialog is not None and settings_dialog.winfo_ismapped():
            return None
        if not self.canvas.winfo_ismapped():
            return None
        pointer_x, pointer_y = self.root.winfo_pointerxy()
        canvas_x, canvas_y = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
        if not (
            canvas_x <= pointer_x < canvas_x + self.canvas.winfo_width()
            and canvas_y <= pointer_y < canvas_y + self.canvas.winfo_height()
        ):
            return None
        delta = int(getattr(event, "delta", 0))
        if delta == 0:
            return None
        units = max(1, abs(delta) // 120)
        self.canvas.yview_scroll(-units if delta > 0 else units, "units")
        return "break"

    def _load_channels(self) -> list[dict]:
        try:
            return load_channels()
        except StorageError:
            self.channels_read_only = True
            self.storage_errors.append("channels.json을 읽지 못해 원본을 보존했습니다. 파일을 복구한 뒤 다시 실행해 주세요. 해당 데이터의 변경은 저장되지 않습니다.")
            return []

    def _load_settings(self) -> dict:
        try:
            return load_settings()
        except StorageError:
            self.settings_read_only = True
            self.storage_errors.append("settings.json을 읽지 못해 원본을 보존했습니다. 파일을 복구한 뒤 다시 실행해 주세요. 해당 데이터의 변경은 저장되지 않습니다.")
            return {}

    def _scan_chrome_profiles(self) -> None:
        """Read Chrome's profile list on every app launch."""
        try:
            self.chrome_profiles = get_chrome_profiles()
        except ProfileReadError:
            self.chrome_profiles = [{"directory": self.settings.get("chrome_profile_directory", "Default"), "name": "프로필 확인 대기", "gaia_id": "", "email": ""}]
            self.storage_errors.append("Chrome 프로필 정보를 읽지 못했습니다. 기존 선택을 보존하고 다시 확인합니다.")
        self.profile_labels = {profile["name"]: profile for profile in self.chrome_profiles}
        saved_profile_directory = self.settings.get("chrome_profile_directory")
        self.selected_chrome_profile = next((profile for profile in self.chrome_profiles if profile["directory"] == saved_profile_directory), self.chrome_profiles[0])
        if saved_profile_directory and self.selected_chrome_profile["directory"] != saved_profile_directory:
            previous_settings = dict(self.settings)
            self.settings["chrome_profile_directory"] = self.selected_chrome_profile["directory"]
            if not self._save_settings():
                self.settings = previous_settings

    def _check_selected_profile_exists(self) -> None:
        if self.stop_event.is_set():
            return
        current_directory = self.selected_chrome_profile["directory"]
        try:
            available_profiles = get_chrome_profiles()
        except ProfileReadError:
            self.root.after(5_000, self._check_selected_profile_exists)
            return
        known_profiles = self.chrome_profiles
        refreshed_profiles = available_profiles
        current_profile_exists = any(profile["directory"] == current_directory for profile in available_profiles)
        if known_profiles != refreshed_profiles:
            previous_profile = self.selected_chrome_profile
            previous_name = self.selected_chrome_profile["name"]
            self.chrome_profiles = available_profiles
            self.profile_labels = {profile["name"]: profile for profile in self.chrome_profiles}
            if current_profile_exists:
                self.selected_chrome_profile = next(profile for profile in self.chrome_profiles if profile["directory"] == current_directory)
            else:
                self.selected_chrome_profile = self.chrome_profiles[0]
                previous_settings = dict(self.settings)
                self.settings["chrome_profile_directory"] = self.selected_chrome_profile["directory"]
                if not self._save_settings():
                    self.settings = previous_settings
            self._apply_selected_profile()
            self.profile_value.set(self.selected_chrome_profile["name"])
            self.current_profile_label.configure(text=self.selected_chrome_profile["name"])
            if hasattr(self, "profile_single_label"):
                self._refresh_profile_controls()
            else:
                self.profile_selector.configure(values=list(self.profile_labels))
            identity_changed = any(previous_profile.get(field) != self.selected_chrome_profile.get(field)
                                   for field in ("directory", "gaia_id", "email"))
            if identity_changed:
                self._reset_extension_connection_check()
                self._set_extension_status("Chrome 확장 프로그램 연결 확인 중…")
                if not current_profile_exists:
                    self._set_status(f"사용 중이던 Chrome 프로필({previous_name})을 사용할 수 없어 {self.selected_chrome_profile['name']} 프로필로 변경했습니다.", True)
                self.root.after(500, self._check_extension_connection)
        self.root.after(5_000, self._check_selected_profile_exists)

    def _save_settings(self) -> bool:
        try:
            if self.settings_read_only:
                raise StorageError("설정 파일 복구가 필요합니다.")
            save_settings(self.settings)
            return True
        except StorageError:
            self._ui(self._set_status, "설정을 저장하지 못했습니다. 파일 상태와 쓰기 권한을 확인해 주세요.", True)
            return False

    def _apply_selected_profile(self) -> None:
        profile_keys = set()
        if self.selected_chrome_profile.get("gaia_id"):
            profile_keys.add(f"gaia:{self.selected_chrome_profile['gaia_id']}")
        if self.selected_chrome_profile.get("email"):
            profile_keys.add(f"email:{self.selected_chrome_profile['email'].lower()}")
        CHROME_TABS.set_selected_profile(profile_keys)

    def _reset_extension_connection_check(self, grace_seconds: int = EXTENSION_CONNECTION_GRACE_SECONDS) -> None:
        self.extension_setup_prompted = False
        self.extension_update_prompted = False
        self.extension_reload_deadline = None
        self.extension_connected = False
        self.chrome_launch_requested = False
        self.extension_connection_deadline = time.monotonic() + grace_seconds

    def _set_extension_status(self, message: str, connected: bool | None = None) -> None:
        self.extension_status_value.set(message)
        if hasattr(self, "extension_status_dot"):
            self.extension_status_dot.configure(fg=self.ACCENT if connected else self.DANGER if connected is False else self.MUTED)
        if hasattr(self, "connection_state_value"):
            state = "연결됨" if connected else "업데이트 필요" if "업데이트 필요" in message else "연결 안 됨" if connected is False else "확인 중"
            self.connection_state_value.set(state)
        if hasattr(self, "extension_notice"):
            if connected is False:
                if not self.extension_notice.winfo_manager():
                    self.extension_notice.pack(fill="x", pady=(0, 14), before=self.list_heading)
            else:
                self.extension_notice.pack_forget()

    def _schedule_update_check(self) -> None:
        if self.stop_event.is_set():
            return
        threading.Thread(target=self._check_for_update, daemon=True).start()
        self.root.after(UPDATE_CHECK_INTERVAL_SECONDS * 1_000, self._schedule_update_check)

    def _check_for_update(self) -> None:
        """Check published GitHub Releases without delaying app monitoring."""
        try:
            release = get_latest_release()
            latest_version = get_release_version(release)
            self._ui(self._set_latest_version, latest_version)
            update_info = find_available_update(release)
            if update_info is None or self.update_prompted_version == update_info.version:
                return
            self._ui(self._start_update_download, update_info)
        except (UpdateError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError, OSError):
            # An update check must never interrupt normal channel monitoring.
            self._ui(self._set_latest_version, None)
            return

    def _set_latest_version(self, version: str | None) -> None:
        latest_text = version if version is not None else "확인 실패"
        self.version_value.set(f"현재 버전 {APP_VERSION} · 최신 버전 {latest_text}")
        if hasattr(self, "version_summary_value"):
            summary = "확인 실패" if version is None else "최신" if version == APP_VERSION else f"최신 {version}"
            self.version_summary_value.set(f"v{APP_VERSION} · {summary}")

    def _start_update_download(self, update_info: UpdateInfo) -> None:
        if self.stop_event.is_set() or self.update_download_in_progress:
            return
        if self.update_prompted_version == update_info.version:
            return
        self.update_download_in_progress = True
        self._set_status(f"AutoChzzk {update_info.version} 업데이트를 다운로드하는 중입니다…")
        threading.Thread(target=self._download_update, args=(update_info,), daemon=True).start()

    def _download_update(self, update_info: UpdateInfo) -> None:
        last_percent = -1

        def report_progress(downloaded: int, total: int) -> None:
            nonlocal last_percent
            percent = min(100, int(downloaded * 100 / total)) if total else 0
            if percent == last_percent:
                return
            last_percent = percent
            self._ui(self._set_status, f"AutoChzzk {update_info.version} 업데이트 다운로드 중 · {percent}%")

        try:
            installer_path = download_update(
                update_info,
                progress=report_progress,
                cancel_event=self.stop_event,
            )
        except UpdateCancelled:
            return
        except Exception:
            self._ui(self._update_download_failed, update_info)
            return
        self._ui(self._update_download_complete, update_info, installer_path)

    def _update_download_failed(self, update_info: UpdateInfo) -> None:
        self.update_download_in_progress = False
        self._set_status(f"AutoChzzk {update_info.version} 업데이트를 다운로드하지 못했습니다.", True)
        if self.stop_event.is_set():
            return
        if self.active_dialog is not None and self.active_dialog.winfo_exists():
            self.root.after(1_000, lambda: self._update_download_failed(update_info))
            return
        self._show_app_dialog(
            "업데이트 다운로드 실패",
            "업데이트 파일을 다운로드하거나 검증하지 못했습니다.\n인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
            "다시 시도",
            lambda: self._start_update_download(update_info),
            "나중에",
        )

    def _update_download_complete(self, update_info: UpdateInfo, installer_path: Path) -> None:
        self.update_download_in_progress = False
        self._set_status(f"AutoChzzk {update_info.version} 업데이트를 설치할 준비가 됐습니다.")
        self._offer_update(update_info, installer_path)

    def _offer_update(self, update_info: UpdateInfo, installer_path: Path) -> None:
        if self.stop_event.is_set():
            return
        if self.active_dialog is not None and self.active_dialog.winfo_exists():
            self.root.after(1_000, lambda: self._offer_update(update_info, installer_path))
            return
        self.update_prompted_version = update_info.version
        self._restore_window()
        self._show_app_dialog(
            "업데이트 준비 완료",
            f"AutoChzzk {update_info.version} 다운로드와 검증이 완료됐습니다.\n현재 버전: {APP_VERSION}\n\n업데이트를 누르면 관리자 권한 확인 후 설치하고 앱을 다시 시작합니다.",
            "업데이트",
            lambda: self._install_update(update_info, installer_path),
            "나중에",
        )

    def _install_update(self, update_info: UpdateInfo, installer_path: Path) -> None:
        if not verify_installer(installer_path, update_info.sha256):
            installer_path.unlink(missing_ok=True)
            self.update_prompted_version = None
            self._show_app_dialog(
                "업데이트 검증 실패",
                "설치 파일이 변경되었거나 손상되어 실행하지 않았습니다.",
                "다시 다운로드",
                lambda: self._start_update_download(update_info),
                "나중에",
            )
            return
        try:
            launch_installer(installer_path)
        except OSError as exc:
            cancelled = getattr(exc, "winerror", None) == 1223
            self.update_prompted_version = None
            self._show_app_dialog(
                "업데이트 취소" if cancelled else "업데이트 실행 실패",
                "관리자 권한 요청이 취소되었습니다." if cancelled else "업데이트 설치 프로그램을 실행하지 못했습니다.",
                "다시 시도",
                lambda: self._install_update(update_info, installer_path),
                "나중에",
            )
            return
        self.on_close()

    def _update_extension_status(self) -> None:
        if CHROME_TABS.is_connected():
            self._set_connected_extension_status()
            if not self.extension_connected:
                self.extension_connected = True
                if self.extension_setup_prompted:
                    self._open_current_lives_after_extension_connect()
        else:
            self.extension_connected = False
            self._set_extension_status("Chrome 확장 프로그램 연결 안 됨", False)
        self._update_monitor_status()

    def _set_connected_extension_status(self) -> None:
        self.chrome_launch_requested = False
        if not CHROME_TABS.selected_extension_needs_update(REQUIRED_EXTENSION_VERSION):
            self.extension_reload_deadline = None
            self.extension_update_prompted = False
            self._set_extension_status("Chrome 확장 프로그램 연결됨", True)
            return
        now = time.monotonic()
        if self.extension_reload_deadline is None:
            if CHROME_TABS.queue_extension_reload(REQUIRED_EXTENSION_VERSION):
                self.extension_reload_deadline = now + EXTENSION_RELOAD_GRACE_SECONDS
                self._set_extension_status("Chrome 확장 프로그램 자동 업데이트 적용 중…")
                return
            self.extension_reload_deadline = now
        if now < self.extension_reload_deadline:
            self._set_extension_status("Chrome 확장 프로그램 자동 업데이트 적용 중…")
            return
        self._set_extension_status("Chrome 확장 프로그램 업데이트 필요", False)
        self._prompt_extension_reinstall()

    def _refresh_extension_status(self) -> None:
        if self.stop_event.is_set():
            return
        self._update_extension_status()
        self.root.after(2_000, self._refresh_extension_status)

    def recheck_extension_status(self) -> None:
        """Recheck the selected profile after its next extension heartbeat."""
        self.extension_update_prompted = False
        self._set_extension_status("Chrome 확장 프로그램 연결 및 버전 확인 중…")
        self.root.after(2_500, self._update_extension_status)

    def show_profile_editor(self) -> None:
        if len(self.chrome_profiles) < 2:
            return
        self.show_settings()
        self._clear_profile_selector_highlight()
        self.profile_selector.focus_set()

    def _clear_profile_selector_highlight(self) -> None:
        self.profile_selector.selection_clear()

    def select_chrome_profile(self) -> None:
        profile = self.profile_labels.get(self.profile_value.get())
        if profile is None:
            return
        if profile == self.selected_chrome_profile:
            return
        previous_settings = dict(self.settings)
        self.settings["chrome_profile_directory"] = profile["directory"]
        if not self._save_settings():
            self.settings = previous_settings
            self.profile_value.set(self.selected_chrome_profile["name"])
            return
        self.selected_chrome_profile = profile
        self._apply_selected_profile()
        self._reset_extension_connection_check()
        self.current_profile_label.configure(text=profile["name"])
        self._set_extension_status("Chrome 확장 프로그램 연결 확인 중…")
        self._set_status(f"{profile['name']} Chrome 프로필에서만 방송 감지와 자동 접속을 사용합니다.")
        self.root.after(1_000, self._check_extension_connection)

    def _save_channels(self, channels=None) -> bool:
        try:
            if self.channels_read_only:
                raise StorageError("채널 파일 복구가 필요합니다.")
            save_channels(self.channels if channels is None else channels)
            return True
        except StorageError:
            self._set_status("채널 변경을 저장하지 못했습니다. 원본 파일과 쓰기 권한을 확인해 주세요.", True)
            return False

    def _show_app_dialog(self, title: str, message: str, confirm_text: str = "확인", confirm_command=None, cancel_text: str | None = None, cancel_command=None) -> None:
        """Show an app-styled modal instead of a Windows system dialog."""
        if self.active_dialog is not None and self.active_dialog.winfo_exists():
            self.active_dialog.lift()
            return

        previous_grab = self.root.grab_current()
        dialog = tk.Toplevel(self.root, bg=self.SURFACE)
        self.active_dialog = dialog
        dialog.title(APP_NAME)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(bg=self.SURFACE)

        card = tk.Frame(dialog, bg=self.SURFACE, padx=24, pady=21)
        card.pack(fill="both", expand=True)
        tk.Label(card, text=title, fg=self.TEXT, bg=self.SURFACE, font=("Malgun Gothic", 12, "bold")).pack(anchor="w")
        tk.Label(card, text=message, fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 9), justify="left", wraplength=340).pack(anchor="w", pady=(9, 20))
        buttons = tk.Frame(card, bg=self.SURFACE)
        buttons.pack(fill="x")

        def close(callback=None) -> None:
            if not dialog.winfo_exists():
                return
            dialog.grab_release()
            dialog.destroy()
            self.active_dialog = None
            if previous_grab is not None and previous_grab.winfo_exists() and previous_grab.winfo_viewable():
                previous_grab.grab_set()
                previous_grab.focus_set()
            if callback is not None:
                callback()

        if cancel_text:
            ttk.Button(buttons, text=cancel_text, style="DialogDark.TButton", command=lambda: close(cancel_command), cursor="hand2", width=12).pack(side="right")
        ttk.Button(buttons, text=confirm_text, style="DialogAccent.TButton", command=lambda: close(confirm_command), cursor="hand2", width=12).pack(side="right", padx=(0, 8) if cancel_text else 0)
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _event: close(cancel_command))
        dialog.update_idletasks()
        root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        x = root_x + max(0, (self.root.winfo_width() - dialog.winfo_width()) // 2)
        y = root_y + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.grab_set()
        dialog.focus_set()

    def _find_chrome_path(self) -> Path | None:
        chrome_paths = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
        return next((path for path in chrome_paths if path.is_file()), None)

    def _open_chrome_extensions(self) -> None:
        chrome_path = self._find_chrome_path()
        if chrome_path is not None:
            subprocess.Popen(
                [str(chrome_path), f"--profile-directory={self.selected_chrome_profile['directory']}", "chrome://extensions/"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            webbrowser.open("chrome://extensions", new=1)

    def _launch_selected_chrome(self) -> bool:
        """Start the selected profile so its extension can receive open commands."""
        chrome_path = self._find_chrome_path()
        if chrome_path is None:
            return False
        try:
            subprocess.Popen(
                [str(chrome_path), f"--profile-directory={self.selected_chrome_profile['directory']}"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            return False
        return True

    def _is_chrome_running(self) -> bool:
        """Return whether any Chrome process is running without showing a console window."""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return "chrome.exe" in result.stdout.lower()

    def show_extension_pairing(self) -> None:
        self._show_app_dialog(
            "확장 연결 코드",
            "1. 아래 버튼으로 연결 코드를 복사합니다.\n"
            "2. 선택한 Chrome 프로필에서 AutoChzzk 확장 아이콘을 클릭합니다.\n"
            "3. 설정 화면에 코드를 붙여넣고 저장합니다.\n\n"
            "앱과 확장 프로그램은 모두 2.0.0 이상이어야 합니다.\n"
            "연결 코드는 다른 사람에게 공유하지 마세요. 복사한 코드는 60초 후 클립보드에서 지웁니다.",
            "연결 코드 복사",
            self._copy_extension_pairing_code,
            "닫기",
        )

    def _copy_extension_pairing_code(self) -> None:
        try:
            secret = get_pairing_secret()
            self.root.clipboard_clear()
            self.root.clipboard_append(secret)
        except (OSError, ValueError, tk.TclError):
            self._show_app_dialog("연결 코드 오류", "연결 코드를 읽거나 복사하지 못했습니다. 앱의 로컬 데이터 폴더 접근 권한을 확인해 주세요.")
            return
        self._copied_pairing_secret = secret

        def clear_copied_code() -> None:
            try:
                # Do not remove anything the user copied after the pairing code.
                if self.root.clipboard_get() == secret:
                    self.root.clipboard_clear()
            except tk.TclError:
                pass
            if getattr(self, "_copied_pairing_secret", None) == secret:
                self._copied_pairing_secret = None

        self.root.after(60_000, clear_copied_code)
        self._set_status("연결 코드를 복사했습니다. Chrome의 AutoChzzk 확장 설정에 붙여넣으세요.")

    def show_extension_install_guide(self) -> None:
        self._show_app_dialog(
            "Chrome 확장 프로그램 설치 안내",
            f"선택한 Chrome 프로필({self.selected_chrome_profile['name']})에만 설치하면 됩니다.\n\n1. Chrome 열기를 누릅니다.\n2. chrome://extensions 에 접속합니다.\n3. 화면 우측 상단의 ‘개발자 모드’를 켭니다.\n4. ‘압축해제된 확장 프로그램 로드’를 눌러 AutoChzzk 설치 폴더의 chrome_extension 폴더를 선택합니다.\n5. 앱의 설정 → 연결 코드에서 코드를 복사하고, 확장 아이콘을 클릭해 설정에 등록합니다.\n\n이전 버전은 확장 새로고침 후 연결 코드를 등록해야 합니다. 이미 다른 프로필에 설치했다면 앱의 설정에서 해당 Chrome 프로필을 선택하고 ‘적용’을 눌러 주세요.",
            "Chrome 열기",
            self._open_chrome_extensions,
            "확인했습니다",
        )

    def _prompt_extension_reinstall(self) -> None:
        if (not CHROME_TABS.is_connected()
                or not CHROME_TABS.selected_extension_needs_update(REQUIRED_EXTENSION_VERSION)
                or self.extension_reload_deadline is None
                or time.monotonic() < self.extension_reload_deadline):
            return
        if self.extension_update_prompted:
            return
        if self.active_dialog is not None and self.active_dialog.winfo_exists():
            self.root.after(1_000, self._prompt_extension_reinstall)
            return
        self.extension_update_prompted = True
        self.show_extension_reinstall_guide()

    def show_extension_reinstall_guide(self) -> None:
        versions = sorted(version for version in CHROME_TABS.selected_extension_versions() if version)
        current_version = ", ".join(versions) if versions else "확인할 수 없음"
        self._show_app_dialog(
            "Chrome 확장 프로그램 수동 업데이트 필요",
            "자동 업데이트를 적용하지 못했거나 연결된 확장 프로그램이 이 기능을 지원하지 않습니다.\n\n"
            "chrome://extensions에서 AutoChzzk Chrome Companion의 새로고침 버튼을 눌러 주세요. "
            "그래도 버전이 바뀌지 않으면 기존 확장 프로그램을 삭제한 뒤 AutoChzzk 설치 폴더의 "
            "chrome_extension 폴더를 다시 로드해 주세요.\n\n"
            f"현재 버전: {current_version}\n필요 버전: {REQUIRED_EXTENSION_VERSION}",
            "확장 프로그램 열기",
            self._open_chrome_extensions,
            "나중에",
        )

    def show_changelog(self) -> None:
        if self.changelog_dialog is not None and self.changelog_dialog.winfo_exists():
            self.changelog_dialog.lift()
            self.changelog_dialog.focus_set()
            return

        previous_grab = self.root.grab_current()
        dialog = tk.Toplevel(self.root, bg=self.SURFACE)
        self.changelog_dialog = dialog
        dialog.title("업데이트 내역")
        dialog.transient(self.root)
        dialog.resizable(False, False)

        card = tk.Frame(dialog, bg=self.SURFACE, padx=22, pady=20)
        card.pack(fill="both", expand=True)
        tk.Label(card, text="업데이트 내역", fg=self.TEXT, bg=self.SURFACE, font=("Malgun Gothic", 13, "bold")).pack(anchor="w")
        tk.Label(card, text="배포된 버전의 주요 변경 사항입니다.", fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 8)).pack(anchor="w", pady=(3, 12))

        content_frame = tk.Frame(card, bg=self.INPUT)
        content_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(content_frame, orient="vertical", style="Dark.Vertical.TScrollbar")
        notes = tk.Text(
            content_frame,
            bg=self.INPUT,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            bd=0,
            wrap="word",
            font=("Malgun Gothic", 9),
            padx=14,
            pady=12,
            height=20,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=notes.yview)
        scrollbar.pack(side="right", fill="y")
        notes.pack(side="left", fill="both", expand=True)
        notes.tag_configure("version", foreground=self.ACCENT, font=("Malgun Gothic", 10, "bold"), spacing1=4)
        notes.tag_configure("item", foreground=self.TEXT, spacing1=3)
        for version, entries in RELEASE_NOTES:
            notes.insert("end", f"{version}\n", "version")
            for entry in entries:
                notes.insert("end", f"• {entry}\n", "item")
            notes.insert("end", "\n")
        notes.configure(state="disabled")

        def close() -> None:
            if dialog.winfo_exists():
                dialog.destroy()
            self.changelog_dialog = None
            if previous_grab is not None and previous_grab.winfo_exists() and previous_grab.winfo_viewable():
                previous_grab.grab_set()
                previous_grab.focus_set()

        ttk.Button(card, text="닫기", style="DialogAccent.TButton", command=close, cursor="hand2", width=10).pack(anchor="e", pady=(14, 0))
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _event: close())
        dialog.geometry("570x540")
        dialog.update_idletasks()
        root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        x = root_x + max(0, (self.root.winfo_width() - dialog.winfo_width()) // 2)
        y = root_y + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.grab_set()
        dialog.focus_set()

    def _check_extension_connection(self) -> None:
        if self.stop_event.is_set():
            return
        if CHROME_TABS.is_connected():
            self._set_connected_extension_status()
            self.extension_connected = True
            self._hide_status()
            if not self.extension_setup_prompted:
                self.extension_setup_prompted = True
                self._open_current_lives_after_extension_connect()
            return
        if self.extension_setup_prompted:
            return
        if time.monotonic() < self.extension_connection_deadline:
            self._set_extension_status("Chrome 확장 프로그램 연결 확인 중…")
            self._set_status("Chrome 확장 프로그램 연결을 확인하는 중입니다…")
            self.root.after(500, self._check_extension_connection)
            return
        self._set_extension_status("Chrome 확장 프로그램 연결 안 됨", False)
        self.extension_setup_prompted = True
        if self._is_chrome_running():
            self._set_status("Chrome 확장 프로그램 연결을 확인하지 못했습니다.", True)
            self.show_extension_install_guide()
        else:
            self._set_status("Chrome이 실행되지 않아 확장 프로그램 연결을 기다리지 않습니다.")

    def _open_current_lives_after_extension_connect(self) -> None:
        """Open broadcasts that were already live while the extension was disconnected."""
        self.force_open_checks.update(channel["id"] for channel in self.channels if channel.get("enabled"))

    def add_channel(self) -> None:
        channel_id = extract_channel_id(self.input_value.get())
        if not channel_id:
            message = "채널 URL 또는 32자리 채널 ID를 입력해 주세요." if not self.input_value.get().strip() else "치지직 채널 URL 또는 32자리 채널 ID를 정확히 입력해 주세요."
            self._show_app_dialog("입력 확인", message)
            return
        if any(channel["id"] == channel_id for channel in self.channels): messagebox.showinfo(APP_NAME, "이미 등록된 채널입니다."); return
        if channel_id in self.pending_additions:
            return
        if self.channels_read_only:
            self._set_status("채널 파일을 복구한 뒤 다시 실행해 주세요.", True)
            return
        def complete(name, error):
            self.pending_additions.discard(channel_id)
            if error:
                self._set_status("채널 정보를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.", True)
                return
            if any(channel["id"] == channel_id for channel in self.channels):
                return
            channels = self.channels + [{"id": channel_id, "name": name, "enabled": True, "interval": 60}]
            if not self._save_channels(channels):
                return
            self.channels = channels
            if extract_channel_id(self.input_value.get()) == channel_id:
                self.input_value.set("")
                self._close_add_channel_dialog()
            self.initial_checks.add(channel_id)
            self._refresh_list()
            self._set_status("채널 등록 완료 · 방송 상태 확인 중…")
        if self.lookup_pool.submit(("name", channel_id), lambda: get_channel_name(channel_id), complete):
            self.pending_additions.add(channel_id)
            self._set_status("채널 정보를 불러오는 중…")
        else:
            self._set_status("방송 상태를 확인 중입니다. 잠시 후 채널 추가를 다시 눌러 주세요.")

    def _refresh_list(self) -> None:
        menu = getattr(self, "channel_menu", None)
        if menu is not None and menu.winfo_exists():
            menu.close()
        for child in self.list_frame.winfo_children(): child.destroy()
        self.channel_rows = {}
        self.detection_buttons = {}
        self.interval_labels = {}
        self.interval_editors = {}
        self.live_status_widgets = {}
        self.watching_indicators: dict[str, tk.Label] = {}
        self.check_status_widgets = {}
        self._update_channel_count()
        if not self.channels: tk.Label(self.list_frame, text="아직 등록된 채널이 없습니다.", fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 10), pady=28).pack()
        for channel in self.channels:
            self._make_channel_row(channel)
            if self.editing_channel_id == channel["id"]: self._make_interval_editor(channel)
        self._update_monitor_status()
        self._update_refresh_controls()

    def _update_channel_count(self) -> None:
        enabled_count = sum(bool(channel.get("enabled")) for channel in self.channels)
        self.count_label.configure(text=f"등록 {len(self.channels)}개 · 감지 {enabled_count}개")

    def _update_monitor_status(self) -> None:
        """Refresh the watching indicators without showing an idle footer message."""
        self._refresh_watching_indicators()

    def _refresh_watching_indicators(self) -> None:
        for channel_id, indicator in self.watching_indicators.items():
            if indicator.winfo_exists():
                indicator.configure(
                    text="● 시청 중" if CHROME_TABS.is_watched(channel_id) else "",
                    fg=self.DANGER,
                )

    def _make_channel_row(self, channel: dict) -> None:
        row = tk.Frame(self.list_frame, bg=self.SURFACE, padx=16, pady=15)
        row.pack(fill="x")
        self.channel_rows[channel["id"]] = row
        actions = tk.Frame(row, bg=self.SURFACE)
        actions.pack(side="right", anchor="center")
        tk.Button(actions, text="⋯", command=lambda value=channel["id"]: self.show_channel_menu(value), bg=self.SURFACE, fg=self.MUTED, activebackground=self.INPUT, activeforeground=self.TEXT, relief="flat", bd=0, width=2, font=("Segoe UI", 15), cursor="hand2").pack(side="right", padx=(9, 0))
        detection_button = AnimatedToggle(actions, value=bool(channel.get("enabled")), command=lambda value=channel["id"]: self.toggle_channel(value), bg=self.SURFACE, accent=self.ACCENT, muted=self.MUTED, text_color=self.TEXT)
        detection_button.pack(side="right")
        self.detection_buttons[channel["id"]] = detection_button
        details = tk.Frame(row, bg=self.SURFACE)
        details.pack(side="left", fill="both", expand=True, padx=(0, 10))
        name_row = tk.Frame(details, bg=self.SURFACE)
        name_row.pack(fill="x")
        watching_indicator = tk.Label(name_row, bg=self.SURFACE, font=("Malgun Gothic", 8))
        watching_indicator.pack(side="right", padx=(7, 0))
        self.watching_indicators[channel["id"]] = watching_indicator
        MarqueeText(name_row, channel.get("name") or channel["id"], fg=self.TEXT, bg=self.SURFACE, font=("Malgun Gothic", 10, "bold")).pack(side="left", fill="x", expand=True)
        live_text, live_color = self._live_status_display(channel["id"])
        live_status = MarqueeText(details, live_text, fg=live_color, bg=self.SURFACE, font=("Malgun Gothic", 8), height=20)
        live_status.pack(fill="x", pady=(2, 0))
        self.live_status_widgets[channel["id"]] = live_status
        check_text, check_color = self._check_status_display(channel["id"])
        check_status = tk.Label(details, text=check_text, fg=check_color, bg=self.SURFACE, font=("Malgun Gothic", 8), anchor="w")
        check_status.pack(fill="x", pady=(3, 0))
        self.check_status_widgets[channel["id"]] = check_status
        tk.Frame(self.list_frame, bg=self.INPUT, height=1).pack(fill="x")

    def show_channel_menu(self, channel_id: str) -> None:
        channel = next((item for item in self.channels if item["id"] == channel_id), None)
        if channel is None:
            return
        row = self.channel_rows.get(channel_id)
        if row is None or not row.winfo_exists():
            return
        previous_menu = getattr(self, "channel_menu", None)
        if previous_menu is not None and previous_menu.winfo_exists():
            previous_menu.close()
        menu = self.channel_menu = ChannelOptionsMenu(
            self.root, interval=channel.get("interval", 60),
            edit_command=lambda: self.show_interval_editor(channel_id),
            delete_command=lambda: self.confirm_remove_channel(channel_id, channel.get("name") or channel_id),
            refresh_command=lambda: self.refresh_channel(channel_id),
            bg=self.INPUT, text_color=self.TEXT, muted=self.MUTED, danger=self.DANGER,
        )
        menu.show(row)

    def _live_status_display(self, channel_id: str) -> tuple[str, str]:
        live_state = self.live_info.get(channel_id)
        if live_state is None:
            return "방송 상태 확인 중…", self.MUTED
        if live_state[0]:
            return f"방송 중 · {live_state[1]}", self.ACCENT
        return "현재 방송 중이 아닙니다.", self.MUTED

    def _update_live_status_widget(self, channel_id: str) -> None:
        live_status = self.live_status_widgets.get(channel_id)
        if live_status is None or not live_status.winfo_exists():
            return
        live_text, live_color = self._live_status_display(channel_id)
        live_status.set_text(live_text, fg=live_color)

    def _check_status_display(self, channel_id: str) -> tuple[str, str]:
        last_success = getattr(self, "last_successful_check", {}).get(channel_id)
        if last_success is None:
            freshness = "아직 정상 확인하지 못했습니다"
        else:
            age = max(0, int(time.monotonic() - last_success))
            elapsed = "방금 전" if age < 10 else f"{age}초 전" if age < 60 else f"{age // 60}분 전" if age < 3600 else f"{age // 3600}시간 전"
            freshness = f"마지막 정상 확인 {elapsed}"
        if channel_id in getattr(self, "check_errors", set()):
            return f"확인 실패 · {freshness}", self.DANGER
        pending = getattr(getattr(self, "lookup_pool", None), "pending", {})
        if channel_id in getattr(self, "manual_checks", set()) or ("live", channel_id) in pending:
            return f"확인 중… · {freshness}", self.MUTED
        return ("확인 대기" if last_success is None else freshness), self.MUTED

    def _update_check_status_widget(self, channel_id: str) -> None:
        widget = getattr(self, "check_status_widgets", {}).get(channel_id)
        if widget is not None and widget.winfo_exists():
            text, color = self._check_status_display(channel_id)
            if widget.cget("text") != text or widget.cget("fg") != color:
                widget.configure(text=text, fg=color)

    def _refresh_check_indicators(self) -> None:
        if self.stop_event.is_set():
            return
        for channel_id in self.check_status_widgets:
            self._update_check_status_widget(channel_id)
        self._update_pause_controls()
        self.root.after(1_000, self._refresh_check_indicators)

    def _is_monitor_paused(self) -> bool:
        return time.monotonic() < getattr(self, "pause_until", 0)

    def pause_monitoring(self, minutes: int) -> None:
        if self.stop_event.is_set():
            return
        self.pause_until = time.monotonic() + minutes * 60
        canceled_closes = CHROME_TABS.cancel_tab_commands()
        for channel in self.channels:
            if LIVE_URL.format(channel_id=channel["id"]) in canceled_closes:
                self.was_live[channel["id"]] = True
        self._update_pause_controls()
        self._set_status(f"전체 감지를 {minutes}분 동안 쉽니다. 수동 갱신은 계속 사용할 수 있습니다.")

    def resume_monitoring(self) -> None:
        if self.stop_event.is_set():
            return
        self.pause_until = 0
        self.force_open_checks.update(channel["id"] for channel in self.channels if channel.get("enabled"))
        self._update_pause_controls()
        self._set_status("전체 감지를 다시 시작합니다. 감지 ON 채널의 방송 상태를 확인합니다.")

    def _update_pause_controls(self) -> None:
        if not hasattr(self, "pause_value"):
            return
        paused = self._is_monitor_paused()
        if paused:
            remaining = max(0, ceil(self.pause_until - time.monotonic()))
            self.pause_value.set(f"감지 쉬는 중 · {remaining // 60}분 {remaining % 60:02d}초 남음")
            if not self.pause_label.winfo_manager():
                self.pause_label.pack(fill="x", pady=(0, 12), after=self.monitor_controls)
            if not self.resume_button.winfo_manager():
                self.resume_button.pack(side="left", padx=(8, 0))
        else:
            self.pause_value.set("")
            self.pause_label.pack_forget()
            self.resume_button.pack_forget()

    def refresh_channel(self, channel_id: str) -> None:
        if self.stop_event.is_set() or not any(channel["id"] == channel_id for channel in self.channels):
            return
        self.manual_checks.add(channel_id)
        self._update_check_status_widget(channel_id)

    def refresh_all_channels(self) -> None:
        if self.stop_event.is_set() or self.refresh_batch or not self.channels:
            return
        self.refresh_batch = {channel["id"] for channel in self.channels}
        self.refresh_total = len(self.refresh_batch)
        self.refresh_failed.clear()
        self.manual_checks.update(self.refresh_batch)
        self._update_refresh_controls()
        for channel_id in self.refresh_batch:
            self._update_check_status_widget(channel_id)
        self._set_status(f"등록된 채널 {self.refresh_total}개의 방송 상태를 갱신합니다.")

    def _update_refresh_controls(self) -> None:
        if not hasattr(self, "refresh_all_button"):
            return
        batch = getattr(self, "refresh_batch", set())
        total = getattr(self, "refresh_total", len(batch))
        text = f"갱신 중 {total - len(batch)}/{total}" if batch else "전체 갱신"
        self.refresh_all_button.configure(text=text, state="disabled" if batch or not self.channels else "normal")

    def _finish_refresh_channel(self, channel_id: str, failed: bool = False) -> None:
        if channel_id not in self.refresh_batch:
            return
        self.refresh_batch.discard(channel_id)
        if failed:
            self.refresh_failed.add(channel_id)
        self._update_refresh_controls()
        if not self.refresh_batch:
            failed_count = len(self.refresh_failed)
            self._set_status(f"전체 갱신 완료 · {self.refresh_total - failed_count}개 정상 확인 · {failed_count}개 확인 실패", bool(failed_count))

    def _make_interval_editor(self, channel: dict) -> None:
        editor = tk.Frame(self.list_frame, bg="#373B45", padx=13, pady=10)
        row = self.channel_rows.get(channel["id"])
        pack_options = {"fill": "x", "pady": (0, 7)}
        if row is not None:
            pack_options["after"] = row
        editor.pack(**pack_options)
        self.interval_editors[channel["id"]] = editor
        tk.Label(editor, text="확인 간격", fg=self.TEXT, bg="#373B45", font=("Malgun Gothic", 9, "bold")).pack(side="left")
        tk.Label(editor, text="최소 15초", fg=self.MUTED, bg="#373B45", font=("Malgun Gothic", 8)).pack(side="left", padx=(7, 10))
        interval_value = tk.StringVar(value=str(channel.get("interval", 60)))
        interval_entry = tk.Entry(editor, textvariable=interval_value, width=5, bg=self.INPUT, fg=self.TEXT, insertbackground=self.TEXT, relief="flat", justify="center", font=("Consolas", 10))
        interval_entry.pack(side="left", ipady=6)
        tk.Label(editor, text="초", fg=self.MUTED, bg="#373B45", font=("Malgun Gothic", 9)).pack(side="left", padx=5)
        ttk.Button(editor, text="닫기", style="Small.TButton", command=lambda value=channel["id"]: self.show_interval_editor(value), cursor="hand2", width=8).pack(side="right")
        ttk.Button(editor, text="설정 저장", style="SmallAccent.TButton", command=lambda value=channel["id"], field=interval_value: self.update_interval(value, field.get()), cursor="hand2", width=8).pack(side="right", padx=(0, 8))
        interval_entry.focus_set()
        interval_entry.select_range(0, "end")

    def show_interval_editor(self, channel_id: str) -> None:
        previous_id = self.editing_channel_id
        if previous_id is not None:
            previous_editor = self.interval_editors.pop(previous_id, None)
            if previous_editor is not None:
                previous_editor.destroy()
        if previous_id == channel_id:
            self.editing_channel_id = None
            return
        channel = next((item for item in self.channels if item["id"] == channel_id), None)
        if channel is None:
            self.editing_channel_id = None
            return
        self.editing_channel_id = channel_id
        self._make_interval_editor(channel)

    def toggle_channel(self, channel_id: str) -> None:
        channels = [dict(item) for item in self.channels]
        for channel in channels:
            if channel["id"] == channel_id:
                channel["enabled"] = not bool(channel.get("enabled"))
                break
        if self._save_channels(channels):
            self.channels = channels
            self._invalidate_channel(channel_id)
            detection_button = self.detection_buttons.get(channel_id)
            if detection_button is not None and detection_button.winfo_exists():
                active = next((bool(item.get("enabled")) for item in channels if item["id"] == channel_id), False)
                detection_button.set_value(active)
            self._update_channel_count()

    def confirm_remove_channel(self, channel_id: str, channel_name: str) -> None:
        self._show_app_dialog("채널 삭제", f"‘{channel_name}’ 채널을 삭제하시겠습니까?", "삭제", lambda: self.remove_channel(channel_id), "취소")

    def remove_channel(self, channel_id: str) -> None:
        channels = [channel for channel in self.channels if channel["id"] != channel_id]
        if self._save_channels(channels):
            self.channels = channels
            self._invalidate_channel(channel_id)
            self.initial_checks.discard(channel_id)
            self.live_info.pop(channel_id, None)
            self.editing_channel_id = None
            self._refresh_list()

    def update_interval(self, channel_id: str, value: str) -> None:
        try:
            interval = int(value)
        except ValueError:
            self._show_app_dialog("입력 확인", "확인 간격은 15 이상의 숫자로 입력해 주세요.")
            return
        if interval < 15:
            self._show_app_dialog("입력 확인", "확인 간격은 최소 15초 이상이어야 합니다.")
            return
        channels = [dict(item) for item in self.channels]
        channel_name = channel_id
        for channel in channels:
            if channel["id"] == channel_id:
                channel["interval"] = interval
                channel_name = channel.get("name") or channel_id
                break
        if not self._save_channels(channels):
            return
        self.channels = channels
        self.last_checked.pop(channel_id, None)
        self.editing_channel_id = None
        interval_label = self.interval_labels.get(channel_id)
        if interval_label is not None and interval_label.winfo_exists():
            interval_label.configure(text=f"{interval}초")
        editor = self.interval_editors.pop(channel_id, None)
        if editor is not None:
            editor.destroy()
        self._set_status(f"{channel_name} 확인 간격을 {interval}초로 적용했습니다.")

    def _monitor(self) -> None:
        """Schedule independent lookups; apply every result on the UI thread."""
        if self.stop_event.is_set():
            return
        now = time.monotonic()
        if self.pause_until and now >= self.pause_until:
            self.resume_monitoring()
        self.lookup_pool.drain()
        paused = self._is_monitor_paused()
        # Explicit refreshes get capacity before routine polling.
        for channel in sorted(self.channels, key=lambda item: item["id"] not in self.manual_checks):
            channel_id = channel["id"]
            forced = channel_id in self.force_open_checks
            initial = channel_id in self.initial_checks
            manual = channel_id in self.manual_checks
            if not manual and (paused or (not initial and not forced and (not channel.get("enabled") or now - self.last_checked.get(channel_id, float("-inf")) < channel.get("interval", 60)))):
                continue
            generation = self.channel_generations.get(channel_id, 0)
            def complete(value, error, channel_id=channel_id, generation=generation, forced=forced):
                if generation != self.channel_generations.get(channel_id, 0):
                    return
                current = next((item for item in self.channels if item["id"] == channel_id), None)
                if current is None:
                    return
                self.last_checked[channel_id] = time.monotonic()
                self.manual_checks.discard(channel_id)
                if error:
                    self.check_errors.add(channel_id)
                    if forced:
                        self.retry_open_checks.add(channel_id)
                    self._update_check_status_widget(channel_id)
                    retry_hint = "다음 주기에 재시도합니다." if current.get("enabled") and not self._is_monitor_paused() else "수동 갱신으로 다시 확인할 수 있습니다."
                    self._set_status(f"방송 상태 확인 실패 · 이전 상태를 유지합니다. {retry_hint}", True)
                    self._finish_refresh_channel(channel_id, failed=True)
                    return
                is_live, title = value
                self.check_errors.discard(channel_id)
                self.last_successful_check[channel_id] = time.monotonic()
                self._record_live_status(channel_id, is_live, title)
                self._update_check_status_widget(channel_id)
                self._finish_refresh_channel(channel_id)
                if not current.get("enabled") or self._is_monitor_paused():
                    return
                reopen = forced or channel_id in self.retry_open_checks
                self.retry_open_checks.discard(channel_id)
                was_live = self.was_live.get(channel_id, False)
                self.was_live[channel_id] = is_live
                if is_live and (reopen or not was_live):
                    self._open_live(current, title)
                elif not is_live and was_live:
                    self._close_finished_live(current)
            if self.lookup_pool.submit(("live", channel_id), lambda channel_id=channel_id: get_live_status(channel_id), complete):
                # Keep manual intent until a valid result, including across toggles.
                if not paused:
                    self.initial_checks.discard(channel_id)
                    self.force_open_checks.discard(channel_id)
                self._update_check_status_widget(channel_id)
        self.root.after(100, self._monitor)

    def _invalidate_channel(self, channel_id):
        self.channel_generations[channel_id] = self.channel_generations.get(channel_id, 0) + 1
        self.was_live.pop(channel_id, None)
        self.last_checked.pop(channel_id, None)
        self.force_open_checks.discard(channel_id)
        self.retry_open_checks.discard(channel_id)
        if not any(channel["id"] == channel_id for channel in self.channels):
            self.manual_checks.discard(channel_id)
            self.last_successful_check.pop(channel_id, None)
            self.check_errors.discard(channel_id)
            if channel_id in self.refresh_batch:
                self.refresh_total -= 1
            self._finish_refresh_channel(channel_id)
        elif channel_id in self.refresh_batch:
            # A toggle invalidates an in-flight answer; keep the batch queued.
            self.manual_checks.add(channel_id)

    def _record_live_status(self, channel_id: str, is_live: bool, title: str) -> None:
        live_state = (is_live, title)
        if self.live_info.get(channel_id) == live_state:
            return
        self.live_info[channel_id] = live_state
        self._update_live_status_widget(channel_id)

    def _close_finished_live(self, channel: dict) -> None:
        """Close only tabs that the extension previously opened for this broadcast."""
        if self._is_monitor_paused() or not CHROME_TABS.is_connected():
            return
        command_id = CHROME_TABS.queue_background_close(LIVE_URL.format(channel_id=channel["id"]))
        if command_id:
            self._set_status(f"방송 종료 감지: {channel.get('name', channel['id'])} 자동 접속 탭을 닫는 중")

    def _open_live(self, channel: dict, title: str) -> None:
        if self._is_monitor_paused():
            return
        if not any(item["id"] == channel["id"] and item.get("enabled") for item in self.channels): return
        if self.stop_event.is_set() or not self.live_info.get(channel["id"], (False, ""))[0]:
            return
        remaining = self.allow_browser_open_after - time.monotonic()
        if remaining > 0:
            self._set_status("Chrome 방송 탭 상태를 확인하는 중입니다…")
            self.root.after(int(remaining * 1000) + 50, lambda: self._open_live(channel, title))
            return
        if CHROME_TABS.is_watched(channel["id"]):
            self._set_status(f"{channel.get('name', channel['id'])} 방송은 Chrome에서 이미 시청 중입니다.")
            return
        live_url = LIVE_URL.format(channel_id=channel["id"])
        if CHROME_TABS.is_connected():
            command_id = CHROME_TABS.queue_background_open(live_url)
            self._set_status(f"방송 시작 감지: {channel.get('name', channel['id'])} · Chrome 백그라운드 탭으로 여는 중")
            self.root.after(6_000, lambda: self._fallback_open(command_id, channel))
            return
        if self.chrome_launch_requested:
            # All channels share the same launch while Chrome restores tabs and
            # the selected profile's extension sends its first report.
            if time.monotonic() < self.extension_connection_deadline:
                self._set_status("Chrome 시작 후 확장 프로그램 연결을 기다리는 중입니다…")
                return
            if self._is_chrome_running():
                self._set_status("Chrome 확장 프로그램 연결을 확인하지 못해 방송을 자동으로 열지 않았습니다.", True)
                return
        if self._launch_selected_chrome():
            self._reset_extension_connection_check(EXTENSION_LAUNCH_CONNECTION_GRACE_SECONDS)
            self.chrome_launch_requested = True
            self._set_extension_status("Chrome 시작 후 확장 프로그램 연결 확인 중…")
            self._set_status("Chrome을 열어 확장 프로그램 연결을 기다리는 중입니다…")
            self.root.after(500, self._check_extension_connection)
            return
        self._set_status("Chrome 확장 프로그램이 연결되지 않아 방송을 자동으로 열지 않았습니다.", True)

    def _fallback_open(self, command_id: str, channel: dict) -> None:
        """Never bypass the extension when its background-tab command fails."""
        if not CHROME_TABS.is_pending(command_id): return
        CHROME_TABS.discard_command(command_id)
        self._set_status(f"{channel.get('name', channel['id'])} 방송 감지 · Chrome 확장 프로그램이 탭 열기를 확인하지 못해 자동으로 열지 않았습니다.", True)

    def _set_status(self, message: str, is_error: bool = False, clear_after: int = 5_000) -> None:
        """Show a temporary footer message while the app is doing work."""
        self.status_clear_token += 1
        clear_token = self.status_clear_token
        self.status_value.set(message)
        add_status = getattr(self, "add_status_label", None)
        if add_status is not None and add_status.winfo_exists():
            add_status.configure(fg=self.DANGER if is_error else self.MUTED)
        self.status_dot.itemconfigure(self.status_dot_item, fill=self.DANGER if is_error else self.ACCENT)
        if not self.status_frame.winfo_ismapped():
            self.status_frame.pack(fill="x", before=self.version_row)
        if clear_after > 0:
            self.root.after(clear_after, lambda: self._clear_status(clear_token))

    def _clear_status(self, clear_token: int) -> None:
        if clear_token != self.status_clear_token:
            return
        self._hide_status()

    def _hide_status(self) -> None:
        self.status_clear_token += 1
        self.status_value.set("")
        self.status_frame.pack_forget()

    def _ui(self, callback, *args) -> None:
        if hasattr(self, "stop_event") and self.stop_event.is_set():
            return
        self.ui_queue.put((callback, args))

    def _drain_ui_queue(self) -> None:
        if self.stop_event.is_set():
            return
        for _ in range(100):
            if self.ui_queue.empty():
                break
            callback, args = self.ui_queue.get()
            callback(*args)
            if self.stop_event.is_set():
                return
        self.root.after(50, self._drain_ui_queue)

    def _create_tray_icon(self):
        if pystray is None: return None
        if LOGO_PATH.is_file():
            image = Image.open(LOGO_PATH).convert("RGBA")
            image.thumbnail((64, 64), Image.Resampling.LANCZOS)
        else:
            image = Image.new("RGBA", (64, 64), self.BG); draw = ImageDraw.Draw(image); draw.ellipse((8, 8, 56, 56), fill=self.ACCENT); draw.polygon(((27, 22), (27, 42), (44, 32)), fill=self.BG)
        return pystray.Icon("AutoChzzk", image, APP_NAME, menu=pystray.Menu(pystray.MenuItem("창 열기", self.show_window, default=True), pystray.MenuItem("종료", self.quit_from_tray)))

    def _start_tray_icon(self) -> None:
        if pystray is None or self.tray_icon is not None:
            return
        self.tray_icon = self._create_tray_icon()
        if self.tray_icon is not None:
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def hide_to_tray(self) -> None:
        if pystray is None: messagebox.showwarning(APP_NAME, "트레이 기능에 필요한 패키지가 없습니다. `python -m pip install -r requirements.txt`를 실행해 주세요."); return
        self.root.withdraw()
        self._start_tray_icon()

    def show_window(self, _icon=None, _item=None) -> None: self._ui(self._restore_window)
    def _restore_window(self) -> None: self.root.deiconify(); self.root.lift(); self.root.focus_force()
    def quit_from_tray(self, _icon=None, _item=None) -> None: self._ui(self.on_close)
    def on_close(self) -> None:
        self.stop_event.set()
        secret = getattr(self, "_copied_pairing_secret", None)
        if secret is not None:
            try:
                if self.root.clipboard_get() == secret:
                    self.root.clipboard_clear()
            except tk.TclError:
                pass
            self._copied_pairing_secret = None
        self.lookup_pool.close()
        if self.extension_server is not None: self.extension_server.shutdown(); self.extension_server.server_close()
        clear_show_window_callback()
        if self.tray_icon is not None: self.tray_icon.stop()
        self.root.destroy()


def main() -> None:
    """Start the desktop application, keeping only one Windows instance active."""
    # Keep one monitoring process only. A second launch quietly exits.
    mutex = None
    if sys.platform == "win32":
        import ctypes
        enable_windows_dpi_awareness()
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LPRS1234.AutoChzzk")
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            try:
                request_show_window()
            except (OSError, ValueError):
                pass
            return
    root = tk.Tk()
    AutoChzzkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
