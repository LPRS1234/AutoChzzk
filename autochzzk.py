"""AutoChzzk - open saved CHZZK channels when they start a live broadcast."""
from __future__ import annotations

import json
import os
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

from autochzzk_core.chrome_profiles import get_chrome_profiles
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
    EXTENSION_PORT,
    ICO_PATH,
    LIVE_URL,
    LOGO_PATH,
    MUTEX_NAME,
    UPDATE_CHECK_INTERVAL_SECONDS,
    enable_windows_dpi_awareness,
)
from autochzzk_core.extension import (
    CHROME_TABS,
    clear_show_window_callback,
    start_extension_server,
)
from autochzzk_core.storage import load_channels, load_settings, save_channels, save_settings
from autochzzk_core.updater import (
    UpdateCancelled,
    UpdateError,
    UpdateInfo,
    download_update,
    find_available_update,
    launch_installer,
    verify_installer,
)
from autochzzk_core.widgets import MarqueeText

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
        root.title(APP_NAME)
        root.geometry("620x650")
        root.minsize(540, 540)
        root.configure(bg=self.BG)
        self.input_value, self.status_value = tk.StringVar(), tk.StringVar()
        self.extension_status_value = tk.StringVar(value="Chrome 확장 프로그램 연결 확인 중…")
        self.settings = self._load_settings()
        self._scan_chrome_profiles()
        self.profile_value = tk.StringVar(value=self.selected_chrome_profile["name"])
        self._apply_selected_profile()
        self.channels = self._load_channels()
        # Migrate channels created by versions before per-channel intervals.
        self._save_channels()
        self.was_live: dict[str, bool] = {}
        self.live_info: dict[str, tuple[bool, str]] = {}
        self.editing_channel_id: str | None = None
        # Delay the normal polling loop while the startup check runs, so every
        # enabled saved channel is checked exactly once as soon as the app opens.
        self.last_checked: dict[str, float] = {channel["id"]: time.monotonic() for channel in self.channels if channel.get("enabled")}
        self.stop_event = threading.Event()
        self.tray_icon = None
        self.active_dialog = None
        self.update_download_in_progress = False
        self.update_prompted_version: str | None = None
        self.extension_setup_prompted = False
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
        threading.Thread(target=self._monitor, daemon=True).start()
        threading.Thread(target=self._check_saved_channels_on_start, daemon=True).start()
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
        style.configure("SmallAccent.TButton", background=self.ACCENT, foreground="#08251D", borderwidth=0, font=("Malgun Gothic", 8, "bold"), padding=(5, 5))
        style.map("SmallAccent.TButton", background=[("active", "#38EDBB")])
        style.configure("DialogAccent.TButton", background=self.ACCENT, foreground="#08251D", borderwidth=0, font=("Malgun Gothic", 9, "bold"), padding=(10, 7))
        style.map("DialogAccent.TButton", background=[("active", "#38EDBB")])
        style.configure("DialogDark.TButton", background="#3A3D47", foreground=self.TEXT, borderwidth=0, font=("Malgun Gothic", 9, "bold"), padding=(10, 7))
        style.map("DialogDark.TButton", background=[("active", "#50545F")])
        style.configure("Dark.TCombobox", fieldbackground=self.INPUT, background="#3A3D47", foreground=self.TEXT, bordercolor="#40444F", lightcolor="#40444F", darkcolor="#40444F", arrowcolor=self.TEXT, padding=5)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", self.INPUT), ("focus", self.INPUT)], background=[("active", "#50545F")], bordercolor=[("focus", self.ACCENT)], arrowcolor=[("active", self.ACCENT)])
        style.configure("Dark.Vertical.TScrollbar", background="#3A3D47", troughcolor=self.SURFACE, bordercolor=self.SURFACE, arrowcolor=self.MUTED, arrowsize=0)
        style.map("Dark.Vertical.TScrollbar", background=[("active", "#50545F"), ("pressed", self.ACCENT)])
        self.root.option_add("*TCombobox*Listbox.background", self.INPUT)
        self.root.option_add("*TCombobox*Listbox.foreground", self.TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#08251D")

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=self.BG, padx=30, pady=24); outer.pack(fill="both", expand=True)
        heading = tk.Frame(outer, bg=self.BG); heading.pack(fill="x")
        ttk.Button(heading, text="종료", style="Dark.TButton", command=self.on_close, cursor="hand2").pack(side="right", padx=(0, 0), pady=(4, 0))
        tk.Label(heading, text=APP_NAME, fg=self.TEXT, bg=self.BG, font=("Malgun Gothic", 18, "bold")).pack(anchor="w")
        tk.Label(heading, text="저장한 채널의 방송 시작을 자동 감지합니다", fg=self.MUTED, bg=self.BG, font=("Malgun Gothic", 9)).pack(anchor="w")
        profile_row = tk.Frame(outer, bg=self.BG)
        profile_row.pack(fill="x", pady=(13, 0))
        tk.Label(profile_row, text="사용 중인 Chrome 프로필", fg=self.TEXT, bg=self.BG, font=("Malgun Gothic", 9, "bold")).pack(side="left")
        self.profile_change_button = ttk.Button(profile_row, text="프로필 변경", style="Small.TButton", command=self.show_profile_editor, cursor="hand2")
        if len(self.chrome_profiles) > 1:
            self.profile_change_button.pack(side="right")
        self.current_profile_label = tk.Label(profile_row, text=self.profile_value.get(), fg=self.ACCENT, bg=self.BG, font=("Malgun Gothic", 9, "bold"))
        self.current_profile_label.pack(side="right", padx=(0, 9))
        extension_row = tk.Frame(outer, bg=self.BG)
        extension_row.pack(fill="x", pady=(5, 0))
        self.extension_status_dot = tk.Label(extension_row, text="●", fg=self.MUTED, bg=self.BG, font=("Segoe UI", 8))
        self.extension_status_dot.pack(side="left", padx=(0, 5))
        tk.Label(extension_row, textvariable=self.extension_status_value, fg=self.MUTED, bg=self.BG, font=("Malgun Gothic", 8), anchor="w").pack(side="left")
        ttk.Button(extension_row, text="설치 안내", style="Small.TButton", command=self.show_extension_install_guide, cursor="hand2").pack(side="right")
        tk.Label(outer, text="자동 접속을 사용하려면 선택한 Chrome 프로필에 확장 프로그램을 설치해야 합니다.", fg=self.MUTED, bg=self.BG, font=("Malgun Gothic", 8), anchor="w").pack(fill="x", pady=(2, 0))
        self.profile_editor = tk.Frame(outer, bg=self.SURFACE, padx=14, pady=10)
        tk.Label(self.profile_editor, text="변경할 Chrome 프로필", fg=self.TEXT, bg=self.SURFACE, font=("Malgun Gothic", 9, "bold")).pack(side="left")
        self.profile_selector = ttk.Combobox(self.profile_editor, textvariable=self.profile_value, values=list(self.profile_labels), state="readonly", width=20, font=("Malgun Gothic", 9), style="Dark.TCombobox")
        self.profile_selector.pack(side="left", padx=(10, 8))
        self.profile_selector.bind("<<ComboboxSelected>>", lambda _event: self.root.after_idle(self._clear_profile_selector_highlight))
        ttk.Button(self.profile_editor, text="프로필 적용", style="Accent.TButton", command=self.select_chrome_profile, cursor="hand2").pack(side="right")
        self.add_card = tk.Frame(outer, bg=self.SURFACE, padx=17, pady=15); self.add_card.pack(fill="x", pady=(20, 14))
        add_card = self.add_card
        tk.Label(add_card, text="채널 추가", fg=self.TEXT, bg=self.SURFACE, font=("Malgun Gothic", 10, "bold")).pack(anchor="w")
        input_row = tk.Frame(add_card, bg=self.SURFACE); input_row.pack(fill="x", pady=(8, 0))
        entry = tk.Entry(input_row, textvariable=self.input_value, bg=self.INPUT, fg=self.TEXT, insertbackground=self.TEXT, relief="flat", font=("Consolas", 10), highlightthickness=1, highlightbackground="#40444F", highlightcolor=self.ACCENT)
        entry.pack(side="left", fill="x", expand=True, ipady=9); entry.bind("<Return>", lambda _event: self.add_channel())
        ttk.Button(input_row, text="등록", style="Accent.TButton", command=self.add_channel, cursor="hand2").pack(side="left", padx=(8, 0))
        tk.Label(add_card, text="치지직 채널 URL 또는 32자리 채널 ID", fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 8)).pack(anchor="w", pady=(5, 0))
        controls = tk.Frame(outer, bg=self.BG); controls.pack(fill="x", pady=(0, 7))
        self.count_label = tk.Label(controls, fg=self.TEXT, bg=self.BG, font=("Malgun Gothic", 10, "bold")); self.count_label.pack(side="left")
        status = tk.Frame(outer, bg="#1D2C29", padx=13, pady=9)
        status.pack(fill="x", pady=(13, 0), side="bottom")
        self.status_dot = tk.Canvas(status, width=10, height=10, bg="#1D2C29", highlightthickness=0)
        self.status_dot_item = self.status_dot.create_oval(3, 3, 7, 7, fill=self.ACCENT, outline="")
        self.status_dot.pack(side="left", padx=(0, 7))
        tk.Label(status, textvariable=self.status_value, fg=self.TEXT, bg="#1D2C29", font=("Malgun Gothic", 9), anchor="w").pack(side="left", fill="x", expand=True)
        list_box = tk.Frame(outer, bg=self.SURFACE); list_box.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(list_box, bg=self.SURFACE, highlightthickness=0, height=255)
        scrollbar = ttk.Scrollbar(list_box, orient="vertical", command=self.canvas.yview, style="Dark.Vertical.TScrollbar")
        self.list_frame = tk.Frame(self.canvas, bg=self.SURFACE, padx=12, pady=10)
        self.list_window = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set); self.canvas.pack(side="left", fill="both", expand=True); scrollbar.pack(side="right", fill="y")
        self.list_frame.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.list_window, width=event.width))
        self.root.bind_all("<MouseWheel>", self._on_list_mousewheel, add="+")

    def _on_list_mousewheel(self, event) -> str | None:
        """Scroll the channel list when the pointer is over its visible area."""
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
        return load_channels()

    def _load_settings(self) -> dict:
        return load_settings()

    def _scan_chrome_profiles(self) -> None:
        """Read Chrome's profile list on every app launch."""
        self.chrome_profiles = get_chrome_profiles()
        self.profile_labels = {profile["name"]: profile for profile in self.chrome_profiles}
        saved_profile_directory = self.settings.get("chrome_profile_directory")
        self.selected_chrome_profile = next((profile for profile in self.chrome_profiles if profile["directory"] == saved_profile_directory), self.chrome_profiles[0])
        if saved_profile_directory and self.selected_chrome_profile["directory"] != saved_profile_directory:
            self.settings["chrome_profile_directory"] = self.selected_chrome_profile["directory"]
            self._save_settings()

    def _check_selected_profile_exists(self) -> None:
        if self.stop_event.is_set():
            return
        current_directory = self.selected_chrome_profile["directory"]
        available_profiles = get_chrome_profiles()
        known_profiles = [(profile["directory"], profile["name"]) for profile in self.chrome_profiles]
        refreshed_profiles = [(profile["directory"], profile["name"]) for profile in available_profiles]
        current_profile_exists = any(profile["directory"] == current_directory for profile in available_profiles)
        if known_profiles != refreshed_profiles:
            previous_name = self.selected_chrome_profile["name"]
            self.chrome_profiles = available_profiles
            self.profile_labels = {profile["name"]: profile for profile in self.chrome_profiles}
            if current_profile_exists:
                self.selected_chrome_profile = next(profile for profile in self.chrome_profiles if profile["directory"] == current_directory)
            else:
                self.selected_chrome_profile = self.chrome_profiles[0]
                self.settings["chrome_profile_directory"] = self.selected_chrome_profile["directory"]
                self._save_settings()
                self._apply_selected_profile()
            self.profile_value.set(self.selected_chrome_profile["name"])
            self.current_profile_label.configure(text=self.selected_chrome_profile["name"])
            self.profile_selector.configure(values=list(self.profile_labels))
            if len(self.chrome_profiles) > 1:
                self.profile_change_button.pack(side="right")
            else:
                self.profile_change_button.pack_forget()
            if not current_profile_exists:
                self.profile_editor.pack_forget()
                self._reset_extension_connection_check()
                self._set_extension_status("Chrome 확장 프로그램 연결 확인 중…")
                self._set_status(f"사용 중이던 Chrome 프로필({previous_name})이 삭제되어 {self.selected_chrome_profile['name']} 프로필로 변경했습니다.", True)
                self.root.after(500, self._check_extension_connection)
        self.root.after(5_000, self._check_selected_profile_exists)

    def _save_settings(self) -> None:
        save_settings(self.settings)

    def _apply_selected_profile(self) -> None:
        profile_keys = set()
        if self.selected_chrome_profile.get("gaia_id"):
            profile_keys.add(f"gaia:{self.selected_chrome_profile['gaia_id']}")
        if self.selected_chrome_profile.get("email"):
            profile_keys.add(f"email:{self.selected_chrome_profile['email'].lower()}")
        CHROME_TABS.set_selected_profile(profile_keys)

    def _reset_extension_connection_check(self) -> None:
        self.extension_setup_prompted = False
        self.extension_connection_deadline = time.monotonic() + EXTENSION_CONNECTION_GRACE_SECONDS

    def _set_extension_status(self, message: str, connected: bool | None = None) -> None:
        self.extension_status_value.set(message)
        if hasattr(self, "extension_status_dot"):
            self.extension_status_dot.configure(fg=self.ACCENT if connected else self.DANGER if connected is False else self.MUTED)

    def _schedule_update_check(self) -> None:
        if self.stop_event.is_set():
            return
        threading.Thread(target=self._check_for_update, daemon=True).start()
        self.root.after(UPDATE_CHECK_INTERVAL_SECONDS * 1_000, self._schedule_update_check)

    def _check_for_update(self) -> None:
        """Check published GitHub Releases without delaying app monitoring."""
        try:
            update_info = find_available_update(get_latest_release())
            if update_info is None or self.update_prompted_version == update_info.version:
                return
            self._ui(self._start_update_download, update_info)
        except (UpdateError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError, OSError):
            # An update check must never interrupt normal channel monitoring.
            return

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

    def _refresh_extension_status(self) -> None:
        if self.stop_event.is_set():
            return
        if CHROME_TABS.is_connected():
            self._set_extension_status("Chrome 확장 프로그램 연결됨", True)
        else:
            self._set_extension_status("Chrome 확장 프로그램 연결 안 됨", False)
        self._update_monitor_status()
        self.root.after(2_000, self._refresh_extension_status)

    def show_profile_editor(self) -> None:
        if len(self.chrome_profiles) < 2:
            return
        if self.profile_editor.winfo_ismapped():
            self.profile_editor.pack_forget()
            return
        self.profile_value.set(self.selected_chrome_profile["name"])
        self.profile_editor.pack(fill="x", pady=(8, 0), before=self.add_card)
        self._clear_profile_selector_highlight()
        self.root.after_idle(self._clear_profile_selector_highlight)

    def _clear_profile_selector_highlight(self) -> None:
        self.profile_selector.selection_clear()
        self.root.focus_set()

    def select_chrome_profile(self) -> None:
        profile = self.profile_labels.get(self.profile_value.get())
        if profile is None:
            return
        self.profile_editor.pack_forget()
        if profile == self.selected_chrome_profile:
            return
        self.selected_chrome_profile = profile
        self.settings["chrome_profile_directory"] = profile["directory"]
        self._save_settings()
        self._apply_selected_profile()
        self._reset_extension_connection_check()
        self.current_profile_label.configure(text=profile["name"])
        self._set_extension_status("Chrome 확장 프로그램 연결 확인 중…")
        self._set_status(f"{profile['name']} Chrome 프로필에서만 방송 감지와 자동 접속을 사용합니다.")
        self.root.after(1_000, self._check_extension_connection)

    def _save_channels(self) -> None:
        save_channels(self.channels)

    def _show_app_dialog(self, title: str, message: str, confirm_text: str = "확인", confirm_command=None, cancel_text: str | None = None, cancel_command=None) -> None:
        """Show an app-styled modal instead of a Windows system dialog."""
        if self.active_dialog is not None and self.active_dialog.winfo_exists():
            self.active_dialog.lift()
            return

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
            if callback is not None:
                callback()

        if cancel_text:
            ttk.Button(buttons, text=cancel_text, style="DialogDark.TButton", command=lambda: close(cancel_command), cursor="hand2", width=12).pack(side="right")
        ttk.Button(buttons, text=confirm_text, style="DialogAccent.TButton", command=lambda: close(confirm_command), cursor="hand2", width=12).pack(side="right", padx=(0, 8) if cancel_text else 0)
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.update_idletasks()
        root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        x = root_x + max(0, (self.root.winfo_width() - dialog.winfo_width()) // 2)
        y = root_y + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.grab_set()
        dialog.focus_set()

    def _open_chrome_extensions(self) -> None:
        chrome_paths = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
        chrome_path = next((path for path in chrome_paths if path.is_file()), None)
        if chrome_path is not None:
            subprocess.Popen(
                [str(chrome_path), f"--profile-directory={self.selected_chrome_profile['directory']}", "chrome://extensions/"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            webbrowser.open("chrome://extensions", new=1)

    def show_extension_install_guide(self) -> None:
        self._show_app_dialog(
            "Chrome 확장 프로그램 설치 안내",
            f"선택한 Chrome 프로필({self.selected_chrome_profile['name']})에만 설치하면 됩니다.\n\n1. Chrome 열기를 누릅니다.\n2. chrome://extensions 에 접속합니다.\n3. 화면 우측 상단의 ‘개발자 모드’를 켭니다.\n4. ‘압축해제된 확장 프로그램 로드’를 눌러 AutoChzzk 설치 폴더의 chrome_extension 폴더를 선택합니다.\n\n이미 다른 프로필에 설치했다면 ‘프로필 변경’에서 그 프로필로 바꿔 주세요.",
            "Chrome 열기",
            self._open_chrome_extensions,
            "확인했습니다",
        )

    def _check_extension_connection(self) -> None:
        if self.stop_event.is_set():
            return
        if CHROME_TABS.is_connected():
            self._set_extension_status("Chrome 확장 프로그램 연결됨", True)
            if not self.extension_setup_prompted:
                self.extension_setup_prompted = True
                threading.Thread(target=self._open_current_lives_after_extension_connect, daemon=True).start()
            return
        if self.extension_setup_prompted:
            return
        if time.monotonic() < self.extension_connection_deadline:
            self._set_extension_status("Chrome 확장 프로그램 연결 확인 중…")
            self.root.after(500, self._check_extension_connection)
            return
        self._set_extension_status("Chrome 확장 프로그램 연결 안 됨", False)
        self.extension_setup_prompted = True
        self.show_extension_install_guide()

    def _open_current_lives_after_extension_connect(self) -> None:
        """Open broadcasts that were already live while the extension was disconnected."""
        for channel in [dict(item) for item in self.channels if item.get("enabled")]:
            if self.stop_event.is_set():
                return
            try:
                is_live, title = get_live_status(channel["id"])
            except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
                continue
            self._ui(self._record_live_status, channel["id"], is_live, title)
            if is_live:
                self._ui(self._open_live, channel, title)

    def add_channel(self) -> None:
        channel_id = extract_channel_id(self.input_value.get())
        if not channel_id:
            message = "채널 URL 또는 32자리 채널 ID를 입력해 주세요." if not self.input_value.get().strip() else "치지직 채널 URL 또는 32자리 채널 ID를 정확히 입력해 주세요."
            self._show_app_dialog("입력 확인", message)
            return
        if any(channel["id"] == channel_id for channel in self.channels): messagebox.showinfo(APP_NAME, "이미 등록된 채널입니다."); return
        self._set_status("채널 정보를 불러오는 중…"); self.root.update_idletasks()
        try: name = get_channel_name(channel_id)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError): name = channel_id
        new_channel = {"id": channel_id, "name": name, "enabled": True, "interval": 60}
        self.channels.append(new_channel); self._save_channels(); self.input_value.set(""); self._refresh_list()
        self.last_checked[channel_id] = time.monotonic()
        threading.Thread(target=self._check_channel_now, args=(dict(new_channel),), daemon=True).start()

    def _refresh_list(self) -> None:
        for child in self.list_frame.winfo_children(): child.destroy()
        enabled_count = sum(bool(channel.get("enabled")) for channel in self.channels)
        self.count_label.configure(text=f"등록 채널 {len(self.channels)}개 · 감지 중 {enabled_count}개")
        if not self.channels: tk.Label(self.list_frame, text="아직 등록된 채널이 없습니다.", fg=self.MUTED, bg=self.SURFACE, font=("Malgun Gothic", 10), pady=28).pack()
        for channel in self.channels:
            self._make_channel_row(channel)
            if self.editing_channel_id == channel["id"]: self._make_interval_editor(channel)
        self._update_monitor_status()

    def _update_monitor_status(self) -> None:
        if not self.channels:
            self._set_status("감지할 채널을 등록하세요.")
            return
        watching_count = sum(CHROME_TABS.is_watched(channel["id"]) for channel in self.channels)
        live_count = sum(bool(self.live_info.get(channel["id"], (False, ""))[0]) for channel in self.channels)
        if watching_count:
            self._set_status(f"{watching_count}개의 방송을 시청 중")
        elif live_count:
            self._set_status(f"현재 방송 중인 등록 채널이 {live_count}개 있습니다.")
        else:
            self._set_status("현재 방송 중인 등록 채널이 없습니다.")

    def _make_channel_row(self, channel: dict) -> None:
        row = tk.Frame(self.list_frame, bg=self.INPUT, padx=12, pady=9); row.pack(fill="x", pady=4)
        actions = tk.Frame(row, bg=self.INPUT)
        actions.pack(side="right", anchor="n")
        active = bool(channel.get("enabled")); label = "감지 ON" if active else "감지 OFF"
        tk.Button(actions, text=label, command=lambda value=channel["id"]: self.toggle_channel(value), relief="flat", bd=0, cursor="hand2", padx=9, pady=5, font=("Malgun Gothic", 8, "bold"), bg=self.ACCENT if active else "#454954", fg="#08251D" if active else self.TEXT, activebackground="#38EDBB" if active else "#5A5F6B").pack(side="right", padx=(7, 0))
        ttk.Button(actions, text="삭제", style="Small.TButton", command=lambda value=channel["id"], name=channel.get("name") or channel["id"]: self.confirm_remove_channel(value, name), cursor="hand2").pack(side="right")
        tk.Label(actions, text=f"{channel.get('interval', 60)}초", fg=self.MUTED, bg=self.INPUT, font=("Consolas", 9)).pack(side="right", padx=(0, 5))
        ttk.Button(actions, text="간격 수정", style="Small.TButton", command=lambda value=channel["id"]: self.show_interval_editor(value), cursor="hand2").pack(side="right", padx=(0, 8))
        details = tk.Frame(row, bg=self.INPUT)
        details.pack(side="left", fill="both", expand=True, padx=(0, 10))
        MarqueeText(details, channel.get("name") or channel["id"], fg=self.TEXT, bg=self.INPUT, font=("Malgun Gothic", 10, "bold")).pack(fill="x")
        live_state = self.live_info.get(channel["id"])
        if live_state is None:
            live_text, live_color = "방송 상태 확인 중…", self.MUTED
        elif live_state[0]:
            live_text, live_color = f"방송 중 · {live_state[1]}", self.ACCENT
        else:
            live_text, live_color = "현재 방송 중이 아닙니다.", self.MUTED
        MarqueeText(details, live_text, fg=live_color, bg=self.INPUT, font=("Malgun Gothic", 8), height=20).pack(fill="x", pady=(2, 0))

    def _make_interval_editor(self, channel: dict) -> None:
        editor = tk.Frame(self.list_frame, bg="#373B45", padx=13, pady=10)
        editor.pack(fill="x", pady=(0, 7))
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
        self.editing_channel_id = None if self.editing_channel_id == channel_id else channel_id
        self._refresh_list()

    def toggle_channel(self, channel_id: str) -> None:
        for channel in self.channels:
            if channel["id"] == channel_id:
                channel["enabled"] = not bool(channel.get("enabled")); self.was_live.pop(channel_id, None); break
        self._save_channels(); self._refresh_list()

    def confirm_remove_channel(self, channel_id: str, channel_name: str) -> None:
        self._show_app_dialog("채널 삭제", f"‘{channel_name}’ 채널을 삭제하시겠습니까?", "삭제", lambda: self.remove_channel(channel_id), "취소")

    def remove_channel(self, channel_id: str) -> None:
        self.channels = [channel for channel in self.channels if channel["id"] != channel_id]; self.was_live.pop(channel_id, None); self.editing_channel_id = None; self._save_channels(); self._refresh_list()

    def update_interval(self, channel_id: str, value: str) -> None:
        try:
            interval = int(value)
        except ValueError:
            self._show_app_dialog("입력 확인", "확인 간격은 15 이상의 숫자로 입력해 주세요.")
            return
        if interval < 15:
            self._show_app_dialog("입력 확인", "확인 간격은 최소 15초 이상이어야 합니다.")
            return
        channel_name = channel_id
        for channel in self.channels:
            if channel["id"] == channel_id:
                channel["interval"] = interval; channel_name = channel.get("name") or channel_id; break
        self._save_channels(); self.last_checked.pop(channel_id, None); self.editing_channel_id = None; self._refresh_list()
        self._set_status(f"{channel_name} 확인 간격을 {interval}초로 적용했습니다.")

    def _monitor(self) -> None:
        while not self.stop_event.is_set():
            for channel in [dict(item) for item in self.channels if item.get("enabled")]:
                if self.stop_event.is_set(): break
                channel_id = channel["id"]
                if time.monotonic() - self.last_checked.get(channel_id, float("-inf")) < channel.get("interval", 60): continue
                self.last_checked[channel_id] = time.monotonic()
                try:
                    self._process_live_status(channel)
                except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError): self._ui(self._set_status, f"{channel.get('name', channel['id'])} 확인 실패 · 다음 주기에 재시도합니다.", True)
            self.stop_event.wait(1)

    def _check_channel_now(self, channel: dict) -> None:
        """Check a just-added channel immediately instead of waiting for the next cycle."""
        try:
            self._process_live_status(channel, added=True)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
            self._ui(self._set_status, f"{channel.get('name', channel['id'])} 등록 완료 · 상태 확인은 다음 주기에 재시도합니다.", True)

    def _check_saved_channels_on_start(self) -> None:
        """Show the current live state for every saved channel when the app opens."""
        saved_channels = [dict(channel) for channel in self.channels]
        if saved_channels:
            self._ui(self._set_status, f"저장된 채널 {len(saved_channels)}개의 방송 상태를 확인 중…")
        for channel in saved_channels:
            if self.stop_event.is_set(): return
            try:
                self._process_live_status(channel)
            except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
                self._ui(self._set_status, f"{channel.get('name', channel['id'])} 초기 확인 실패 · 다음 주기에 재시도합니다.", True)

    def _process_live_status(self, channel: dict, added: bool = False) -> None:
        is_live, title = get_live_status(channel["id"])
        self._ui(self._record_live_status, channel["id"], is_live, title)
        if is_live and not self.was_live.get(channel["id"], False): self.was_live[channel["id"]] = True; self._ui(self._open_live, channel, title)
        elif not is_live:
            self.was_live[channel["id"]] = False
            if added: self._ui(self._set_status, f"{channel.get('name', channel['id'])} 등록 완료 · 현재 오프라인")

    def _record_live_status(self, channel_id: str, is_live: bool, title: str) -> None:
        self.live_info[channel_id] = (is_live, title)
        self._refresh_list()

    def _open_live(self, channel: dict, title: str) -> None:
        if not any(item["id"] == channel["id"] and item.get("enabled") for item in self.channels): return
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
        self._set_status("Chrome 확장 프로그램이 연결되지 않아 방송을 자동으로 열지 않았습니다.", True)

    def _fallback_open(self, command_id: str, channel: dict) -> None:
        """Never bypass the extension when its background-tab command fails."""
        if not CHROME_TABS.is_pending(command_id): return
        CHROME_TABS.discard_command(command_id)
        self._set_status(f"{channel.get('name', channel['id'])} 방송 감지 · Chrome 확장 프로그램이 탭 열기를 확인하지 못해 자동으로 열지 않았습니다.", True)

    def _set_status(self, message: str, is_error: bool = False) -> None:
        self.status_value.set(message)
        self.status_dot.itemconfigure(self.status_dot_item, fill=self.DANGER if is_error else self.ACCENT)

    def _ui(self, callback, *args) -> None: self.root.after(0, callback, *args)

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

    def show_window(self, _icon=None, _item=None) -> None: self.root.after(0, self._restore_window)
    def _restore_window(self) -> None: self.root.deiconify(); self.root.lift(); self.root.focus_force()
    def quit_from_tray(self, _icon=None, _item=None) -> None: self.root.after(0, self.on_close)
    def on_close(self) -> None:
        self.stop_event.set()
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
            request = urllib.request.Request(f"http://127.0.0.1:{EXTENSION_PORT}/show-window", data=b"{}", method="POST")
            try:
                urllib.request.urlopen(request, timeout=1).close()
            except urllib.error.URLError:
                pass
            return
    root = tk.Tk()
    AutoChzzkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
