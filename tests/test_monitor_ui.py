import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from autochzzk import AutoChzzkApp


class MonitorControlsUITests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('540x540+30000+30000')
        self.addCleanup(self.root.destroy)
        app = self.app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.root = self.root
        app.channels = [dict(id=letter * 32, name='Synthetic', enabled=letter == 'a', interval=60)
                        for letter in 'ab']
        app.stop_event = threading.Event()
        app.pause_until = 0
        app.manual_checks = set()
        app.refresh_batch = set()
        app.refresh_failed = set()
        app.last_successful_check = {}
        app.check_errors = set()
        app.lookup_pool = Mock(pending={})
        app.force_open_checks = set()
        app.live_info = {}
        app.editing_channel_id = None
        app.active_dialog = app.changelog_dialog = None
        app.selected_chrome_profile = dict(directory='Default', name='Synthetic', gaia_id='', email='')
        app.chrome_profiles = [app.selected_chrome_profile]
        app.profile_labels = {'Synthetic': app.selected_chrome_profile}
        app.profile_value = tk.StringVar(value='Synthetic')
        app.input_value = tk.StringVar()
        app.status_value = tk.StringVar()
        app.status_clear_token = 0
        app.extension_status_value = tk.StringVar(value='확인 중')
        app.version_value = tk.StringVar(value='2.2.0')
        app._configure_styles()
        app._build_ui()
        app._refresh_list()
        self.root.update()

    def test_refresh_button_queues_enabled_and_disabled_channels(self):
        self.app.refresh_all_button.invoke()
        self.assertEqual(self.app.manual_checks, {'a' * 32, 'b' * 32})
        self.assertFalse(self.app.channels[1]['enabled'])
        self.assertEqual(str(self.app.refresh_all_button.cget('state')), 'disabled')

    def test_pause_and_resume_buttons_preserve_channel_flags(self):
        app = self.app
        with patch('autochzzk.time.monotonic', return_value=100):
            app.pause_30_button.invoke()
            self.assertEqual(app.pause_until, 1900)
            self.root.update_idletasks()
            self.assertTrue(app.resume_button.winfo_ismapped())
            self.assertIn('30', app.pause_value.get())
            app.resume_button.invoke()
        self.assertEqual(app.pause_until, 0)
        self.assertEqual([channel['enabled'] for channel in app.channels], [True, False])
        self.assertEqual(app.force_open_checks, {'a' * 32})

    def test_error_status_remains_in_channel_row_after_footer_clears(self):
        app = self.app
        channel_id = 'a' * 32
        row = app.channel_rows[channel_id]
        app.last_successful_check[channel_id] = 10
        app.check_errors.add(channel_id)
        with patch('autochzzk.time.monotonic', return_value=130):
            app._update_check_status_widget(channel_id)
            app._hide_status()
        label = app.check_status_widgets[channel_id]
        self.assertIn('확인 실패', label.cget('text'))
        self.assertIn('2분', label.cget('text'))
        self.assertIs(app.channel_rows[channel_id], row)

    def test_header_actions_fit_minimum_window_width(self):
        for button in [self.app.refresh_all_button, self.app.pause_30_button,
                       self.app.pause_60_button]:
            self.assertTrue(button.winfo_ismapped())
            self.assertGreaterEqual(button.winfo_rootx(), self.root.winfo_rootx())
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                 self.root.winfo_rootx() + self.root.winfo_width())


if __name__ == '__main__':
    unittest.main()
