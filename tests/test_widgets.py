import time
import tkinter as tk
import unittest

from PIL import ImageTk

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

    def switch_image(self, toggle, part):
        self.assertEqual(
            toggle.type(part), 'image',
            'Curved switch edges need pixels with partial coverage',
        )
        return ImageTk.getimage(getattr(toggle, f'_{part}_image'))

    def test_curved_edges_include_partial_coverage_pixels(self):
        toggle = self.make_toggle(value=True)
        for part in ('track', 'knob'):
            with self.subTest(part=part):
                alpha = self.switch_image(toggle, part).getchannel('A')
                self.assertEqual(alpha.getpixel((0, 0)), 0)
                self.assertEqual(alpha.getpixel((alpha.width // 2, alpha.height // 2)), 255)
                self.assertGreater(sum(alpha.histogram()[1:255]), 0)

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
        self.assertEqual(int(toggle.cget('highlightthickness')), 0)
        self.assertEqual(self.switch_image(toggle, 'track').getpixel((0, 14))[3], 0)
        toggle.event_generate('<KeyPress-space>')
        toggle.event_generate('<KeyPress-Return>')
        self.root.update()
        self.assertEqual(requests, [True, True, True])
        self.assertFalse(toggle.get_value())
        self.assertGreater(self.switch_image(toggle, 'track').getpixel((0, 14))[3], 0)

        toggle.event_generate('<Button-1>', x=50, y=17)
        self.root.update()
        self.assertEqual(self.switch_image(toggle, 'track').getpixel((0, 14))[3], 0)

    def test_tab_focus_shows_a_small_ring_and_clears_when_leaving(self):
        preceding = tk.Button(self.root, text='Previous')
        preceding.pack()
        toggle = self.make_toggle()
        preceding.focus_force()
        self.root.update()
        preceding.event_generate('<KeyPress-Tab>')
        self.root.update()
        self.assertIs(self.root.focus_get(), toggle)
        self.assertGreater(self.switch_image(toggle, 'track').getpixel((0, 14))[3], 0)

        toggle.event_generate('<KeyPress-Tab>')
        self.root.update()
        self.assertIsNot(self.root.focus_get(), toggle)
        self.assertEqual(self.switch_image(toggle, 'track').getpixel((0, 14))[3], 0)

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


class ChannelOptionsMenuTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk display unavailable: {error}')
        self.root.geometry('540x620+30000+30000')
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.addCleanup(self.root.destroy)
        self.previous_focus = tk.Entry(self.root)
        self.previous_focus.place(x=20, y=10, width=200, height=24)
        self.row = tk.Frame(self.root)
        self.row.place(x=20, y=60, width=500, height=72)
        self.root.update()
        self.previous_focus.focus_force()
        self.root.update()

    def make_menu(self, *, edit_command=lambda: None, delete_command=lambda: None):
        self.assertTrue(hasattr(widgets, 'ChannelOptionsMenu'), 'ChannelOptionsMenu API is missing')
        menu = widgets.ChannelOptionsMenu(
            self.root, interval=15, edit_command=edit_command,
            delete_command=delete_command, bg='#2C2F38', text_color='#F4F6F8',
            muted='#A7ABB7', danger='#FF6B7A',
        )
        menu.show(self.row)
        self.root.update()
        return menu

    def action_buttons(self, menu):
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        return [child for child in children(menu) if isinstance(child, tk.Button)]

    def assert_inside_app(self, menu):
        self.assertGreaterEqual(menu.winfo_rootx(), self.root.winfo_rootx())
        self.assertGreaterEqual(menu.winfo_rooty(), self.root.winfo_rooty())
        self.assertLessEqual(
            menu.winfo_rootx() + menu.winfo_width(),
            self.root.winfo_rootx() + self.root.winfo_width(),
        )
        self.assertLessEqual(
            menu.winfo_rooty() + menu.winfo_height(),
            self.root.winfo_rooty() + self.root.winfo_height(),
        )

    def test_actions_close_and_release_grab_before_callback(self):
        calls = []
        for index, action in enumerate(('edit', 'delete')):
            with self.subTest(action=action):
                def callback(value=action):
                    calls.append((value, bool(menu.winfo_exists()), self.root.grab_current()))

                menu = self.make_menu(edit_command=callback, delete_command=callback)
                self.assertIs(self.root.grab_current(), menu)
                self.action_buttons(menu)[index].invoke()
                self.root.update()
                self.assertEqual(calls[-1], (action, False, None))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.callback_errors, [])

    def test_escape_restores_previous_focus_and_grab(self):
        self.root.grab_set()
        menu = self.make_menu()
        focused = self.root.focus_get()
        self.assertIs(focused.winfo_toplevel(), menu)
        focused.event_generate('<KeyPress-Escape>')
        self.root.update()
        self.assertFalse(menu.winfo_exists())
        self.assertIs(self.root.grab_current(), self.root)
        self.assertIs(self.root.focus_get(), self.previous_focus)
        menu.close()
        self.root.grab_release()
        self.assertEqual(self.callback_errors, [])

    def test_opens_below_row_aligned_to_its_right_inset(self):
        menu = self.make_menu()
        self.assertGreaterEqual(menu.winfo_rooty(), self.row.winfo_rooty() + self.row.winfo_height())
        self.assertEqual(
            menu.winfo_rootx() + menu.winfo_width(),
            self.row.winfo_rootx() + self.row.winfo_width() - 16,
        )
        self.assert_inside_app(menu)

    def test_opens_above_bottom_row_and_clamps_to_app_edges(self):
        self.row.place_configure(y=550, height=54)
        self.root.update()
        menu = self.make_menu()
        self.assertLessEqual(menu.winfo_rooty() + menu.winfo_height(), self.row.winfo_rooty())
        self.assert_inside_app(menu)

        menu.close()

        self.row.place_configure(x=-20, y=-20, width=40, height=40)
        self.root.update()
        menu = self.make_menu()
        self.assert_inside_app(menu)

    def test_menu_stays_inside_app_at_negative_screen_coordinates(self):
        self.root.geometry('540x620+-900+-800')
        self.root.update()
        self.assertLess(self.root.winfo_rootx(), 0)
        self.assertLess(self.root.winfo_rooty(), 0)
        menu = self.make_menu()
        self.assert_inside_app(menu)

    def test_arrow_tab_and_enter_choose_the_expected_action_once(self):
        calls = []
        menu = self.make_menu(
            edit_command=lambda: calls.append('edit'),
            delete_command=lambda: calls.append('delete'),
        )
        buttons = self.action_buttons(menu)
        buttons[0].event_generate('<KeyPress-Down>')
        self.root.update()
        self.assertIs(self.root.focus_get(), buttons[1])
        buttons[1].event_generate('<KeyPress-Tab>')
        self.root.update()
        self.assertIs(self.root.focus_get(), buttons[0])
        buttons[0].event_generate('<Shift-KeyPress-Tab>')
        self.root.update()
        self.assertIs(self.root.focus_get(), buttons[1])
        buttons[1].event_generate('<KeyPress-Up>')
        self.root.update()
        self.assertIs(self.root.focus_get(), buttons[0])
        buttons[0].event_generate('<KeyPress-Return>')
        self.root.update()
        self.assertEqual(calls, ['edit'])
        self.assertFalse(menu.winfo_exists())
        self.assertIsNone(self.root.grab_current())
        self.assertEqual(self.callback_errors, [])

    def test_outside_click_and_scroll_dismiss_without_running_actions(self):
        calls = []
        for sequence, options in (
            ('<ButtonPress-1>', {'x': -20, 'y': -20}),
            ('<MouseWheel>', {'delta': -120}),
        ):
            with self.subTest(sequence=sequence):
                menu = self.make_menu(edit_command=lambda: calls.append('edit'))
                menu.event_generate(sequence, **options)
                self.root.update()
                self.assertFalse(menu.winfo_exists())
                self.assertIsNone(self.root.grab_current())
        self.assertEqual(calls, [])
        self.assertEqual(self.callback_errors, [])

    def test_focus_leaving_menu_dismisses_without_stealing_focus(self):
        menu = self.make_menu()
        self.previous_focus.focus_force()
        self.root.update()
        self.assertFalse(menu.winfo_exists())
        self.assertIsNone(self.root.grab_current())
        self.assertIs(self.root.focus_get(), self.previous_focus)
        self.assertEqual(self.callback_errors, [])


if __name__ == '__main__':
    unittest.main()
