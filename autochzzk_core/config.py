"""Application constants and platform-specific startup configuration."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

APP_NAME = "AutoChzzk"
APP_VERSION = "1.2.0"
UPDATE_API_URL = "https://api.github.com/repos/LPRS1234/AutoChzzk/releases/latest"
MUTEX_NAME = "Local\\AutoChzzk_SingleInstance_1"

if getattr(sys, "frozen", False):
    APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    APP_DIR.mkdir(parents=True, exist_ok=True)
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
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
EXTENSION_CONNECTION_GRACE_SECONDS = 6

CHANNEL_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
URL_ID_PATTERN = re.compile(
    r"chzzk\.naver\.com/(?:live/)?([0-9a-f]{32})(?:[/?#]|$)",
    re.IGNORECASE,
)


def enable_windows_dpi_awareness() -> None:
    """Render Tk widgets at the display's native DPI instead of bitmap scaling."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        return

