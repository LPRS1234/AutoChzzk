from __future__ import annotations

import unittest
from unittest.mock import patch

from autochzzk_core.extension import ChromeTabState


class ChromeTabStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tabs = ChromeTabState()
        self.tabs.set_selected_profile({"email:selected@example.com"})
        self.tabs.update(
            "selected-client",
            set(),
            {"email:selected@example.com"},
            focused=True,
        )

    def test_close_command_is_sent_to_selected_profile(self) -> None:
        command_id = self.tabs.queue_background_close("https://chzzk.naver.com/live/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

        self.assertTrue(command_id)
        self.assertEqual(
            self.tabs.pending_commands("selected-client"),
            [{"id": command_id, "action": "close", "url": "https://chzzk.naver.com/live/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],
        )

        self.tabs.acknowledge_commands("selected-client", [command_id])
        self.assertEqual(self.tabs.pending_commands("selected-client"), [])

    def test_close_command_requires_a_connected_selected_profile(self) -> None:
        disconnected = ChromeTabState()
        disconnected.set_selected_profile({"email:selected@example.com"})

        self.assertEqual(disconnected.queue_background_close("https://chzzk.naver.com/live/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), "")

    def test_old_or_unreported_extension_version_requires_update(self) -> None:
        self.assertTrue(self.tabs.selected_extension_needs_update("1.0.6"))

        self.tabs.update(
            "selected-client",
            set(),
            {"email:selected@example.com"},
            focused=True,
            extension_version="1.0.5",
        )
        self.assertTrue(self.tabs.selected_extension_needs_update("1.0.6"))

    def test_current_extension_version_does_not_require_update(self) -> None:
        self.tabs.update(
            "selected-client",
            set(),
            {"email:selected@example.com"},
            focused=True,
            extension_version="1.0.6",
        )

        self.assertFalse(self.tabs.selected_extension_needs_update("1.0.6"))

    def test_commands_expire_before_reconnect(self) -> None:
        with patch("autochzzk_core.extension.time.monotonic", return_value=100):
            self.tabs.update("selected-client", set(), {"email:selected@example.com"}, True)
            opened = self.tabs.queue_background_open("https://chzzk.naver.com/live/" + "a" * 32)
            self.tabs.queue_background_close("https://chzzk.naver.com/live/" + "b" * 32)
        with patch("autochzzk_core.extension.time.monotonic", return_value=131):
            self.assertFalse(self.tabs.is_pending(opened))
            self.assertEqual(self.tabs.pending_commands("selected-client"), [])

    def test_rejects_non_broadcast_urls(self) -> None:
        for url in ("https://example.com", "javascript:alert(1)", "https://chzzk.naver.com/live/" + "a" * 32 + "?redirect=x"):
            self.assertEqual(self.tabs.queue_background_open(url), "")
            self.assertEqual(self.tabs.queue_background_close(url), "")

    def test_new_state_cancels_opposite_pending_command(self) -> None:
        url = "https://chzzk.naver.com/live/" + "a" * 32
        self.tabs.queue_background_close(url)
        opened = self.tabs.queue_background_open(url)
        self.assertEqual(self.tabs.pending_commands("selected-client"), [{"id": opened, "action": "open", "url": url}])
        closed = self.tabs.queue_background_close(url)
        self.assertEqual(self.tabs.pending_commands("selected-client"), [{"id": closed, "action": "close", "url": url}])

    def test_profile_change_cancels_commands_for_previous_profile(self) -> None:
        self.tabs.queue_background_open("https://chzzk.naver.com/live/" + "a" * 32)
        self.tabs.queue_background_close("https://chzzk.naver.com/live/" + "b" * 32)
        self.tabs.set_selected_profile({"email:other@example.invalid"})
        self.assertEqual(self.tabs.pending_commands("selected-client"), [])

    def test_outdated_extension_is_asked_to_reload_only_once(self) -> None:
        self.tabs.update(
            "selected-client", set(), {"email:selected@example.com"}, True, extension_version="2.0.0"
        )

        self.assertTrue(self.tabs.queue_extension_reload("2.1.0"))
        commands = self.tabs.pending_commands("selected-client")
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["action"], "reload")
        self.assertEqual(commands[0]["version"], "2.1.0")
        self.assertFalse(self.tabs.queue_extension_reload("2.1.0"))

        self.tabs.acknowledge_commands("selected-client", [commands[0]["id"]])
        self.assertEqual(self.tabs.pending_commands("selected-client"), [])
        self.assertFalse(self.tabs.queue_extension_reload("2.1.0"))

    def test_current_extension_is_not_asked_to_reload(self) -> None:
        self.tabs.update(
            "selected-client", set(), {"email:selected@example.com"}, True, extension_version="2.1.0"
        )
        self.assertFalse(self.tabs.queue_extension_reload("2.1.0"))

    def test_command_queue_is_bounded_and_acknowledgement_is_client_specific(self) -> None:
        url = "https://chzzk.naver.com/live/" + "a" * 32
        ids = [self.tabs.queue_background_close(url) for _ in range(128)]
        self.assertTrue(all(ids))
        self.assertEqual(self.tabs.queue_background_close(url), "")
        self.tabs.acknowledge_commands("another-client", ids)
        self.assertEqual(len(self.tabs.pending_commands("selected-client")), 128)
        self.tabs.acknowledge_commands("selected-client", ids)
        self.assertEqual(self.tabs.pending_commands("selected-client"), [])


if __name__ == "__main__":
    unittest.main()
