import time
import tkinter as tk
import unittest

from autochzzk_core import widgets


class AnimatedToggleTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk display unavailable: {error}')
        self.root.geometry('110x70+30000+30000')
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.addCleanup(self.root.destroy)

    def make_toggle(self, *, value=False, command=lambda: None):
        self.assertTrue(hasattr(widgets, 'AnimatedToggle'), 'AnimatedToggle API is missing')
        toggle = widgets.AnimatedToggle(
            self.root, value=value, command=command, bg='#16181D',
        )
        toggle.pack()
        self.root.update()
        return toggle

    def pump_for(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.004)
        self.root.update()

    def knob_x(self, toggle):
        return toggle.coords('knob')[0]

    def test_immediate_state_moves_knob_and_updates_display(self):
        toggle = self.make_toggle()
        off_x = self.knob_x(toggle)
        self.assertFalse(toggle.get_value())
        self.assertEqual(toggle.itemcget('label', 'text'), 'OFF')

        toggle.set_value(True, animate=False)
        self.assertTrue(toggle.get_value())
        self.assertEqual(toggle.itemcget('label', 'text'), 'ON')
        self.assertGreater(self.knob_x(toggle), off_x)

        toggle.set_value(False, animate=False)
        self.assertFalse(toggle.get_value())
        self.assertEqual(self.knob_x(toggle), off_x)
        self.assertEqual(toggle.itemcget('label', 'text'), 'OFF')

    def test_animation_has_intermediate_motion_and_reaches_endpoint(self):
        toggle = self.make_toggle()
        off_x = self.knob_x(toggle)
        toggle.set_value(True, animate=False)
        on_x = self.knob_x(toggle)
        toggle.set_value(False, animate=False)

        toggle.set_value(True)
        self.assertTrue(toggle.get_value())
        self.assertEqual(self.knob_x(toggle), off_x)
        self.pump_for(0.05)
        self.assertGreater(self.knob_x(toggle), off_x)
        self.assertLess(self.knob_x(toggle), on_x)
        self.pump_for(0.20)
        self.assertEqual(self.knob_x(toggle), on_x)

    def test_rapid_reversals_start_from_current_position_and_finish(self):
        toggle = self.make_toggle()
        off_x = self.knob_x(toggle)
        toggle.set_value(True, animate=False)
        on_x = self.knob_x(toggle)
        toggle.set_value(False, animate=False)

        toggle.set_value(True)
        self.pump_for(0.035)
        first_position = self.knob_x(toggle)
        toggle.set_value(False)
        self.assertEqual(self.knob_x(toggle), first_position)
        self.pump_for(0.035)
        second_position = self.knob_x(toggle)
        self.assertLess(second_position, first_position)
        self.assertGreater(second_position, off_x)

        toggle.set_value(True)
        self.assertEqual(self.knob_x(toggle), second_position)
        self.pump_for(0.22)
        self.assertTrue(toggle.get_value())
        self.assertEqual(self.knob_x(toggle), on_x)
        self.pump_for(0.05)
        self.assertEqual(self.knob_x(toggle), on_x)

    def test_invoke_leaves_value_unchanged_until_caller_approves(self):
        requests = []
        toggle = self.make_toggle(command=lambda: requests.append(True))
        off_x = self.knob_x(toggle)
        toggle.invoke()
        self.assertEqual(requests, [True])
        self.assertFalse(toggle.get_value())
        self.pump_for(0.20)
        self.assertEqual(self.knob_x(toggle), off_x)

        toggle.set_value(True)
        self.assertTrue(toggle.get_value())
        self.pump_for(0.20)
        self.assertGreater(self.knob_x(toggle), off_x)

    def test_failed_command_preserves_state(self):
        def reject_change():
            raise OSError('Cannot save settings')

        toggle = self.make_toggle(value=True, command=reject_change)
        on_x = self.knob_x(toggle)
        with self.assertRaises(OSError):
            toggle.invoke()
        self.assertTrue(toggle.get_value())
        self.assertEqual(self.knob_x(toggle), on_x)

    def test_click_space_and_return_invoke_command_with_visible_focus(self):
        requests = []
        toggle = self.make_toggle(command=lambda: requests.append(True))
        toggle.event_generate('<Button-1>', x=50, y=17)
        toggle.focus_force()
        self.root.update()
        toggle.event_generate('<KeyPress-space>')
        toggle.event_generate('<KeyPress-Return>')
        self.root.update()
        self.assertEqual(requests, [True, True, True])
        self.assertFalse(toggle.get_value())
        self.assertGreater(int(toggle.cget('highlightthickness')), 0)
        self.assertNotEqual(toggle.cget('highlightcolor'), toggle.cget('highlightbackground'))

    def test_destroy_cancels_pending_animation(self):
        toggle = self.make_toggle()
        before = set(self.root.tk.call('after', 'info'))
        toggle.set_value(True)
        animation_callbacks = set(self.root.tk.call('after', 'info')) - before
        self.assertTrue(animation_callbacks)
        toggle.destroy()
        pending = set(self.root.tk.call('after', 'info'))
        self.assertTrue(animation_callbacks.isdisjoint(pending))
        self.pump_for(0.22)
        self.assertEqual(self.callback_errors, [])


if __name__ == '__main__':
    unittest.main()
