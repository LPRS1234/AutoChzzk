from queue import SimpleQueue
import threading
import unittest
from unittest.mock import Mock, patch

from autochzzk import AutoChzzkApp
from autochzzk_core.config import EXTENSION_RELOAD_GRACE_SECONDS, REQUIRED_EXTENSION_VERSION
from autochzzk_core.monitor import LookupPool


class AppIntegrationTests(unittest.TestCase):
    def test_quit_callback_does_not_schedule_on_destroyed_window(self):
        app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.root = Mock()
        app.stop_event = threading.Event()
        app.ui_queue = SimpleQueue()
        app._ui(app.stop_event.set)
        app._drain_ui_queue()
        app.root.after.assert_not_called()
        app._ui(Mock())
        self.assertTrue(app.ui_queue.empty())

    def test_channel_name_lookup_does_not_block_ui_or_save_from_worker(self):
        app = AutoChzzkApp.__new__(AutoChzzkApp)
        channel_id = 'a' * 32
        app.channels = []
        app.input_value = Mock()
        app.input_value.get.return_value = channel_id
        app.channels_read_only = False
        app.pending_additions = set()
        app.initial_checks = set()
        app.lookup_pool = LookupPool(1)
        app._set_status = Mock()
        app._refresh_list = Mock()
        app._save_channels = Mock(return_value=True)
        entered, release = threading.Event(), threading.Event()
        def name_lookup(value):
            entered.set()
            release.wait(2)
            return 'Synthetic channel'
        with patch('autochzzk.get_channel_name', side_effect=name_lookup):
            try:
                app.add_channel()
                self.assertTrue(entered.wait(1))
                self.assertEqual(app.channels, [])
                app._save_channels.assert_not_called()
                app.add_channel()
                self.assertEqual(len(app.lookup_pool.pending), 1)
            finally:
                release.set()
            result = app.lookup_pool.results.get(timeout=2)
            app.lookup_pool.results.put(result)
            app.lookup_pool.drain()
        app._save_channels.assert_called_once()
        self.assertEqual(app.channels[0]['id'], channel_id)
        self.assertIn(channel_id, app.initial_checks)

    def test_clipboard_cleanup_preserves_later_user_copy(self):
        app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.root = Mock()
        app._set_status = Mock()
        with patch('autochzzk.get_pairing_secret', return_value='synthetic-pairing-code'):
            app._copy_extension_pairing_code()
        cleanup = app.root.after.call_args.args[1]
        app.root.clipboard_clear.reset_mock()
        app.root.clipboard_get.return_value = 'user copied another value'
        cleanup()
        app.root.clipboard_clear.assert_not_called()

    def test_account_identity_change_rechecks_extension(self):
        app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.stop_event = threading.Event()
        old = dict(directory='Default', name='Same', gaia_id='old', email='')
        new = dict(old, gaia_id='new')
        app.selected_chrome_profile = old
        app.chrome_profiles = [old]
        for name in ['root', 'profile_value', 'current_profile_label', 'profile_selector',
                     'profile_change_button', 'profile_editor', '_apply_selected_profile',
                     '_reset_extension_connection_check', '_set_extension_status']:
            setattr(app, name, Mock())
        with patch('autochzzk.get_chrome_profiles', return_value=[new]):
            app._check_selected_profile_exists()
        self.assertEqual(app.selected_chrome_profile['gaia_id'], 'new')
        app._apply_selected_profile.assert_called_once()
        app._reset_extension_connection_check.assert_called_once()

    def test_outdated_extension_gets_automatic_reload_grace_period(self):
        app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.extension_reload_deadline = None
        app.extension_update_prompted = False
        app._set_extension_status = Mock()
        app._prompt_extension_reinstall = Mock()
        with (patch('autochzzk.CHROME_TABS.selected_extension_needs_update', return_value=True),
              patch('autochzzk.CHROME_TABS.queue_extension_reload', return_value=True) as queue_reload,
              patch('autochzzk.time.monotonic', return_value=100)):
            app._set_connected_extension_status()

        queue_reload.assert_called_once_with(REQUIRED_EXTENSION_VERSION)
        self.assertEqual(app.extension_reload_deadline, 100 + EXTENSION_RELOAD_GRACE_SECONDS)
        app._set_extension_status.assert_called_once_with('Chrome 확장 프로그램 자동 업데이트 적용 중…')
        app._prompt_extension_reinstall.assert_not_called()

    def test_failed_automatic_reload_falls_back_to_manual_guide(self):
        app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.extension_reload_deadline = 100
        app.extension_update_prompted = False
        app._set_extension_status = Mock()
        app._prompt_extension_reinstall = Mock()
        with (patch('autochzzk.CHROME_TABS.selected_extension_needs_update', return_value=True),
              patch('autochzzk.time.monotonic', return_value=101)):
            app._set_connected_extension_status()

        app._set_extension_status.assert_called_once_with('Chrome 확장 프로그램 업데이트 필요', False)
        app._prompt_extension_reinstall.assert_called_once()

    def test_connection_reset_allows_reload_for_a_new_profile(self):
        app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.extension_setup_prompted = True
        app.extension_update_prompted = True
        app.extension_reload_deadline = 1
        app.extension_connected = True

        app._reset_extension_connection_check()

        self.assertIsNone(app.extension_reload_deadline)
        self.assertFalse(app.extension_update_prompted)

    def test_delayed_manual_prompt_rechecks_the_installed_version(self):
        app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.extension_reload_deadline = 1
        app.extension_update_prompted = False
        app.active_dialog = Mock()
        app.active_dialog.winfo_exists.return_value = True
        app.root = Mock()
        app.show_extension_reinstall_guide = Mock()
        with (patch('autochzzk.CHROME_TABS.is_connected', return_value=True),
              patch('autochzzk.CHROME_TABS.selected_extension_needs_update', return_value=True),
              patch('autochzzk.time.monotonic', return_value=2)):
            app._prompt_extension_reinstall()
        delayed_prompt = app.root.after.call_args.args[1]

        app.extension_reload_deadline = None
        with (patch('autochzzk.CHROME_TABS.is_connected', return_value=True),
              patch('autochzzk.CHROME_TABS.selected_extension_needs_update', return_value=True)):
            delayed_prompt()

        app.show_extension_reinstall_guide.assert_not_called()

    def test_manual_recheck_keeps_an_active_reload_grace_period(self):
        app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.extension_reload_deadline = 123
        app.extension_update_prompted = True
        app._set_extension_status = Mock()
        app.root = Mock()

        app.recheck_extension_status()

        self.assertEqual(app.extension_reload_deadline, 123)
