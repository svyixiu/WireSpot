"""Custom window behaviour on Windows: taskbar styles, dragging, close, hover, tray icon, setup wizard."""
import ctypes
import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from wirespot import chrome, motion


@unittest.skipUnless(os.name == "nt", "Windows only")
class ChromeTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.closed = []
        self.w = chrome.CustomWindow(self.root, 300, 200, "chrome-test", on_close=lambda: self.closed.append(1))
        self.w.place_default(200, 200)
        self.w.show()
        self.root.update()

    def style(self, idx):
        u = ctypes.WinDLL("user32")
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        return u.GetWindowLongPtrW(self.w.hwnd, idx)

    def test_real_taskbar_window(self):
        """Taskbar button that minimises/restores on click and offers 'Close window'."""
        self.assertTrue(self.style(chrome.GWL_EXSTYLE) & chrome.WS_EX_APPWINDOW)
        self.assertFalse(self.style(chrome.GWL_EXSTYLE) & chrome.WS_EX_TOOLWINDOW)
        self.assertTrue(self.style(chrome.GWL_STYLE) & chrome.WS_MINIMIZEBOX)
        self.assertTrue(self.style(chrome.GWL_STYLE) & chrome.WS_SYSMENU)
        u = ctypes.WinDLL("user32")
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        u.SendMessageW(self.w.hwnd, 0x0112, 0xF020, 0)            # what a taskbar click sends: SC_MINIMIZE
        self.root.update()
        self.assertTrue(self.w.is_minimized())
        self.w.show()                                             # tray "Open WireSpot" restores it
        self.root.update()
        self.assertFalse(self.w.is_minimized())
        # the style survives the fade (-alpha) and minimise/restore
        self.assertTrue(self.style(chrome.GWL_STYLE) & chrome.WS_MINIMIZEBOX)

    def test_taskbar_close_and_alt_f4_go_to_on_close(self):
        u = ctypes.WinDLL("user32")
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        u.SendMessageW(self.w.hwnd, 0x0010, 0, 0)                 # WM_CLOSE
        self.root.update()
        self.assertEqual(self.closed, [1])

    def test_drag_moves_the_window_without_native_move_loop(self):
        x0, y0 = self.w.top.winfo_x(), self.w.top.winfo_y()
        ev = mock.Mock(x_root=x0 + 50, y_root=y0 + 10)
        self.w.drag_start(ev)
        self.w.drag_move(mock.Mock(x_root=x0 + 150, y_root=y0 + 70))
        self.root.update()
        self.assertEqual((self.w.top.winfo_x(), self.w.top.winfo_y()), (x0 + 100, y0 + 60))
        self.w.drag_end()
        self.assertFalse(hasattr(chrome.CustomWindow, "drag"))       # the crashing HTCAPTION path is gone


class HoverTests(unittest.TestCase):
    def test_moving_onto_a_child_is_not_leaving(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        root.deiconify()                          # Tk only delivers Enter/Leave to mapped widgets
        row = tk.Frame(root)
        row.pack()
        label = tk.Label(row, text="x")
        label.pack()
        root.update()
        calls = []
        motion.hover(row, (row, label), lambda: calls.append("in"), lambda: calls.append("out"))
        under = {"w": label}
        with mock.patch.object(tk.Misc, "winfo_containing", lambda self, x, y: under["w"]), \
                mock.patch.object(tk.Misc, "winfo_pointerxy", lambda self: (1, 1)):
            row.event_generate("<Enter>")
            row.event_generate("<Leave>")                 # pointer went onto the label: still inside
            label.event_generate("<Enter>")
            self.assertEqual(calls, ["in"])
            under["w"] = None                             # now really outside
            label.event_generate("<Leave>")
            self.assertEqual(calls, ["in", "out"])


@unittest.skipUnless(os.name == "nt", "Windows only")
class TrayIconTests(unittest.TestCase):
    def test_unchanged_icon_is_not_resent(self):
        from wirespot import tray

        n = tray.NotifyIcon(mock.Mock(), {})
        n.hwnd = 1234
        with mock.patch.object(tray.user32, "PostMessageW") as post:
            n.set("idle", "WireSpot · disconnected")
            n.set("idle", "WireSpot · disconnected")
            n.set("idle", "WireSpot · disconnected")
            self.assertEqual(post.call_count, 1)
            n.set("live", "WireSpot · live")
            self.assertEqual(post.call_count, 2)


@unittest.skipUnless(os.name == "nt", "Windows only")
class SetupWizardTests(unittest.TestCase):
    def test_welcome_options_and_choices(self):
        from wirespot import setup

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        near = Path(tmp.name) / "release"
        (near.parent / "portable" / "vpn").mkdir(parents=True)
        (near.parent / "portable" / "vpn" / "A.conf").write_text("x")
        near.mkdir()
        with mock.patch.dict(os.environ, {"APPDATA": str(Path(tmp.name) / "Roaming"),
                                          "WIRESPOT_DATA": str(Path(tmp.name) / "pd")}), \
                mock.patch.object(setup.installer, "installed_version", return_value="0.2.0"), \
                mock.patch.object(setup.installer, "app_running", return_value=True), \
                mock.patch("wirespot.autostart.is_enabled", return_value=False):
            app = setup.Installer(Path(tmp.name) / "payload", near)
            self.addCleanup(app.root.destroy)
            app.root.update()
            self.assertEqual(app.n_legacy, 1)
            self.assertTrue(app.opts["migrate"])
            self.assertFalse(app.accepted)
            welcome_page = app.page_frame
            app.options()
            self.assertIs(app.page_frame, welcome_page)
            app.toggle_agreement()
            app.root.update()
            self.assertTrue(app.accepted)
            app.toggle_agreement()
            app.root.update()
            self.assertFalse(app.accepted)
            app.options()
            self.assertIs(app.page_frame, welcome_page)
            app.toggle_agreement()
            app.options()
            app.root.update()
            self.assertIn("migrate", app.handlers)
            app.handlers["desktop"]()                         # a switch click flips the option
            self.assertFalse(app.opts["desktop"])
            with mock.patch.object(setup.installer, "install", return_value={"dest": tmp.name, "shortcuts": []}) as inst, \
                    mock.patch.object(setup.subprocess, "Popen"):
                app.install()
                for _ in range(100):
                    app.root.update()
                    if app.result:
                        break
                    app.pump()
                self.assertEqual(inst.call_args.kwargs["desktop"], False)
                self.assertEqual(inst.call_args.kwargs["migrate_legacy"], True)
                self.assertEqual(inst.call_args.kwargs["accepted"]["terms"], setup.TERMS_VERSION)
                self.assertEqual(inst.call_args.kwargs["accepted"]["privacy"], setup.PRIVACY_VERSION)
                self.assertIsNotNone(app.result)

    def test_still_running_offers_to_close_it(self):
        from wirespot import setup

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(setup.installer, "installed_version", return_value=""), \
                mock.patch("wirespot.autostart.is_enabled", return_value=False):
            app = setup.Installer(Path(tmp.name), Path(tmp.name))
            self.addCleanup(app.root.destroy)
            app.steps = tk.Frame(app.root)
            app.on_fail(setup.installer.AppStillRunning("x"))
            self.assertEqual(app.title.cget("text"), "WireSpot is still running")


if __name__ == "__main__":
    unittest.main()
