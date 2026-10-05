import gc
import tempfile
import threading
import time
import urllib.error
from pathlib import Path
from queue import SimpleQueue
import unittest
from unittest.mock import patch

import test_monitor_ui
import test_updater
from autochzzk_core.config import APP_VERSION
from autochzzk_core.updater import UpdateIntegrityError, download_update


class UpdateButtonUITests(unittest.TestCase):
    def setUp(self):
        # Finish older Tk test windows' finalizers on the main thread.
        gc.collect()
        fixture = test_monitor_ui.MonitorControlsUITests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.app = fixture.root, fixture.app
        self.app.ui_queue = SimpleQueue()
        self.app.update_download_in_progress = False
        self.app.update_prompted_version = None
        self.app.available_update_info = None
        self.app.update_dialog_pending = False
        self.app.on_close = self.app.stop_event.set
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.destination = Path(temporary.name)
        self.content = b'synthetic verified installer'
        self.release = test_updater.release_for('9.9.9', self.content)
        self.installer = self.destination / 'AutoChzzk-Setup-9.9.9.exe'
        self.installer.write_bytes(self.content)
        self.download_gate = threading.Event()
        self.download_gate.set()
        self.download_started = threading.Event()
        self.download_finished = threading.Event()
        self.download_count = 0
        redirect = patch('autochzzk.download_update', side_effect=self.download_to_temporary)
        redirect.start()
        self.addCleanup(redirect.stop)
        network = patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network request'))
        network.start()
        self.addCleanup(network.stop)
        self.addCleanup(self.stop_downloads)

    def download_to_temporary(self, info, **kwargs):
        self.download_count += 1
        self.download_started.set()
        try:
            if not self.download_gate.wait(3):
                raise TimeoutError('Synthetic download gate timed out')
            return download_update(info, destination=self.destination, **kwargs)
        finally:
            self.download_finished.set()

    def stop_downloads(self):
        self.app.stop_event.set()
        self.download_gate.set()
        if self.download_started.is_set():
            self.download_finished.wait(3)
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)

    def check_release(self, release=None):
        with patch('autochzzk.get_latest_release', return_value=release or self.release):
            self.app._check_for_update()
        self.app._drain_ui_queue()
        self.root.update_idletasks()

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(condition(),
                        f'Update UI stalled: downloading={self.app.update_download_in_progress}, '
                        f'pending={self.app.update_dialog_pending}, queue={self.app.ui_queue.qsize()}, '
                        f'stopped={self.app.stop_event.is_set()}, button={self.app.update_button.cget("state")}')

    def dismiss_dialog(self):
        dialog = self.app.active_dialog
        button = next(widget for widget in self.descendants(dialog)
                      if widget.winfo_class() == 'TButton' and widget.cget('text') == '나중에')
        button.invoke()
        self.root.update_idletasks()

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def test_detected_release_waits_for_click(self):
        self.download_gate.clear()
        with patch('autochzzk.get_latest_release', return_value=self.release):
            self.app._check_for_update()
        self.assertNotIn('9.9.9', self.app.version_summary_value.get())
        self.app._drain_ui_queue()
        self.root.update_idletasks()
        self.assertFalse(self.app.update_download_in_progress)
        self.assertIsNone(self.app.active_dialog)
        self.assertTrue(self.app.update_button.winfo_ismapped())
        self.assertEqual(str(self.app.update_button.cget('state')), 'normal')
        self.assertEqual(self.app.available_update_info.version, '9.9.9')

    def test_only_valid_newer_installer_shows_button(self):
        self.check_release()
        for version in (APP_VERSION, '1.0.0'):
            with self.subTest(version=version):
                self.check_release(test_updater.release_for(version, self.content))
                self.assertFalse(self.app.update_button.winfo_ismapped())
        invalid = test_updater.release_for('9.9.9', self.content)
        invalid['assets'][0]['digest'] = None
        self.check_release(invalid)
        self.assertFalse(self.app.update_button.winfo_ismapped())

    def test_button_fits_between_version_and_quit_at_minimum_width(self):
        self.check_release()
        version = self.app.version_summary_button
        update = self.app.update_button
        quit_button = self.app.quit_button
        self.assertLessEqual(version.winfo_rootx() + version.winfo_width(), update.winfo_rootx())
        self.assertLessEqual(update.winfo_rootx() + update.winfo_width(), quit_button.winfo_rootx())
        self.assertLessEqual(quit_button.winfo_rootx() + quit_button.winfo_width(),
                             self.root.winfo_rootx() + self.root.winfo_width())
        self.assertLessEqual(self.app.current_profile_label.winfo_rootx() +
                             self.app.current_profile_label.winfo_width(), version.winfo_rootx())

    def test_later_can_reopen_verified_installer_without_network(self):
        self.check_release()
        self.app.update_button.invoke()
        self.wait_for(lambda: self.app.active_dialog is not None)
        self.dismiss_dialog()
        self.assertTrue(self.app.update_button.winfo_ismapped())
        self.app.update_button.invoke()
        self.wait_for(lambda: self.app.active_dialog is not None)
        self.assertEqual(self.installer.read_bytes(), self.content)
        confirm = next(widget for widget in self.descendants(self.app.active_dialog)
                       if widget.winfo_class() == 'TButton' and widget.cget('text') == '업데이트')
        with patch('os.startfile'):
            confirm.invoke()
        self.assertTrue(self.app.stop_event.is_set())

    def test_downloading_disables_button_and_repeated_requests(self):
        self.check_release()
        self.download_gate.clear()
        self.app.update_button.invoke()
        self.assertTrue(self.download_started.wait(3))
        self.assertEqual(str(self.app.update_button.cget('state')), 'disabled')
        self.app.update_button.invoke()
        self.app._request_update()
        self.assertEqual(self.download_count, 1)
        self.download_gate.set()
        self.wait_for(lambda: self.app.active_dialog is not None)
        self.assertEqual(str(self.app.update_button.cget('state')), 'normal')

    def test_download_failure_allows_footer_retry(self):
        self.check_release()
        with patch('autochzzk.download_update', side_effect=UpdateIntegrityError('Synthetic failure')):
            self.app.update_button.invoke()
            self.wait_for(lambda: self.app.active_dialog is not None)
        self.dismiss_dialog()
        self.assertEqual(str(self.app.update_button.cget('state')), 'normal')
        self.app.update_button.invoke()
        self.wait_for(lambda: self.app.active_dialog is not None)
        self.assertIn('9.9.9', self.app.status_value.get())
        self.assertEqual(self.installer.read_bytes(), self.content)

    def test_pending_dialog_blocks_duplicate_download_until_offer_is_shown(self):
        self.check_release()
        self.download_gate.clear()
        self.app.update_button.invoke()
        self.assertTrue(self.download_started.wait(3))
        self.app._show_app_dialog('Synthetic dialog', 'Synthetic message', cancel_text='나중에')
        original = self.app.active_dialog
        self.download_gate.set()
        self.wait_for(lambda: not self.app.update_download_in_progress)
        self.assertIs(self.app.active_dialog, original)
        self.assertEqual(str(self.app.update_button.cget('state')), 'disabled')
        self.app._request_update()
        self.assertEqual(self.download_count, 1)
        self.dismiss_dialog()
        self.wait_for(lambda: self.app.active_dialog is not None)
        self.dismiss_dialog()
        self.assertEqual(str(self.app.update_button.cget('state')), 'normal')

    def test_check_failure_keeps_previously_found_update_available(self):
        self.check_release()
        with patch('autochzzk.get_latest_release', side_effect=urllib.error.URLError('Synthetic failure')):
            self.app._check_for_update()
        self.app._drain_ui_queue()
        self.root.update_idletasks()
        self.assertTrue(self.app.update_button.winfo_ismapped())
        self.assertEqual(self.app.available_update_info.version, '9.9.9')

    def test_closing_app_prevents_button_starting_download(self):
        self.check_release()
        self.app.stop_event.set()
        self.app.update_button.invoke()
        self.assertFalse(self.download_started.is_set())
        self.assertFalse(self.app.update_download_in_progress)


if __name__ == '__main__':
    unittest.main()
