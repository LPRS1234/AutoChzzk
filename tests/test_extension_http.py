from __future__ import annotations

import http.client
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from autochzzk_core import extension


class BridgeHTTPTests(unittest.TestCase):
    def setUp(self):
        # Synthetic keys only; never create/read the real user's pairing file.
        self.secret = "ab" * 32
        self.server = extension.ExtensionHTTPServer(("127.0.0.1", 0), extension.ExtensionRequestHandler, self.secret)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.tabs_patch = patch.object(extension, "CHROME_TABS", extension.ChromeTabState())
        self.tabs_patch.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tabs_patch.stop()

    def request(self, payload, *, path="/chzzk-tabs", headers=None, signed=True):
        body = json.dumps(payload).encode()
        request_headers = extension.signed_request_headers(path, body, self.secret) if signed else {"Content-Type": "application/json"}
        request_headers["Origin"] = "chrome-extension://" + "a" * 32
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("POST", path, body, request_headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.headers), data, request_headers
        finally:
            connection.close()

    def report(self):
        return {"clientId": "synthetic-client", "focused": True, "channelIds": ["a" * 32],
                "profileGaiaId": "synthetic-id", "profileEmail": "test@example.invalid",
                "extensionVersion": "2.0.0", "completedCommandIds": []}

    def test_challenge_and_report_have_verified_response_proof(self):
        for path, payload in (("/challenge", {}), ("/chzzk-tabs", self.report())):
            status, headers, data, request_headers = self.request(payload, path=path)
            self.assertEqual(status, 200)
            expected = extension._mac(self.secret, f"response\n{request_headers['X-AutoChzzk-Nonce']}\n200\n".encode() + data)
            self.assertEqual(headers["X-AutoChzzk-Signature"], expected)
            self.assertEqual(headers["Access-Control-Allow-Origin"], "chrome-extension://" + "a" * 32)

    def test_unsigned_wrong_key_and_replayed_requests_are_rejected(self):
        self.assertEqual(self.request(self.report(), signed=False)[0], 401)
        body = json.dumps(self.report()).encode()
        wrong = extension.signed_request_headers("/chzzk-tabs", body, "cd" * 32)
        self.assertEqual(self.request(self.report(), headers=wrong)[0], 401)
        status, _, _, headers = self.request(self.report())
        self.assertEqual(status, 200)
        self.assertEqual(self.request(self.report(), headers=headers)[0], 401)

    def test_invalid_host_origin_and_unsigned_native_request_are_rejected(self):
        for headers in ({"Host": "example.com"}, {"Origin": "https://example.com"}, {"Origin": "null"}):
            self.assertEqual(self.request(self.report(), headers=headers)[0], 403)
        self.assertEqual(self.request({}, path="/show-window", signed=False)[0], 401)

    def test_invalid_json_schema_and_oversized_body_are_rejected(self):
        for payload in ([], None, {**self.report(), "channelIds": None}, {**self.report(), "focused": "true"},
                        {**self.report(), "completedCommandIds": ["a" * 32] * 129},
                        {**self.report(), "profileEmail": 123}):
            self.assertEqual(self.request(payload)[0], 400)
        self.assertEqual(self.request({"value": "a" * extension.MAX_BODY})[0], 413)
        self.assertEqual(extension.CHROME_TABS.clients, {})

    def test_acknowledged_ids_are_returned(self):
        report = self.report()
        report["completedCommandIds"] = ["b" * 32]
        status, _, data, _ = self.request(report)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["acknowledgedCommandIds"], ["b" * 32])

    def test_socket_timeout_and_worker_limit(self):
        client = socket.create_connection(self.server.server_address, timeout=2)
        client.sendall(b"POST /challenge HTTP/1.1\r\n")
        client.settimeout(5)
        self.assertEqual(client.recv(1), b"")
        client.close()
        for _ in range(8):
            self.assertTrue(self.server.slots.acquire(timeout=1))
        try:
            client = socket.create_connection(self.server.server_address, timeout=2)
            client.settimeout(2)
            try:
                self.assertEqual(client.recv(1), b"")
            except ConnectionResetError:
                pass
            client.close()
        finally:
            for _ in range(8):
                self.server.slots.release()

    def test_slow_drip_cannot_hold_a_worker_indefinitely(self):
        client = socket.create_connection(self.server.server_address, timeout=2)
        client.sendall(b"POST /challenge HTTP/1.1\r\nX-Slow: ")
        deadline = time.monotonic() + 7
        closed = False
        try:
            while time.monotonic() < deadline:
                try:
                    client.sendall(b"x")
                    client.settimeout(1)
                    if client.recv(1) == b"":
                        closed = True
                        break
                except socket.timeout:
                    continue
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    closed = True
                    break
            self.assertTrue(closed)
        finally:
            client.close()


@unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
class PairingKeyTests(unittest.TestCase):
    def test_key_is_protected_and_stable_in_temporary_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test-pairing.key"
            with patch.object(extension, "PAIRING_PATH", path):
                first = extension.get_pairing_secret()
                self.assertRegex(first, r"^[0-9a-f]{64}$")
                self.assertEqual(extension.get_pairing_secret(), first)
                self.assertNotIn(first.encode(), path.read_bytes())

    def test_corrupt_key_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test-pairing.key"
            path.write_bytes(b"synthetic-corruption")
            with patch.object(extension, "PAIRING_PATH", path), self.assertRaises(OSError):
                extension.get_pairing_secret()
            self.assertEqual(path.read_bytes(), b"synthetic-corruption")
