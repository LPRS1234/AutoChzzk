"""AutoChzzk - open saved CHZZK channels when they start a live broadcast."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk

try:
    import pystray
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    pystray = None

APP_NAME = "AutoChzzk"
APP_VERSION = "1.1.0"
UPDATE_API_URL = "https://api.github.com/repos/LPRS1234/AutoChzzk/releases/latest"
MUTEX_NAME = "Local\\AutoChzzk_SingleInstance_1"
if getattr(sys, "frozen", False):
    APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    APP_DIR.mkdir(parents=True, exist_ok=True)
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).parent
    RESOURCE_DIR = APP_DIR
DATA_PATH = APP_DIR / "channels.json"
SETTINGS_PATH = APP_DIR / "settings.json"
LOGO_PATH = RESOURCE_DIR / "assets" / "logo" / "app-icon.png"
ICO_PATH = RESOURCE_DIR / "assets" / "logo" / "app-icon.ico"
LIVE_API_URL = "https://api.chzzk.naver.com/polling/v3.1/channels/{channel_id}/live-status"
CHANNEL_API_URL = "https://api.chzzk.naver.com/service/v1/channels/{channel_id}"
LIVE_URL = "https://chzzk.naver.com/live/{channel_id}"
EXTENSION_PORT = 8765
EXTENSION_INITIAL_SYNC_SECONDS = 12
CHANNEL_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
URL_ID_PATTERN = re.compile(r"chzzk\.naver\.com/(?:live/)?([0-9a-f]{32})(?:[/?#]|$)", re.IGNORECASE)
APP_INSTANCE = None


def extract_channel_id(value: str) -> str | None:
    value = value.strip()
    if CHANNEL_ID_PATTERN.fullmatch(value):
        return value.lower()
    match = URL_ID_PATTERN.search(value)
    return match.group(1).lower() if match else None


def request_content(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AutoChzzk/1.1", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response).get("content") or {}


def get_channel_name(channel_id: str) -> str:
    return request_content(CHANNEL_API_URL.format(channel_id=channel_id)).get("channelName") or channel_id


def get_live_status(channel_id: str) -> tuple[bool, str]:
    content = request_content(LIVE_API_URL.format(channel_id=channel_id))
    return content.get("status") == "OPEN", content.get("liveTitle") or "제목 없는 방송"


def version_key(version: str) -> tuple[int, ...]:
    """Convert a release tag such as v1.2.0 into a comparable version tuple."""
    numbers = re.findall(r"\d+", version)
    return tuple(int(number) for number in numbers) if numbers else ()


def get_latest_release() -> dict:
    request = urllib.request.Request(
        UPDATE_API_URL,
        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def get_chrome_profiles() -> list[dict[str, str]]:
    local_state = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Local State"
    user_data_dir = local_state.parent
    try:
        info_cache = json.loads(local_state.read_text(encoding="utf-8")).get("profile", {}).get("info_cache", {})
    except (OSError, json.JSONDecodeError):
        info_cache = {}
    profiles = []
    for directory, info in info_cache.items():
        if not isinstance(directory, str) or not isinstance(info, dict):
            continue
        if not (user_data_dir / directory).is_dir():
            continue
        email = str(info.get("user_name") or "")
        name = email.split("@", 1)[0] if "@" in email else str(info.get("name") or info.get("gaia_name") or directory)
        profiles.append({"directory": directory, "name": name, "gaia_id": str(info.get("gaia_id") or ""), "email": email})
    return profiles or [{"directory": "Default", "name": "기본 프로필", "gaia_id": "", "email": ""}]


class ChromeTabState:
    """Short-lived CHZZK tab reports from every installed Chrome profile."""
    def __init__(self) -> None:
        self.clients: dict[str, tuple[set[str], set[str], float]] = {}
        self.selected_profile_keys: set[str] = set()
        self.last_focused_client_id: str | None = None
        self.pending_opens: dict[str, tuple[str, str]] = {}
        self.lock = threading.Lock()

    def _fresh_clients(self) -> dict[str, tuple[set[str], set[str], float]]:
        now = time.monotonic()
        return {client_id: report for client_id, report in self.clients.items() if now - report[2] < 30}

    def set_selected_profile(self, profile_keys: set[str]) -> None:
        with self.lock:
            self.selected_profile_keys = profile_keys

    def _selected_clients(self) -> dict[str, tuple[set[str], set[str], float]]:
        return {client_id: report for client_id, report in self._fresh_clients().items() if report[1] & self.selected_profile_keys}

    def update(self, client_id: str, channel_ids: set[str], profile_keys: set[str], focused: bool) -> None:
        with self.lock:
            self.clients[client_id] = (channel_ids, profile_keys, time.monotonic())
            self.clients = self._fresh_clients()
            if focused:
                self.last_focused_client_id = client_id

    def is_watched(self, channel_id: str) -> bool:
        with self.lock:
            return any(channel_id in channel_ids for channel_ids, _profile_keys, _updated_at in self._selected_clients().values())

    def is_connected(self) -> bool:
        with self.lock:
            return bool(self._selected_clients())

    def queue_background_open(self, url: str) -> str:
        command_id = uuid.uuid4().hex
        with self.lock:
            clients = self._selected_clients()
            if not clients:
                return ""
            target_client_id = self.last_focused_client_id if self.last_focused_client_id in clients else max(clients, key=lambda client_id: clients[client_id][2])
            self.pending_opens[command_id] = (url, target_client_id)
        return command_id

    def pending_commands(self, client_id: str) -> list[dict[str, str]]:
        with self.lock:
            return [{"id": command_id, "url": url} for command_id, (url, target_client_id) in self.pending_opens.items() if target_client_id == client_id]

    def acknowledge_commands(self, client_id: str, command_ids: list[str]) -> None:
        with self.lock:
            for command_id in command_ids:
                command = self.pending_opens.get(command_id)
                if command is not None and command[1] == client_id:
                    self.pending_opens.pop(command_id, None)

    def is_pending(self, command_id: str) -> bool:
        with self.lock:
            return bool(command_id) and command_id in self.pending_opens

    def discard_command(self, command_id: str) -> None:
        with self.lock:
            self.pending_opens.pop(command_id, None)


CHROME_TABS = ChromeTabState()


class ExtensionRequestHandler(BaseHTTPRequestHandler):
    def _reply(self, status: int = 200, payload: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload or {"ok": True}).encode("utf-8"))

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._reply()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/show-window":
            if APP_INSTANCE is not None:
                APP_INSTANCE._ui(APP_INSTANCE._restore_window)
                self._reply()
            else:
                self._reply(503)
            return
        if self.path != "/chzzk-tabs":
            self._reply(404)
            return
        try:
            size = max(0, min(int(self.headers.get("Content-Length", "0")), 16_384))
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            client_id = payload.get("clientId", "")
            if not isinstance(client_id, str) or not 8 <= len(client_id) <= 128:
                self._reply(400)
                return
            channel_ids = {value.lower() for value in payload.get("channelIds", []) if isinstance(value, str) and CHANNEL_ID_PATTERN.fullmatch(value)}
            profile_keys = set()
            profile_gaia_id = payload.get("profileGaiaId", "")
            profile_email = payload.get("profileEmail", "")
            if isinstance(profile_gaia_id, str) and profile_gaia_id:
                profile_keys.add(f"gaia:{profile_gaia_id}")
            if isinstance(profile_email, str) and profile_email:
                profile_keys.add(f"email:{profile_email.lower()}")
            CHROME_TABS.update(client_id, channel_ids, profile_keys, bool(payload.get("focused")))
            completed = [value for value in payload.get("completedCommandIds", []) if isinstance(value, str)]
            CHROME_TABS.acknowledge_commands(client_id, completed)
            self._reply(payload={"ok": True, "openCommands": CHROME_TABS.pending_commands(client_id)})
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._reply(400)

    def log_message(self, _format: str, *_args) -> None:
        return


def start_extension_server() -> ThreadingHTTPServer | None:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", EXTENSION_PORT), ExtensionRequestHandler)
    except OSError:
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


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
        if not self.winfo_exists(): return
        if self.text_width <= self.winfo_width():
            self.scrolling = False
            self.coords(self.item, 0, self.winfo_height() // 2)
            return
        self.scrolling = True
        if self.after_id is None: self.after_id = self.after(700, self._scroll)

    def _scroll(self) -> None:
        self.after_id = None
        try:
            if not self.winfo_exists() or not self.scrolling: return
            x, y = self.coords(self.item)
            x -= 1
            if x + self.text_width < 0: x = self.winfo_width() + 12
            self.coords(self.item, x, y)
            self.after_id = self.after(35, self._scroll)
        except tk.TclError:
            return


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
        self.extension_setup_prompted = False
        self.extension_server = start_extension_server()
        self.window_icon = None
        self.header_icon = None
        self._load_brand_icons()
        # Chrome extensions can be asleep while the desktop app starts. Wait
        # for one periodic tab report before opening any startup-detected live.
        self.allow_browser_open_after = time.monotonic() + EXTENSION_INITIAL_SYNC_SECONDS
        self._configure_styles()
        self._build_ui()
        global APP_INSTANCE
        APP_INSTANCE = self
        self._refresh_list()
        root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        root.after(500, self._check_extension_connection)
        root.after(1_000, self._refresh_extension_status)
        root.after(5_000, self._check_selected_profile_exists)
        threading.Thread(target=self._monitor, daemon=True).start()
        threading.Thread(target=self._check_saved_channels_on_start, daemon=True).start()
        threading.Thread(target=self._check_for_update, daemon=True).start()

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
        tk.Label(controls, text="채널별 확인 간격은 목록에서 수정할 수 있습니다 · 최소 15초", fg=self.MUTED, bg=self.BG, font=("Malgun Gothic", 9)).pack(side="right")
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

    def _load_channels(self) -> list[dict]:
        try:
            loaded = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            channels = []
            for item in loaded:
                if not isinstance(item, dict) or not CHANNEL_ID_PATTERN.fullmatch(item.get("id", "")): continue
                try: item["interval"] = max(15, int(item.get("interval", 60)))
                except (TypeError, ValueError): item["interval"] = 60
                channels.append(item)
            return channels
        except (FileNotFoundError, json.JSONDecodeError): return []

    def _load_settings(self) -> dict:
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

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
                self.extension_setup_prompted = False
                self._set_extension_status("Chrome 확장 프로그램 연결 확인 중…")
                self._set_status(f"사용 중이던 Chrome 프로필({previous_name})이 삭제되어 {self.selected_chrome_profile['name']} 프로필로 변경했습니다.", True)
                self.root.after(500, self._check_extension_connection)
        self.root.after(5_000, self._check_selected_profile_exists)

    def _save_settings(self) -> None:
        SETTINGS_PATH.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def _apply_selected_profile(self) -> None:
        profile_keys = set()
        if self.selected_chrome_profile.get("gaia_id"):
            profile_keys.add(f"gaia:{self.selected_chrome_profile['gaia_id']}")
        if self.selected_chrome_profile.get("email"):
            profile_keys.add(f"email:{self.selected_chrome_profile['email'].lower()}")
        CHROME_TABS.set_selected_profile(profile_keys)

    def _set_extension_status(self, message: str, connected: bool | None = None) -> None:
        self.extension_status_value.set(message)
        if hasattr(self, "extension_status_dot"):
            self.extension_status_dot.configure(fg=self.ACCENT if connected else self.DANGER if connected is False else self.MUTED)

    def _check_for_update(self) -> None:
        """Check published GitHub Releases without delaying the app startup."""
        try:
            release = get_latest_release()
            latest_version = str(release.get("tag_name") or release.get("name") or "")
            if not latest_version or version_key(latest_version) <= version_key(APP_VERSION):
                return
            assets = release.get("assets") or []
            installer = next((asset for asset in assets if str(asset.get("name", "")).lower().endswith(".exe")), None)
            download_url = str((installer or {}).get("browser_download_url") or release.get("html_url") or "")
            if download_url:
                self._ui(self._offer_update, latest_version.lstrip("vV"), download_url)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError, OSError):
            # An update check must never interrupt normal channel monitoring.
            return

    def _offer_update(self, latest_version: str, download_url: str) -> None:
        if self.stop_event.is_set():
            return
        if self.active_dialog is not None and self.active_dialog.winfo_exists():
            self.root.after(1_000, lambda: self._offer_update(latest_version, download_url))
            return
        self._show_app_dialog(
            "새 업데이트가 있습니다",
            f"AutoChzzk {latest_version} 버전을 설치할 수 있습니다.\n현재 버전: {APP_VERSION}\n\n다운로드 페이지를 열어 새 설치 파일을 실행해 주세요.",
            "다운로드",
            lambda: webbrowser.open(download_url, new=2),
            "나중에",
        )

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
        self.profile_selector.selection_clear()
        self.root.after_idle(self.root.focus_set)

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
        self.extension_setup_prompted = False
        self.current_profile_label.configure(text=profile["name"])
        self._set_extension_status("Chrome 확장 프로그램 연결 확인 중…")
        self._set_status(f"{profile['name']} Chrome 프로필에서만 방송 감지와 자동 접속을 사용합니다.")
        self.root.after(1_000, self._check_extension_connection)

    def _save_channels(self) -> None:
        DATA_PATH.write_text(json.dumps(self.channels, ensure_ascii=False, indent=2), encoding="utf-8")

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
            self._set_status(f"{live_count}개의 방송을 적용 프로필과 다른 프로필에서 시청 중입니다.", True)
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
        editor.pack(fill="x", padx=7, pady=(0, 7))
        tk.Label(editor, text="확인 간격", fg=self.TEXT, bg="#373B45", font=("Malgun Gothic", 9, "bold")).pack(side="left")
        tk.Label(editor, text="최소 15초", fg=self.MUTED, bg="#373B45", font=("Malgun Gothic", 8)).pack(side="left", padx=(7, 10))
        interval_value = tk.StringVar(value=str(channel.get("interval", 60)))
        interval_entry = tk.Entry(editor, textvariable=interval_value, width=5, bg=self.INPUT, fg=self.TEXT, insertbackground=self.TEXT, relief="flat", justify="center", font=("Consolas", 10))
        interval_entry.pack(side="left", ipady=6)
        tk.Label(editor, text="초", fg=self.MUTED, bg="#373B45", font=("Malgun Gothic", 9)).pack(side="left", padx=5)
        ttk.Button(editor, text="설정 저장", style="Accent.TButton", command=lambda value=channel["id"], field=interval_value: self.update_interval(value, field.get())).pack(side="right")
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
        try: interval = max(15, int(value))
        except ValueError:
            messagebox.showerror(APP_NAME, "확인 간격은 15 이상의 숫자로 입력해 주세요."); return
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

    def hide_to_tray(self) -> None:
        if pystray is None: messagebox.showwarning(APP_NAME, "트레이 기능에 필요한 패키지가 없습니다. `python -m pip install -r requirements.txt`를 실행해 주세요."); return
        self.root.withdraw()
        if self.tray_icon is None: self.tray_icon = self._create_tray_icon(); threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_window(self, _icon=None, _item=None) -> None: self.root.after(0, self._restore_window)
    def _restore_window(self) -> None: self.root.deiconify(); self.root.lift(); self.root.focus_force()
    def quit_from_tray(self, _icon=None, _item=None) -> None: self.root.after(0, self.on_close)
    def on_close(self) -> None:
        global APP_INSTANCE
        self.stop_event.set()
        if self.extension_server is not None: self.extension_server.shutdown(); self.extension_server.server_close()
        if self.tray_icon is not None: self.tray_icon.stop()
        APP_INSTANCE = None
        self.root.destroy()


if __name__ == "__main__":
    # Keep one monitoring process only. A second launch quietly exits.
    mutex = None
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LPRS1234.AutoChzzk")
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            request = urllib.request.Request(f"http://127.0.0.1:{EXTENSION_PORT}/show-window", data=b"{}", method="POST")
            try:
                urllib.request.urlopen(request, timeout=1).close()
            except urllib.error.URLError:
                pass
            sys.exit(0)
    root = tk.Tk(); AutoChzzkApp(root); root.mainloop()
