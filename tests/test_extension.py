from __future__ import annotations

import unittest

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
        command_id = self.tabs.queue_background_close("https://chzzk.naver.com/live/channel")

        self.assertTrue(command_id)
        self.assertEqual(
            self.tabs.pending_commands("selected-client"),
            [{"id": command_id, "action": "close", "url": "https://chzzk.naver.com/live/channel"}],
        )

        self.tabs.acknowledge_commands("selected-client", [command_id])
        self.assertEqual(self.tabs.pending_commands("selected-client"), [])

    def test_close_command_requires_a_connected_selected_profile(self) -> None:
        disconnected = ChromeTabState()
        disconnected.set_selected_profile({"email:selected@example.com"})

        self.assertEqual(disconnected.queue_background_close("https://chzzk.naver.com/live/channel"), "")

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


if __name__ == "__main__":
    unittest.main()
