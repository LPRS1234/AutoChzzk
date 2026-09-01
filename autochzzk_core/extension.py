"""Local bridge shared by AutoChzzk and its Chrome extension."""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import CHANNEL_ID_PATTERN, EXTENSION_PORT


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
        return {
            client_id: report
            for client_id, report in self._fresh_clients().items()
            if report[1] & self.selected_profile_keys
        }

    def update(self, client_id: str, channel_ids: set[str], profile_keys: set[str], focused: bool) -> None:
        with self.lock:
            self.clients[client_id] = (channel_ids, profile_keys, time.monotonic())
            self.clients = self._fresh_clients()
            if focused:
                self.last_focused_client_id = client_id

    def is_watched(self, channel_id: str) -> bool:
        with self.lock:
            return any(
                channel_id in channel_ids
                for channel_ids, _profile_keys, _updated_at in self._selected_clients().values()
            )

    def is_connected(self) -> bool:
        with self.lock:
            return bool(self._selected_clients())

    def queue_background_open(self, url: str) -> str:
        command_id = uuid.uuid4().hex
        with self.lock:
            clients = self._selected_clients()
            if not clients:
                return ""
            target_client_id = (
                self.last_focused_client_id
                if self.last_focused_client_id in clients
                else max(clients, key=lambda client_id: clients[client_id][2])
            )
            self.pending_opens[command_id] = (url, target_client_id)
        return command_id

    def pending_commands(self, client_id: str) -> list[dict[str, str]]:
        with self.lock:
            return [
                {"id": command_id, "url": url}
                for command_id, (url, target_client_id) in self.pending_opens.items()
                if target_client_id == client_id
            ]

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
    show_window_callback: Callable[[], None] | None = None

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
            show_window_callback = type(self).show_window_callback
            if show_window_callback is not None:
                show_window_callback()
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
            channel_ids = {
                value.lower()
                for value in payload.get("channelIds", [])
                if isinstance(value, str) and CHANNEL_ID_PATTERN.fullmatch(value)
            }
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


def start_extension_server(show_window_callback: Callable[[], None]) -> ThreadingHTTPServer | None:
    ExtensionRequestHandler.show_window_callback = show_window_callback
    try:
        server = ThreadingHTTPServer(("127.0.0.1", EXTENSION_PORT), ExtensionRequestHandler)
    except OSError:
        ExtensionRequestHandler.show_window_callback = None
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def clear_show_window_callback() -> None:
    ExtensionRequestHandler.show_window_callback = None
