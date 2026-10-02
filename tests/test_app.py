from queue import SimpleQueue
from pathlib import Path
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from autochzzk import AutoChzzkApp
from autochzzk_core.config import EXTENSION_RELOAD_GRACE_SECONDS, REQUIRED_EXTENSION_VERSION
from autochzzk_core.extension import ChromeTabState
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


class ChromeStartupTests(unittest.TestCase):
    def setUp(self):
        app = self.app = AutoChzzkApp.__new__(AutoChzzkApp)
        self.now = 100.0
        self.tabs = ChromeTabState()
        self.tabs.set_selected_profile({'gaia:synthetic-profile'})
        app.channels = [dict(id=letter * 32, name='Synthetic', enabled=True) for letter in 'abc']
        app.live_info = {channel['id']: (True, 'Synthetic live') for channel in app.channels}
        app.selected_chrome_profile = {'directory': 'Default'}
        app.stop_event = threading.Event()
        app.allow_browser_open_after = 100.0
        app.extension_connection_deadline = 90.0
        app.extension_setup_prompted = True
        app.extension_update_prompted = False
        app.extension_reload_deadline = None
        app.extension_connected = False
        app.chrome_launch_requested = False
        app.force_open_checks = set()
        app.root = Mock()
        app._set_status = Mock()
        app._set_extension_status = Mock()
        app._hide_status = Mock()
        app.show_extension_install_guide = Mock()
        app._find_chrome_path = Mock(return_value=Path('C:/SyntheticChrome/chrome.exe'))
        app._is_chrome_running = Mock(return_value=True)
        for target, replacement in [('autochzzk.CHROME_TABS', self.tabs),
                                    ('autochzzk.time.monotonic', lambda: self.now)]:
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        launcher = patch('autochzzk.subprocess.Popen')
        self.launch = launcher.start()
        self.addCleanup(launcher.stop)

    def test_startup_callbacks_launch_chrome_once_for_multiple_live_channels(self):
        app = self.app
        app.allow_browser_open_after = 105.0
        for channel in app.channels:
            app._open_live(channel, 'Synthetic live')
        callbacks = [call.args[1] for call in app.root.after.call_args_list]
        self.assertEqual(len(callbacks), 3)
        self.launch.assert_not_called()

        self.now = 105.1
        for callback in callbacks:
            callback()

        self.assertEqual(self.launch.call_count, 1)
        self.assertTrue(app.chrome_launch_requested)
        self.assertEqual(app.extension_connection_deadline, 120.1)
        self.assertEqual(self.tabs.pending_commands('synthetic-client'), [])

    def test_first_report_skips_restored_broadcasts_and_opens_only_missing_tabs(self):
        app = self.app
        for channel in app.channels:
            app._open_live(channel, 'Synthetic live')
        self.now = 103.0
        self.tabs.update('synthetic-client', {app.channels[0]['id']},
                         {'gaia:synthetic-profile'}, True, REQUIRED_EXTENSION_VERSION)

        app._check_extension_connection()
        for channel in app.channels:
            app._open_live(channel, 'Synthetic live')

        self.assertEqual(self.launch.call_count, 1)
        self.assertFalse(app.chrome_launch_requested)
        self.assertEqual(app.force_open_checks, {channel['id'] for channel in app.channels})
        commands = self.tabs.pending_commands('synthetic-client')
        self.assertEqual({command['url'] for command in commands}, {
            'https://chzzk.naver.com/live/' + 'b' * 32,
            'https://chzzk.naver.com/live/' + 'c' * 32,
        })
        self.assertTrue(all(command['action'] == 'open' for command in commands))

    def test_connection_timeout_does_not_launch_another_running_chrome_window(self):
        app = self.app
        app._open_live(app.channels[0], 'Synthetic live')
        self.now = 116.0
        app._check_extension_connection()

        app._open_live(app.channels[1], 'Synthetic live')
        app._open_live(app.channels[2], 'Synthetic live')

        self.assertEqual(self.launch.call_count, 1)
        self.assertTrue(app.chrome_launch_requested)
        app.show_extension_install_guide.assert_called_once()

    def test_closed_chrome_can_be_launched_again_after_connection_timeout(self):
        app = self.app
        app._open_live(app.channels[0], 'Synthetic live')
        self.now = 116.0
        app._is_chrome_running.return_value = False

        app._open_live(app.channels[1], 'Synthetic live')

        self.assertEqual(self.launch.call_count, 2)
        self.assertTrue(app.chrome_launch_requested)

    def test_profile_switch_allows_launching_the_new_profile(self):
        app = self.app
        app._open_live(app.channels[0], 'Synthetic live')
        app.selected_chrome_profile = {'directory': 'Profile 2'}

        app._reset_extension_connection_check()
        app._open_live(app.channels[1], 'Synthetic live')

        self.assertEqual(self.launch.call_count, 2)
        self.assertEqual(self.launch.call_args.args[0][1], '--profile-directory=Profile 2')


class ChannelToggleUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk display unavailable: {error}')
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        app = self.app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.root = self.root
        app._configure_styles()
        app.channels = [
            dict(id=f'{index:032x}', name=f'Synthetic {index}', enabled=index != 1,
                 interval=60, extra='preserved')
            for index in range(12)
        ]
        self.channel_id = app.channels[9]['id']
        app.live_info = {self.channel_id: (True, 'Synthetic live title')}
        app.was_live = {self.channel_id: True}
        app.last_checked = {self.channel_id: 1}
        app.force_open_checks = {self.channel_id}
        app.retry_open_checks = {self.channel_id}
        app.channel_generations = {}
        app.editing_channel_id = None
        app._save_channels = Mock(return_value=True)
        app.count_label = tk.Label(self.root)
        app.canvas = tk.Canvas(self.root, highlightthickness=0)
        app.canvas.place(x=0, y=0, width=600, height=230)
        app.list_frame = tk.Frame(app.canvas)
        app.canvas.create_window((0, 0), window=app.list_frame, anchor='nw', width=600)
        app.list_frame.bind('<Configure>', lambda event: app.canvas.configure(
            scrollregion=app.canvas.bbox('all')))
        app._refresh_list()
        self.root.update_idletasks()
        app.canvas.yview_moveto(0.55)
        self.button = app.detection_buttons[self.channel_id]

    def test_toggle_preserves_rows_and_scroll_while_updating_detection(self):
        app = self.app
        rows = dict(app.channel_rows)
        live_status = app.live_status_widgets[self.channel_id]
        scroll = app.canvas.yview()
        self.assertGreater(scroll[0], 0)
        for enabled, count in [(False, 10), (True, 11)]:
            self.button.invoke()
            self.root.update_idletasks()
            self.assertEqual(app.channel_rows, rows)
            self.assertEqual(app.canvas.yview(), scroll)
            self.assertEqual(self.button.get_value(), enabled)
            self.assertIn(f'감지 {count}개', app.count_label.cget('text'))
            self.assertEqual(app.channels[9]['enabled'], enabled)
            self.assertEqual(app.channels[9]['extra'], 'preserved')
            self.assertIs(app.live_status_widgets[self.channel_id], live_status)
        self.assertEqual(app.channel_generations[self.channel_id], 2)
        self.assertNotIn(self.channel_id, app.was_live)
        self.assertNotIn(self.channel_id, app.last_checked)
        self.assertNotIn(self.channel_id, app.force_open_checks)
        self.assertNotIn(self.channel_id, app.retry_open_checks)

    def test_toggle_keeps_unsaved_interval_entry(self):
        app = self.app
        app.show_interval_editor(self.channel_id)
        editor = app.interval_editors[self.channel_id]
        entry = next(widget for widget in editor.winfo_children() if isinstance(widget, tk.Entry))
        entry.delete(0, 'end')
        entry.insert(0, '125')

        self.button.invoke()
        self.root.update_idletasks()

        self.assertIs(app.interval_editors[self.channel_id], editor)
        self.assertEqual(entry.get(), '125')
        self.assertEqual(app.editing_channel_id, self.channel_id)

    def test_failed_save_keeps_detection_and_display_unchanged(self):
        app = self.app
        app._save_channels.return_value = False
        channels = app.channels
        rows = dict(app.channel_rows)
        scroll = app.canvas.yview()

        self.button.invoke()
        self.root.update_idletasks()

        self.assertIs(app.channels, channels)
        self.assertEqual(app.channel_rows, rows)
        self.assertEqual(app.canvas.yview(), scroll)
        self.assertTrue(self.button.get_value())
        self.assertIn('감지 11개', app.count_label.cget('text'))
        self.assertEqual(app.channel_generations, {})


class ListLayoutUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk display unavailable: {error}')
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        app = self.app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.root = self.root
        app.channels = []
        app.editing_channel_id = None
        app.live_info = {}
        app.active_dialog = None
        app.changelog_dialog = None
        app.selected_chrome_profile = dict(directory='Default', name='Synthetic profile', gaia_id='', email='')
        app.chrome_profiles = [app.selected_chrome_profile, dict(directory='Profile 1', name='Other profile', gaia_id='', email='')]
        app.profile_labels = {profile['name']: profile for profile in app.chrome_profiles}
        app.profile_value = tk.StringVar(value='Synthetic profile')
        app.input_value = tk.StringVar()
        app.status_value = tk.StringVar()
        app.extension_status_value = tk.StringVar(value='Chrome 확장 프로그램 연결 확인 중…')
        app.version_value = tk.StringVar(value='현재 버전 · 최신 버전 확인 중…')
        app.on_close = Mock()
        app._configure_styles()
        app._build_ui()
        app._refresh_list()
        self.root.update_idletasks()

    def test_add_dialog_preserves_unsubmitted_input_when_reopened(self):
        app = self.app
        app.show_add_channel_dialog()
        app.input_value.set('a' * 32)
        app._close_add_channel_dialog()
        app.show_add_channel_dialog()
        self.assertEqual(app.input_value.get(), 'a' * 32)
        self.assertTrue(app.add_dialog.winfo_exists())

    def test_connection_problem_remains_visible_outside_settings(self):
        app = self.app
        app._set_extension_status('Chrome 확장 프로그램 연결 안 됨', False)
        self.assertEqual(app.extension_notice.winfo_manager(), 'pack')
        app._set_extension_status('Chrome 확장 프로그램 연결됨', True)
        self.assertEqual(app.extension_notice.winfo_manager(), '')

    def test_auxiliary_dialog_returns_focus_and_grab_to_settings(self):
        app = self.app
        self.root.deiconify()
        self.root.update()
        app.show_settings()
        self.root.update()
        app.show_extension_install_guide()
        self.assertIs(self.root.grab_current(), app.active_dialog)
        buttons = app.active_dialog.winfo_children()[0].winfo_children()[-1].winfo_children()
        next(button for button in buttons if button.cget('text') == '확인했습니다').invoke()
        self.root.update()
        self.assertIsNone(app.active_dialog)
        self.assertIs(self.root.grab_current(), app.settings_dialog)

    def test_changelog_returns_grab_to_settings_after_closing(self):
        app = self.app
        self.root.deiconify()
        self.root.update()
        app.show_settings()
        self.root.update()
        app.show_changelog()
        app.changelog_dialog.winfo_children()[0].winfo_children()[-1].invoke()
        self.assertIsNone(app.changelog_dialog)
        self.assertIs(self.root.grab_current(), app.settings_dialog)

    def test_main_exit_button_calls_quit_and_settings_has_no_exit_button(self):
        app = self.app
        app.quit_button.invoke()
        app.on_close.assert_called_once_with()
        settings_buttons = [widget.cget('text') for widget in self.descendants(app.settings_dialog)
                            if widget.winfo_class() in ('Button', 'TButton')]
        self.assertNotIn('앱 종료', settings_buttons)

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def test_channel_menu_can_open_interval_editor_after_posting(self):
        app = self.app
        app.channels = [dict(id='a' * 32, name='Synthetic', enabled=True, interval=60)]
        app._refresh_list()
        self.root.geometry('620x650+30000+30000')
        self.root.deiconify()
        self.root.update()
        app.show_channel_menu('a' * 32)
        menu = app.channel_menu
        self.assertIsInstance(menu, tk.Toplevel)
        edit_button = next(widget for widget in self.descendants(menu)
                           if isinstance(widget, tk.Button) and widget.cget('text') == '확인 간격 수정')
        edit_button.invoke()
        self.assertFalse(menu.winfo_exists())
        self.assertIsNone(self.root.grab_current())
        self.assertIn('a' * 32, app.interval_editors)

    def test_channel_menu_delete_action_closes_menu_before_confirmation(self):
        app = self.app
        app.channels = [dict(id='a' * 32, name='Synthetic', enabled=True, interval=15)]
        app._refresh_list()
        app.confirm_remove_channel = Mock()
        self.root.geometry('620x650+30000+30000')
        self.root.deiconify()
        self.root.update()
        app.show_channel_menu('a' * 32)
        menu = app.channel_menu
        delete_button = next(widget for widget in self.descendants(menu)
                             if isinstance(widget, tk.Button) and widget.cget('text') == '채널 삭제')
        delete_button.invoke()
        self.assertFalse(menu.winfo_exists())
        self.assertIsNone(self.root.grab_current())
        app.confirm_remove_channel.assert_called_once_with('a' * 32, 'Synthetic')
