import threading
import unittest
from unittest.mock import Mock, patch

from autochzzk_core.monitor import LookupPool
from autochzzk import AutoChzzkApp


class LookupPoolTests(unittest.TestCase):
    def test_capacity_deduplication_and_main_thread_delivery(self):
        pool = LookupPool(2)
        gate = threading.Event()
        called = []
        main = threading.get_ident()
        def work():
            gate.wait(2)
            return threading.get_ident()
        def done(value, error):
            called.append((threading.get_ident(), value, error))
        self.assertTrue(pool.submit("a", work, done))
        self.assertFalse(pool.submit("a", work, done))
        self.assertTrue(pool.submit("b", work, done))
        self.assertFalse(pool.submit("c", work, done))
        gate.set()
        # Synchronize using queue arrival rather than timing sleeps.
        results = [pool.results.get(timeout=2), pool.results.get(timeout=2)]
        for result in results:
            pool.results.put(result)
        self.assertEqual(called, [])
        pool.drain()
        self.assertEqual(len(called), 2)
        self.assertTrue(all(thread == main and worker != main and error is None for thread, worker, error in called))

    def test_worker_exception_and_shutdown(self):
        pool = LookupPool(1)
        called = Mock()
        pool.submit("bad", lambda: 1 / 0, called)
        result = pool.results.get(timeout=2)
        pool.results.put(result)
        pool.drain()
        self.assertIsInstance(called.call_args.args[1], ZeroDivisionError)
        pool.close()
        self.assertFalse(pool.submit("later", lambda: None, called))


class AppMonitorTests(unittest.TestCase):
    def setUp(self):
        self.app = AutoChzzkApp.__new__(AutoChzzkApp)
        self.app.channels = [{"id": "a", "enabled": True, "interval": 60}]
        self.app.stop_event = threading.Event()
        self.app.root = Mock()
        self.app.lookup_pool = Mock()
        self.callbacks = []
        self.app.lookup_pool.submit.side_effect = lambda key, work, callback: self.callbacks.append(callback) or True
        self.app.last_checked = {}
        self.app.initial_checks = {"a"}
        self.app.force_open_checks = set()
        self.app.retry_open_checks = set()
        self.app.channel_generations = {}
        self.app.was_live = {"a": True}
        self.app._record_live_status = Mock()
        self.app._open_live = Mock()
        self.app._close_finished_live = Mock()
        self.app._set_status = Mock()

    def test_error_preserves_live_state_and_does_not_close(self):
        self.app._monitor()
        self.callbacks[0](None, ValueError())
        self.assertTrue(self.app.was_live["a"])
        self.app._close_finished_live.assert_not_called()
        self.app._record_live_status.assert_not_called()

    def test_removed_or_toggled_channel_ignores_old_response(self):
        self.app._monitor()
        self.app._invalidate_channel("a")
        self.callbacks[0]((False, ""), None)
        self.app._record_live_status.assert_not_called()
        self.app._close_finished_live.assert_not_called()

    def test_valid_transition_closes_once_and_connect_forces_open(self):
        self.app._monitor()
        self.callbacks[0]((False, ""), None)
        self.app._close_finished_live.assert_called_once()
        self.app.force_open_checks.add("a")
        self.app._monitor()
        self.callbacks[1]((True, "live"), None)
        self.app._open_live.assert_called_once()

    def test_failed_save_does_not_apply_channel_mutations(self):
        self.app._save_channels = Mock(return_value=False)
        self.app._refresh_list = Mock()
        self.app.toggle_channel("a")
        self.assertTrue(self.app.channels[0]["enabled"])
        self.app.remove_channel("a")
        self.assertEqual(len(self.app.channels), 1)
        self.app.update_interval("a", "90")
        self.assertEqual(self.app.channels[0]["interval"], 60)
        self.app._refresh_list.assert_not_called()

    def test_failed_reconnect_lookup_retries_open_on_next_normal_check(self):
        self.app.force_open_checks.add("a")
        self.app._monitor()
        self.callbacks[0](None, ValueError())
        self.assertTrue(self.app.was_live["a"])
        self.assertIn("a", self.app.retry_open_checks)
        self.app.last_checked.clear()
        self.app._monitor()
        self.callbacks[1]((True, "live"), None)
        self.app._open_live.assert_called_once()
        self.assertNotIn("a", self.app.retry_open_checks)


if __name__ == "__main__":
    unittest.main()
