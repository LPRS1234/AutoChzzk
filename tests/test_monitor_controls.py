import threading
import unittest
from unittest.mock import Mock, patch

from autochzzk import AutoChzzkApp
from autochzzk_core.monitor import LookupPool
from autochzzk_core.extension import ChromeTabState


class MonitorControlTests(unittest.TestCase):
    def setUp(self):
        self.app = AutoChzzkApp.__new__(AutoChzzkApp)
        self.app.channels = [
            {"id": "a", "name": "Synthetic A", "enabled": True, "interval": 60},
            {"id": "b", "name": "Synthetic B", "enabled": False, "interval": 60},
        ]
        self.app.root = Mock()
        self.app.stop_event = threading.Event()
        self.app.lookup_pool = LookupPool(2)
        self.addCleanup(self.app.lookup_pool.close)
        self.app.last_checked = {"a": 1000, "b": 1000}
        self.app.last_successful_check = {}
        self.app.check_errors = set()
        self.app.manual_checks = set()
        self.app.refresh_batch = set()
        self.app.refresh_failed = set()
        self.app.pause_until = 0
        self.app.initial_checks = set()
        self.app.force_open_checks = set()
        self.app.retry_open_checks = set()
        self.app.channel_generations = {}
        self.app.was_live = {}
        self.app.live_info = {}
        self.app.live_status_widgets = {}
        self.app.detection_buttons = {}
        self.app.allow_browser_open_after = 0
        self.app.chrome_launch_requested = False
        self.app.editing_channel_id = None
        for method in (
            "_update_check_status_widget", "_update_refresh_controls",
            "_update_pause_controls", "_set_status", "_update_channel_count",
            "_refresh_list",
        ):
            setattr(self.app, method, Mock())
        self.app._save_channels = Mock(return_value=True)
        self.app._open_live = Mock()
        self.app._close_finished_live = Mock()
        self.clock = patch("autochzzk.time.monotonic", return_value=1000).start()
        self.addCleanup(patch.stopall)

    def require_control(self, name):
        self.assertTrue(callable(getattr(AutoChzzkApp, name, None)), f"Missing monitor control: {name}")

    def complete_results(self, count=1):
        results = [self.app.lookup_pool.results.get(timeout=2) for _ in range(count)]
        for result in results:
            self.app.lookup_pool.results.put(result)
        self.app.lookup_pool.drain()

    def test_success_records_freshness_and_clears_a_previous_failure(self):
        self.app.check_errors.add("a")
        self.app.initial_checks.add("a")
        with patch("autochzzk.get_live_status", return_value=(True, "Synthetic broadcast")):
            self.app._monitor()
            self.complete_results()
        self.assertEqual(self.app.last_successful_check.get("a"), 1000)
        self.assertNotIn("a", self.app.check_errors)
        self.assertEqual(self.app.live_info["a"], (True, "Synthetic broadcast"))
        self.app._open_live.assert_called_once_with(self.app.channels[0], "Synthetic broadcast")

    def test_error_preserves_previous_success_and_persistent_failure_age_until_recovery(self):
        self.require_control("_check_status_display")
        self.app.last_successful_check["a"] = 900
        self.app.live_info["a"] = (True, "Previous title")
        self.app.was_live["a"] = True
        self.app.initial_checks.add("a")
        with patch("autochzzk.get_live_status", side_effect=OSError("Synthetic unavailable API")):
            self.app._monitor()
            self.complete_results()
        self.assertEqual(self.app.last_successful_check["a"], 900)
        self.assertEqual(self.app.live_info["a"], (True, "Previous title"))
        self.assertTrue(self.app.was_live["a"])
        self.assertIn("a", self.app.check_errors)
        failed_text, failed_color = self.app._check_status_display("a")
        self.assertIn("실패", failed_text)
        self.assertIn("전", failed_text)
        self.assertEqual(failed_color, self.app.DANGER)
        self.clock.return_value = 1200
        older_text, older_color = self.app._check_status_display("a")
        self.assertIn("실패", older_text)
        self.assertNotEqual(older_text, failed_text)
        self.assertEqual(older_color, self.app.DANGER)
        self.app._close_finished_live.assert_not_called()
        with patch("autochzzk.get_live_status", return_value=(False, "")):
            self.app._monitor()
            self.complete_results()
        self.assertNotIn("a", self.app.check_errors)
        self.assertEqual(self.app.last_successful_check["a"], 1200)
        self.assertNotEqual(self.app._check_status_display("a")[1], self.app.DANGER)

    def test_pause_durations_preserve_channel_switches_without_saving(self):
        self.require_control("pause_monitoring")
        self.require_control("_is_monitor_paused")
        for minutes, deadline in ((30, 2800), (60, 4600)):
            with self.subTest(minutes=minutes):
                self.app.pause_monitoring(minutes)
                self.assertEqual(self.app.pause_until, deadline)
                self.assertTrue(self.app._is_monitor_paused())
                self.assertEqual([channel["enabled"] for channel in self.app.channels], [True, False])
        self.app._save_channels.assert_not_called()

    def test_pause_blocks_initial_and_forced_automatic_lookups(self):
        self.app.pause_until = 2800
        self.app.initial_checks.update(("a", "b"))
        self.app.force_open_checks.add("a")
        with patch("autochzzk.get_live_status") as lookup:
            self.app._monitor()
            lookup.assert_not_called()
        self.assertFalse(self.app.lookup_pool.pending)
        self.app._open_live.assert_not_called()
        self.app._close_finished_live.assert_not_called()

    def test_inflight_results_during_pause_preserve_pending_live_transition(self):
        self.require_control("pause_monitoring")
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.app.initial_checks.add("a")
        self.app.was_live["a"] = True
        self.app.live_info["a"] = (True, "Previous broadcast")

        def lookup(channel_id):
            entered.set()
            release.wait(2)
            return False, ""

        with patch("autochzzk.get_live_status", side_effect=lookup):
            self.app._monitor()
            self.assertTrue(entered.wait(1))
            self.app.pause_monitoring(30)
            release.set()
            self.complete_results()
        self.assertEqual(self.app.live_info["a"], (False, ""))
        self.assertEqual(self.app.last_successful_check["a"], 1000)
        self.assertTrue(self.app.was_live["a"])
        self.app._open_live.assert_not_called()
        self.app._close_finished_live.assert_not_called()

    def test_manual_refresh_while_paused_updates_on_and_off_channels_without_tabs(self):
        self.require_control("refresh_channel")
        self.app.pause_until = 2800
        self.app.was_live = {"a": False, "b": False}
        with patch("autochzzk.get_live_status", side_effect=lambda channel_id: (True, f"Synthetic {channel_id}")):
            self.app.refresh_channel("a")
            self.app.refresh_channel("b")
            self.app._monitor()
            self.complete_results(2)
        self.assertEqual(self.app.live_info, {"a": (True, "Synthetic a"), "b": (True, "Synthetic b")})
        self.assertEqual(self.app.last_successful_check, {"a": 1000, "b": 1000})
        self.assertEqual(self.app.was_live, {"a": False, "b": False})
        self.assertEqual([channel["enabled"] for channel in self.app.channels], [True, False])
        self.assertFalse(self.app.refresh_batch)
        self.app._save_channels.assert_not_called()
        self.app._open_live.assert_not_called()
        self.app._close_finished_live.assert_not_called()

    def test_refresh_batch_coalesces_and_retries_rejected_capacity_until_completion(self):
        self.require_control("refresh_all_channels")
        self.app.lookup_pool.capacity = 1
        self.app.pause_until = 2800
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def lookup(channel_id):
            if channel_id == "a":
                entered.set()
                release.wait(2)
                return True, "Synthetic a"
            raise OSError("Synthetic b lookup failure")

        with patch("autochzzk.get_live_status", side_effect=lookup) as live_lookup:
            self.app.refresh_all_channels()
            self.app._monitor()
            self.assertTrue(entered.wait(1))
            self.app.refresh_all_channels()
            self.app._monitor()
            self.assertEqual(len(self.app.lookup_pool.pending), 1)
            self.assertIn("b", self.app.manual_checks)
            release.set()
            self.complete_results()
            self.assertIn("b", self.app.refresh_batch)
            self.app.refresh_all_channels()
            self.app._monitor()
            self.complete_results()
            self.app._monitor()
        self.assertEqual([call.args[0] for call in live_lookup.call_args_list], ["a", "b"])
        self.assertFalse(self.app.manual_checks)
        self.assertFalse(self.app.refresh_batch)
        self.assertIn("b", self.app.check_errors)
        self.assertIn("b", self.app.refresh_failed)

    def test_resume_checks_enabled_channels_immediately_and_reopens_known_live(self):
        self.require_control("resume_monitoring")
        self.app.pause_until = 2800
        self.app.was_live = {"a": True, "b": True}
        with patch("autochzzk.get_live_status", return_value=(True, "Synthetic live")) as lookup:
            self.app.resume_monitoring()
            self.app._monitor()
            self.complete_results()
        self.assertEqual(self.app.pause_until, 0)
        self.assertEqual([call.args[0] for call in lookup.call_args_list], ["a"])
        self.app._open_live.assert_called_once_with(self.app.channels[0], "Synthetic live")
        self.app._save_channels.assert_not_called()

    def test_pause_expiry_automatically_rechecks_enabled_channels_immediately(self):
        self.app.pause_until = 1000
        self.app.was_live["a"] = True
        with patch("autochzzk.get_live_status", return_value=(True, "Synthetic live")) as lookup:
            self.app._monitor()
            self.assertIn(("live", "a"), self.app.lookup_pool.pending, "Expiry must schedule an immediate live check")
            self.complete_results()
        self.assertEqual(self.app.pause_until, 0)
        self.assertEqual([call.args[0] for call in lookup.call_args_list], ["a"])
        self.app._open_live.assert_called_once()

    def test_removal_discards_queued_batch_and_ignores_inflight_result(self):
        self.require_control("refresh_all_channels")
        self.app.lookup_pool.capacity = 1
        self.app.pause_until = 2800
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def lookup(channel_id):
            entered.set()
            release.wait(2)
            return True, "Deleted synthetic broadcast"

        with patch("autochzzk.get_live_status", side_effect=lookup):
            self.app.refresh_all_channels()
            self.app._monitor()
            self.assertTrue(entered.wait(1))
            self.app.remove_channel("b")
            self.app.remove_channel("a")
            release.set()
            self.complete_results()
        self.assertFalse(self.app.channels)
        self.assertFalse(self.app.manual_checks)
        self.assertFalse(self.app.refresh_batch)
        self.assertFalse(self.app.live_info)
        self.assertFalse(self.app.last_successful_check)
        self.app._open_live.assert_not_called()

    def test_toggle_invalidates_inflight_generation_without_recording_false_freshness(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.app.initial_checks.add("a")

        def lookup(channel_id):
            entered.set()
            release.wait(2)
            return True, "Stale synthetic broadcast"

        with patch("autochzzk.get_live_status", side_effect=lookup):
            self.app._monitor()
            self.assertTrue(entered.wait(1))
            self.app.toggle_channel("a")
            release.set()
            self.complete_results()
        self.assertFalse(self.app.channels[0]["enabled"])
        self.assertNotIn("a", self.app.live_info)
        self.assertNotIn("a", self.app.last_successful_check)
        self.app._open_live.assert_not_called()

    def test_late_browser_callbacks_independently_respect_pause(self):
        self.app.pause_until = 2800
        self.app.live_info["a"] = (True, "Synthetic live")
        with patch("autochzzk.CHROME_TABS") as tabs:
            tabs.is_connected.return_value = True
            tabs.is_watched.return_value = False
            AutoChzzkApp._open_live(self.app, self.app.channels[0], "Synthetic live")
            AutoChzzkApp._close_finished_live(self.app, self.app.channels[0])
            tabs.queue_background_open.assert_not_called()
            tabs.queue_background_close.assert_not_called()

    def test_pause_cancels_queued_tabs_and_resume_retries_canceled_close(self):
        channel_id = 'a' * 32
        self.app.channels = [dict(id=channel_id, name='Synthetic', enabled=True, interval=60)]
        self.app.was_live = {channel_id: False}
        self.app.live_info = {channel_id: (False, '')}
        tabs = ChromeTabState()
        tabs.set_selected_profile({'gaia:synthetic'})
        tabs.update('synthetic-client', set(), {'gaia:synthetic'}, True, '2.1.0')
        tabs.queue_background_close('https://chzzk.naver.com/live/' + channel_id)
        tabs.queue_background_open('https://chzzk.naver.com/live/' + 'b' * 32)
        tabs.queue_extension_reload('2.1.3')

        with patch('autochzzk.CHROME_TABS', tabs):
            self.app.pause_monitoring(30)
            self.assertEqual([command['action'] for command in tabs.pending_commands('synthetic-client')], ['reload'])
            self.assertTrue(self.app.was_live[channel_id])
            with patch('autochzzk.get_live_status', return_value=(False, '')):
                self.app.resume_monitoring()
                self.app._monitor()
                self.complete_results()
        self.app._close_finished_live.assert_called_once_with(self.app.channels[0])

    def test_individual_refresh_survives_toggle_off_before_result(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def lookup(channel_id):
            entered.set()
            release.wait(2)
            return True, 'Synthetic refreshed title'

        with patch('autochzzk.get_live_status', side_effect=lookup):
            self.app.refresh_channel('a')
            self.app._monitor()
            self.assertTrue(entered.wait(1))
            self.app.toggle_channel('a')
            release.set()
            self.complete_results()
            self.app._monitor()
            self.assertIn(('live', 'a'), self.app.lookup_pool.pending)
            self.complete_results()
        self.assertEqual(self.app.live_info['a'], (True, 'Synthetic refreshed title'))
        self.assertEqual(self.app.last_successful_check['a'], 1000)
        self.assertFalse(self.app.channels[0]['enabled'])
        self.app._open_live.assert_not_called()

    def test_deleted_unqueried_batch_channels_are_not_counted_as_success(self):
        self.app.refresh_all_channels()
        self.app.remove_channel('b')
        self.app.remove_channel('a')
        self.assertIn('0개 정상 확인', self.app._set_status.call_args.args[0])
        self.assertFalse(self.app.last_successful_check)


if __name__ == "__main__":
    unittest.main()
