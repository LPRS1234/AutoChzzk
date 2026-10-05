import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from autochzzk import AutoChzzkApp


class SettingsOverlayUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk display unavailable: {error}')
        self.addCleanup(self.root.destroy)
        self.root.geometry('620x650+30000+30000')
        self.background_entry = tk.Entry(self.root)
        self.background_entry.pack()
        app = self.app = AutoChzzkApp.__new__(AutoChzzkApp)
        app.root = self.root
        app.active_dialog = None
        app.changelog_dialog = None
        app.selected_chrome_profile = dict(directory='Default', name='Synthetic profile', gaia_id='', email='')
        app.chrome_profiles = [app.selected_chrome_profile,
                               dict(directory='Profile 1', name='Other profile', gaia_id='', email='')]
        app.profile_labels = {profile['name']: profile for profile in app.chrome_profiles}
        app.profile_value = tk.StringVar(self.root, value='Synthetic profile')
        app.extension_status_value = tk.StringVar(self.root, value='Synthetic extension status')
        app.version_value = tk.StringVar(self.root, value='Synthetic version status')
        app.canvas = tk.Canvas(self.root, height=200, scrollregion=(0, 0, 400, 1600))
        app.canvas.pack(fill='both', expand=True)
        app._configure_styles()
        app._build_settings_dialog()
        self.root.update()

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def close_button(self):
        return next(widget for widget in self.descendants(self.app.settings_dialog)
                    if widget.winfo_class() in ('Button', 'TButton') and widget.cget('text') == '닫기')

    def test_settings_open_inside_main_window_and_restore_focus_on_close(self):
        app = self.app
        self.background_entry.focus_force()
        self.root.update()
        app.show_settings()
        self.root.update()

        self.assertIs(app.settings_dialog.winfo_toplevel(), self.root)
        self.assertEqual(app.settings_dialog.winfo_manager(), 'place')
        self.assertIs(self.root.grab_current(), app.settings_dialog)
        self.close_button().invoke()
        self.root.update()
        self.assertFalse(app.settings_dialog.winfo_ismapped())
        self.assertIsNone(self.root.grab_current())
        self.assertIs(self.root.focus_get(), self.background_entry)

    def test_settings_card_stays_centered_and_close_is_visible_after_resize(self):
        app = self.app
        app.show_settings()
        for width, height in [(540, 540), (860, 780)]:
            with self.subTest(size=(width, height)):
                self.root.geometry(f'{width}x{height}+30000+30000')
                self.root.update()
                self.assertEqual(app.settings_dialog.winfo_width(), width)
                self.assertEqual(app.settings_dialog.winfo_height(), height)
                card = self.close_button().master
                self.assertGreaterEqual(card.winfo_x(), 12)
                self.assertGreaterEqual(card.winfo_y(), 12)
                self.assertLessEqual(card.winfo_x() + card.winfo_width(), width - 12)
                self.assertLessEqual(card.winfo_y() + card.winfo_height(), height - 12)
                self.assertAlmostEqual(card.winfo_x() + card.winfo_width() / 2, width / 2, delta=1)
                self.assertAlmostEqual(card.winfo_y() + card.winfo_height() / 2, height / 2, delta=1)
                close = self.close_button()
                self.assertTrue(close.winfo_viewable())
                self.assertLessEqual(close.winfo_y() + close.winfo_height(), card.winfo_height())

    def test_settings_content_scrolls_at_minimum_window_size(self):
        app = self.app
        self.root.geometry('540x540+30000+30000')
        app.show_settings()
        self.root.update()
        canvases = [widget for widget in self.descendants(app.settings_dialog) if isinstance(widget, tk.Canvas)]
        self.assertEqual(len(canvases), 1)
        canvas = canvases[0]
        self.assertLess(canvas.yview()[1], 1)
        label = next(widget for widget in self.descendants(app.settings_dialog)
                     if isinstance(widget, tk.Label) and widget.winfo_viewable())
        label.event_generate('<MouseWheel>', delta=-120)
        self.root.update()
        self.assertGreater(canvas.yview()[0], 0)
        self.assertTrue(self.close_button().winfo_viewable())

    def test_scrollbar_reappears_when_large_settings_window_is_shrunk(self):
        app = self.app
        self.root.geometry('860x780+30000+30000')
        app.show_settings()
        self.root.update()
        scrollbar = next(widget for widget in self.descendants(app.settings_dialog)
                         if widget.winfo_class() == 'TScrollbar')
        self.assertFalse(scrollbar.winfo_ismapped())
        self.root.geometry('540x540+30000+30000')
        self.root.update()
        self.assertTrue(scrollbar.winfo_ismapped())

    def test_escape_from_child_control_closes_settings(self):
        app = self.app
        app.show_settings()
        self.root.update()
        self.close_button().focus_force()
        self.close_button().event_generate('<Escape>')
        self.root.update()
        self.assertFalse(app.settings_dialog.winfo_ismapped())
        self.assertIsNone(self.root.grab_current())

    def test_tab_keeps_keyboard_focus_inside_settings(self):
        app = self.app
        app.show_settings()
        self.root.update()
        self.close_button().focus_force()
        self.close_button().event_generate('<Tab>')
        self.root.update()
        focused = self.root.focus_get()
        self.assertTrue(str(focused).startswith(str(app.settings_dialog) + '.'))
        self.assertIsNot(focused, self.background_entry)

    def test_tab_scrolls_settings_to_reveal_focused_control(self):
        app = self.app
        self.root.geometry('540x540+30000+30000')
        app.show_settings()
        self.root.update()
        buttons = {widget.cget('text'): widget for widget in self.descendants(app.settings_dialog)
                   if widget.winfo_class() == 'TButton'}
        canvas = next(widget for widget in self.descendants(app.settings_dialog) if isinstance(widget, tk.Canvas))
        buttons['업데이트 내역'].focus_force()
        buttons['업데이트 내역'].event_generate('<Tab>')
        self.root.update()
        tray_button = buttons['트레이로 숨기기']
        self.assertIs(self.root.focus_get(), tray_button)
        self.assertGreaterEqual(tray_button.winfo_rooty(), canvas.winfo_rooty())
        self.assertLessEqual(tray_button.winfo_rooty() + tray_button.winfo_height(),
                             canvas.winfo_rooty() + canvas.winfo_height())

    def test_settings_blocks_channel_list_mousewheel(self):
        app = self.app
        app.show_settings()
        self.root.update()
        before = app.canvas.yview()
        pointer = (app.canvas.winfo_rootx() + 10, app.canvas.winfo_rooty() + 10)
        with patch.object(self.root, 'winfo_pointerxy', return_value=pointer):
            app._on_list_mousewheel(SimpleNamespace(delta=-120))
        self.assertEqual(app.canvas.yview(), before)

    def test_auxiliary_escape_keeps_settings_open_and_returns_grab(self):
        app = self.app
        app.show_settings()
        self.root.update()
        app._show_app_dialog('Synthetic dialog', 'Synthetic message')
        self.root.update()
        app.active_dialog.focus_force()
        app.active_dialog.event_generate('<Escape>')
        self.root.update()
        self.assertIsNone(app.active_dialog)
        self.assertTrue(app.settings_dialog.winfo_viewable())
        self.assertIs(self.root.grab_current(), app.settings_dialog)


if __name__ == '__main__':
    unittest.main()
