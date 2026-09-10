from queue import SimpleQueue
import threading
import unittest
from unittest.mock import Mock, patch

from autochzzk import AutoChzzkApp
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
