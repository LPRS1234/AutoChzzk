"""Local bridge shared by AutoChzzk and its Chrome extension."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import socket
import sys
import urllib.request
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import CHANNEL_ID_PATTERN, EXTENSION_PORT, LOCAL_DATA_DIR


class ChromeTabState:
    """Short-lived CHZZK tab reports from every installed Chrome profile."""

    def __init__(self) -> None:
        self.clients: dict[str, tuple[set[str], set[str], float, str]] = {}
        self.selected_profile_keys: set[str] = set()
        self.last_focused_client_id: str | None = None
        self.pending_opens: dict[str, tuple[str, str, float]] = {}
        self.pending_closes: dict[str, tuple[str, str, float]] = {}
        self.pending_reloads: dict[str, tuple[str, str, float]] = {}
        self.reload_attempts: set[tuple[str, str]] = set()
        self.lock = threading.Lock()

    def _fresh_clients(self) -> dict[str, tuple[set[str], set[str], float, str]]:
        now = time.monotonic()
        return {client_id: report for client_id, report in self.clients.items() if now - report[2] < 30}

    def set_selected_profile(self, profile_keys: set[str]) -> None:
        with self.lock:
            if self.selected_profile_keys != profile_keys:
                self.pending_opens.clear()
                self.pending_closes.clear()
                self.pending_reloads.clear()
                self.reload_attempts.clear()
            self.selected_profile_keys = set(profile_keys)

    def _selected_clients(self) -> dict[str, tuple[set[str], set[str], float, str]]:
        return {
            client_id: report
            for client_id, report in self._fresh_clients().items()
            if report[1] & self.selected_profile_keys
        }

    def update(
        self,
        client_id: str,
        channel_ids: set[str],
        profile_keys: set[str],
        focused: bool,
        extension_version: str = "",
    ) -> None:
        with self.lock:
            self.clients = self._fresh_clients()
            if client_id not in self.clients and len(self.clients) >= 256:
                return
            self.clients[client_id] = (channel_ids, profile_keys, time.monotonic(), extension_version)
            if focused:
                self.last_focused_client_id = client_id

    def is_watched(self, channel_id: str) -> bool:
        with self.lock:
            return any(
                channel_id in channel_ids
                for channel_ids, _profile_keys, _updated_at, _extension_version in self._selected_clients().values()
            )

    def is_connected(self) -> bool:
        with self.lock:
            return bool(self._selected_clients())

    def selected_extension_versions(self) -> set[str]:
        with self.lock:
            return {report[3] for report in self._selected_clients().values()}

    def selected_extension_needs_update(self, required_version: str) -> bool:
        required_parts = _version_parts(required_version)
        with self.lock:
            clients = self._selected_clients()
            if not clients or required_parts is None:
                return False
            for report in clients.values():
                installed_parts = _version_parts(report[3])
                if installed_parts is None or installed_parts < required_parts:
                    return True
            return False

    def queue_background_open(self, url: str) -> str:
        if not isinstance(url, str) or not LIVE_URL_PATTERN.fullmatch(url):
            return ""
        command_id = uuid.uuid4().hex
        with self.lock:
            self._expire_commands()
            clients = self._selected_clients()
            if not clients:
                return ""
            target_client_id = (
                self.last_focused_client_id
                if self.last_focused_client_id in clients
                else max(clients, key=lambda client_id: clients[client_id][2])
            )
            self.pending_closes = {key: value for key, value in self.pending_closes.items()
                                   if value[:2] != (url, target_client_id)}
            if len(self.pending_opens) + len(self.pending_closes) + len(self.pending_reloads) >= 128:
                return ""
            self.pending_opens[command_id] = (url, target_client_id, time.monotonic())
        return command_id

    def queue_background_close(self, url: str) -> str:
        """Ask the selected Chrome profile to close an app-opened broadcast tab."""
        if not isinstance(url, str) or not LIVE_URL_PATTERN.fullmatch(url):
            return ""
        command_id = uuid.uuid4().hex
        with self.lock:
            self._expire_commands()
            clients = self._selected_clients()
            if not clients:
                return ""
            target_client_id = (
                self.last_focused_client_id
                if self.last_focused_client_id in clients
                else max(clients, key=lambda client_id: clients[client_id][2])
            )
            self.pending_opens = {key: value for key, value in self.pending_opens.items()
                                 if value[:2] != (url, target_client_id)}
            if len(self.pending_opens) + len(self.pending_closes) + len(self.pending_reloads) >= 128:
                return ""
            self.pending_closes[command_id] = (url, target_client_id, time.monotonic())
        return command_id

    def queue_extension_reload(self, required_version: str) -> bool:
        """Ask each outdated selected extension to reload its unpacked files once."""
        required_parts = _version_parts(required_version)
        if required_parts is None:
            return False
        queued = False
        with self.lock:
            self._expire_commands()
            for client_id, report in self._selected_clients().items():
                installed_parts = _version_parts(report[3])
                attempt = (client_id, required_version)
                if installed_parts is not None and installed_parts >= required_parts:
                    continue
                if attempt in self.reload_attempts:
                    continue
                if len(self.pending_opens) + len(self.pending_closes) + len(self.pending_reloads) >= 128:
                    break
                command_id = uuid.uuid4().hex
                self.pending_reloads[command_id] = (client_id, required_version, time.monotonic())
                self.reload_attempts.add(attempt)
                queued = True
        return queued

    def _expire_commands(self) -> None:
        now = time.monotonic()
        self.pending_opens = {key: value for key, value in self.pending_opens.items() if now - value[2] < 30}
        self.pending_closes = {key: value for key, value in self.pending_closes.items() if now - value[2] < 30}
        self.pending_reloads = {key: value for key, value in self.pending_reloads.items() if now - value[2] < 30}

    def pending_commands(self, client_id: str) -> list[dict[str, str]]:
        with self.lock:
            self._expire_commands()
            commands = [
                {"id": command_id, "action": "open", "url": url}
                for command_id, (url, target_client_id, _created) in self.pending_opens.items()
                if target_client_id == client_id
            ]
            commands.extend(
                {"id": command_id, "action": "close", "url": url}
                for command_id, (url, target_client_id, _created) in self.pending_closes.items()
                if target_client_id == client_id
            )
            commands.extend(
                {"id": command_id, "action": "reload", "version": version}
                for command_id, (target_client_id, version, _created) in self.pending_reloads.items()
                if target_client_id == client_id
            )
            return commands

    def acknowledge_commands(self, client_id: str, command_ids: list[str]) -> None:
        with self.lock:
            for command_id in command_ids:
                command = self.pending_opens.get(command_id)
                if command is not None and command[1] == client_id:
                    self.pending_opens.pop(command_id, None)
                command = self.pending_closes.get(command_id)
                if command is not None and command[1] == client_id:
                    self.pending_closes.pop(command_id, None)
                command = self.pending_reloads.get(command_id)
                if command is not None and command[0] == client_id:
                    self.pending_reloads.pop(command_id, None)

    def is_pending(self, command_id: str) -> bool:
        with self.lock:
            self._expire_commands()
            return bool(command_id) and command_id in self.pending_opens

    def discard_command(self, command_id: str) -> None:
        with self.lock:
            self.pending_opens.pop(command_id, None)
            self.pending_closes.pop(command_id, None)
            self.pending_reloads.pop(command_id, None)


CHROME_TABS = ChromeTabState()


# This shared key is entered by the user in the extension options; never sent over HTTP.
LIVE_URL_PATTERN = re.compile(r"https://chzzk\.naver\.com/live/[0-9a-f]{32}", re.IGNORECASE)
PAIRING_PATH = LOCAL_DATA_DIR / "extension_pairing.key"
MAX_BODY = 32_768
AUTH_WINDOW = 60
_PAIRING_LOCK = threading.Lock()


def _version_parts(version: str) -> tuple[int, ...] | None:
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){2,3}", version):
        return None
    return tuple(int(part) for part in version.split("."))


def _protect_key(data: bytes, *, decrypt: bool = False) -> bytes:
    """Use Windows per-user DPAPI, never machine-wide encryption or UI prompts."""
    if sys.platform != "win32":
        raise OSError("확장 프로그램 연결 코드 보호에는 Windows가 필요합니다.")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = DataBlob(len(data), buffer)
    result = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("Windows에서 확장 프로그램 연결 코드를 보호하거나 읽지 못했습니다.")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.memset(result.pbData, 0, result.cbData)
        kernel32.LocalFree(result.pbData)


def get_pairing_secret() -> str:
    """Read/create a per-user pairing secret outside the source repository."""
    with _PAIRING_LOCK:
        PAIRING_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not PAIRING_PATH.exists():
            protected = _protect_key(secrets.token_hex(32).encode("ascii"))
            try:
                with PAIRING_PATH.open("xb") as stream:
                    stream.write(protected)
            except FileExistsError:
                pass
        value = _protect_key(PAIRING_PATH.read_bytes(), decrypt=True).decode("ascii")
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("확장 프로그램 연결 코드 파일이 손상되었습니다.")
    return value


def _mac(secret: str, message: bytes) -> str:
    return hmac.new(bytes.fromhex(secret), message, hashlib.sha256).hexdigest()


def signed_request_headers(path: str, body: bytes, secret: str | None = None) -> dict[str, str]:
    timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
    signature = _mac(secret or get_pairing_secret(), f"POST\n{path}\n{timestamp}\n{nonce}\n".encode() + body)
    return {"Content-Type": "application/json", "X-AutoChzzk-Time": timestamp,
            "X-AutoChzzk-Nonce": nonce, "X-AutoChzzk-Signature": signature}


def request_show_window() -> None:
    body = b"{}"
    secret = get_pairing_secret()
    headers = signed_request_headers("/show-window", body, secret)
    request = urllib.request.Request(f"http://127.0.0.1:{EXTENSION_PORT}/show-window", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=2) as response:
        data = response.read(MAX_BODY + 1)
        expected = _mac(secret, f"response\n{headers['X-AutoChzzk-Nonce']}\n{response.status}\n".encode() + data)
        if len(data) > MAX_BODY or not hmac.compare_digest(response.headers.get("X-AutoChzzk-Signature", ""), expected):
            raise OSError("실행 중인 AutoChzzk 앱의 응답을 확인하지 못했습니다.")


class ExtensionHTTPServer(ThreadingHTTPServer):
    """Bound workers and socket lifetime, including clients that never send a body."""
    daemon_threads = True

    def __init__(self, address, handler, secret: str):
        self.pairing_secret = secret
        self.nonces: dict[str, float] = {}
        self.auth_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        def expire_connection():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        # An idle timeout alone lets a peer hold a worker by dripping one byte at a time.
        deadline = threading.Timer(5, expire_connection)
        deadline.daemon = True
        deadline.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            deadline.cancel()
            self.slots.release()


class ExtensionRequestHandler(BaseHTTPRequestHandler):
    show_window_callback: Callable[[], None] | None = None

    def handle(self) -> None:
        try:
            super().handle()
        except OSError:
            # Disconnects and the absolute connection deadline are normal failures.
            self.close_connection = True

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return bool(origin and re.fullmatch(r"chrome-extension://[a-p]{32}", origin))

    def _reply(self, status: int = 200, payload: dict | None = None) -> None:
        body = json.dumps(payload if payload is not None else {"ok": status == 200}, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        if self._origin_allowed():
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-AutoChzzk-Time, X-AutoChzzk-Nonce, X-AutoChzzk-Signature")
            self.send_header("Access-Control-Expose-Headers", "X-AutoChzzk-Signature")
        if getattr(self, "authenticated_nonce", None):
            signature = _mac(self.server.pairing_secret, f"response\n{self.authenticated_nonce}\n{status}\n".encode() + body)
            self.send_header("X-AutoChzzk-Signature", signature)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (OSError, socket.timeout):
            pass

    def _valid_host(self) -> bool:
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._reply(200 if self._valid_host() and self._origin_allowed() and self.path in ("/challenge", "/chzzk-tabs") else 403)

    def _authenticate(self, body: bytes) -> bool:
        timestamp = self.headers.get("X-AutoChzzk-Time", "")
        nonce = self.headers.get("X-AutoChzzk-Nonce", "")
        signature = self.headers.get("X-AutoChzzk-Signature", "")
        if not re.fullmatch(r"[0-9]{1,12}", timestamp) or abs(time.time() - int(timestamp)) > AUTH_WINDOW:
            return False
        if not re.fullmatch(r"[0-9a-f]{32}", nonce) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            return False
        expected = _mac(self.server.pairing_secret, f"POST\n{self.path}\n{timestamp}\n{nonce}\n".encode() + body)
        if not hmac.compare_digest(signature, expected):
            return False
        with self.server.auth_lock:
            now = time.monotonic()
            self.server.nonces = {key: created for key, created in self.server.nonces.items() if now - created < AUTH_WINDOW * 2}
            if nonce in self.server.nonces or len(self.server.nonces) >= 4096:
                return False
            self.server.nonces[nonce] = now
        self.authenticated_nonce = nonce
        return True

    def do_POST(self) -> None:  # noqa: N802
        if not self._valid_host() or (not self._origin_allowed() and not (self.path == "/show-window" and self.headers.get("Origin") is None)):
            self._reply(403)
            return
        if self.path not in ("/show-window", "/challenge", "/chzzk-tabs"):
            self._reply(404)
            return
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get("Transfer-Encoding"):
                self._reply(400)
                return
            size = int(lengths[0])
            if not 0 < size <= MAX_BODY:
                self._reply(413)
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self._reply(415)
                return
            body = self.rfile.read(size)
            if len(body) != size:
                self._reply(400)
                return
            if not self._authenticate(body):
                self._reply(401)
                return
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                self._reply(400)
                return
            if self.path == "/challenge":
                self._reply(payload={"ok": True, "protocol": 2})
                return
            if self.path == "/show-window":
                callback = type(self).show_window_callback
                if callback is not None:
                    callback()
                self._reply(200 if callback else 503)
                return
            client_id = payload.get("clientId")
            channel_ids = payload.get("channelIds")
            completed = payload.get("completedCommandIds")
            focused = payload.get("focused")
            if (not isinstance(client_id, str) or not 8 <= len(client_id) <= 128
                    or not isinstance(focused, bool)
                    or not isinstance(channel_ids, list) or len(channel_ids) > 256
                    or any(not isinstance(value, str) or not CHANNEL_ID_PATTERN.fullmatch(value) for value in channel_ids)
                    or not isinstance(completed, list) or len(completed) > 128
                    or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value) for value in completed)):
                self._reply(400)
                return
            for field, limit in (("profileGaiaId", 128), ("profileEmail", 320), ("extensionVersion", 32)):
                if not isinstance(payload.get(field), str) or len(payload[field]) > limit:
                    self._reply(400)
                    return
            profile_keys = set()
            if payload["profileGaiaId"]:
                profile_keys.add("gaia:" + payload["profileGaiaId"])
            if payload["profileEmail"]:
                profile_keys.add("email:" + payload["profileEmail"].lower())
            CHROME_TABS.update(client_id, {value.lower() for value in channel_ids}, profile_keys, focused, payload["extensionVersion"])
            CHROME_TABS.acknowledge_commands(client_id, completed)
            self._reply(payload={"ok": True, "openCommands": CHROME_TABS.pending_commands(client_id), "acknowledgedCommandIds": completed})
        except (ValueError, UnicodeDecodeError):
            self._reply(400)
        except (OSError, socket.timeout):
            self._reply(408)

    def log_message(self, _format: str, *_args) -> None:
        return


def start_extension_server(show_window_callback: Callable[[], None]) -> ThreadingHTTPServer | None:
    ExtensionRequestHandler.show_window_callback = show_window_callback
    try:
        server = ExtensionHTTPServer(("127.0.0.1", EXTENSION_PORT), ExtensionRequestHandler, get_pairing_secret())
    except (OSError, ValueError):
        ExtensionRequestHandler.show_window_callback = None
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def clear_show_window_callback() -> None:
    ExtensionRequestHandler.show_window_callback = None
