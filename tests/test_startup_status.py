"""Production controller and real Tk rendering, without network mutations."""
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wirespot import controller, settings, ui
from wirespot.state import RelayRecord, State
from wirespot.status import DEFAULT_STATUS, normalize_status
from wirespot.theme import Fonts
from wirespot.tray import status_rows
from wirespot.window import MainWindow, TABS


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "state.json"
        d = Path(self.tmp.name)
        for target, value in (("wirespot.paths.STATE_PATH", self.state_path),
                              ("wirespot.paths.SETTINGS_PATH", d / "settings.json"),
                              ("wirespot.sync.PAUSE_PATH", d / "pause.json"),
                              ("wirespot.sync.LEGACY_PAUSE_PATH", d / "tray.json"),
                              ("wirespot.sync.BUS_PATH", d / "bus.json"),
                              ("wirespot.sync.JOURNAL_PATH", d / "activity.log"),
                              ("wirespot.admission.STORE_PATH", d / "devices.json")):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        self.out, self.err = sys.stdout, sys.stderr
        self.addCleanup(self.restore_output)

    def restore_output(self):
        sys.stdout, sys.stderr = self.out, self.err
        ui.INTERACTIVE, ui.FORCE_TIER = True, None

    def make_controller(self):
        ctl = controller.Controller()
        self.addCleanup(ctl.stop)
        return ctl

    def window(self, ctl):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(lambda: (root.update_idletasks(), root.destroy()))
        win = MainWindow(root, ctl, Fonts(root), Mock(), None)
        win.visible = True
        return win

    def test_real_window_all_bare_states_and_pages(self):
        ctl = self.make_controller()
        for raw in [{}, *({"state": s.value} for s in State), {"state": "UNKNOWN"},
                    {"state": "READY", "ssid": None, "rx": None, "security": None}]:
            with self.subTest(raw=raw):
                ctl.snap = raw
                win = self.window(ctl)
                for page, _, _ in TABS:
                    win.show_page(page, animate=False)
                for _, value in status_rows(raw):
                    self.assertIsInstance(value, str)
                    self.assertNotIn("None", value)

    def test_cold_disconnected_and_saved_live_before_poll(self):
        for saved in (None, "DISCONNECTED", "READY"):
            with self.subTest(saved=saved):
                if saved:
                    RelayRecord(state=saved, tunnel_name="test").save(self.state_path)
                settings.save({"hotspot": {"ssid": "Saved WiFi"}})
                ctl = self.make_controller()
                self.assertEqual(set(ctl.snap), set(DEFAULT_STATUS))
                self.assertEqual(ctl.snap["ssid"], "Saved WiFi")
                self.assertEqual(controller.icon_state(ctl.snap), "unknown")
                win = self.window(ctl)
                self.assertEqual(self.kv(win, "status"), "Not yet verified")
                ctl.accept_snapshot({"state": "READY", "ssid": "Verified WiFi", "fresh": True})
                win.on_snap()
                self.assertIn("Verified WiFi", self.kv(win, "hotspot"))

    def test_header_dots_hover_and_tray_panel_lifecycle(self):
        from wirespot import tray
        from wirespot.icons import IconSet

        ctl = self.make_controller()
        ctl.snap = normalize_status({"state": "READY", "fresh": True, "tunnel": "ws_x",
                                     "pending": [{"mac": "AA:BB:CC:00:00:01", "ip": "192.168.137.9", "name": "Pixel",
                                                  "device": "Google Pixel"}]})
        win = self.window(ctl)
        win.header._dots(hover=True)                     # renders the x / minus glyph dots
        win.header._dots(hover=False)
        win.header.set_active(False)
        panel = tray.TrayPanel(win.root, ctl, win.f, IconSet(win.root), open_window=lambda p=None: None,
                               open_page=lambda p: None, quit_app=lambda: None, run_doctor=lambda s: None)
        with patch.object(tray, "work_area", return_value=(0, 0, 1920, 1040)):
            panel.toggle(1800, 1060)
            self.assertTrue(panel.is_open)
            body = panel.view.body
            ctl.snap = normalize_status({**ctl.snap, "rx": 123456})
            panel.render()
            self.assertIs(panel.view.body, body)         # updated in place: no blink
            panel.animate()
            panel.close()                                # outside click / Esc
            self.assertFalse(panel.is_open)
            panel.toggle(1800, 1060)                     # the same click on the icon must not reopen it
            self.assertFalse(panel.is_open)
        for page in ("devices", "profiles", "hotspot", "checks", "activity", "settings"):
            win.open_page(page, animate=False)
            win.animate()
        win.back(animate=False)

    @staticmethod
    def kv(win, key):
        row = next(r for r in win.home_view.rows if r.e.get("id") == "kv:" + key)
        return row.v.cget("text")

    def test_live_values_update_in_place_without_rebuild(self):
        """The flicker fix: a new snapshot with the same rows must not recreate widgets."""
        ctl = self.make_controller()
        ctl.snap = normalize_status({"state": "READY", "fresh": True, "tunnel": "ws_x", "rx": 1, "clients": []})
        win = self.window(ctl)
        body = win.home_view.body
        ctl.snap = normalize_status({"state": "READY", "fresh": True, "tunnel": "ws_x", "rx": 5_000_000, "clients": []})
        win.on_snap()
        self.assertIs(win.home_view.body, body)
        self.assertIn("4.8 MiB", self.kv(win, "traffic"))
        ctl.snap = normalize_status({"state": "DISCONNECTED", "fresh": True})
        win.on_snap()
        self.assertIsNot(win.home_view.body, body)             # a state change rebuilds (off-screen, then swaps)

    def test_verified_runtime_and_missing_network(self):
        RelayRecord(state="READY", tunnel_name="test").save(self.state_path)
        ctl = self.make_controller()
        ctl.wg = False
        ctl.backend = Mock()
        ctl.backend.uplink.return_value = []
        ctl.backend.public_ip.return_value = ""
        with patch.object(ctl, "_autostart_enabled", return_value=False), patch.object(controller.profiles, "list_profiles", return_value=([], [])):
            for data, expected in [({"ok": True, "tunnel_state": "Running", "hotspot_state": "On"}, "READY"),
                                   ({"ok": True, "tunnel_state": "Stopped"}, "ERROR"),
                                   ({"ok": True, "tunnel_state": "Running", "hotspot_state": "Off"}, "VPN_CONNECTED"),
                                   ({"ok": False}, "UNKNOWN")]:
                ctl.backend.guard_tick.return_value = data
                self.assertEqual(ctl.snapshot()["state"], expected)

    def test_failed_first_poll_and_shutdown(self):
        ctl = self.make_controller()
        win = self.window(ctl)
        with patch.object(ctl, "snapshot", side_effect=OSError("network unavailable")), patch.object(controller.time, "sleep", side_effect=lambda _: setattr(ctl, "running", False)):
            ctl._poll_loop()
        event = ctl.events.get_nowait()
        self.assertEqual(event[0], "snap")
        ctl.running = True
        ctl.accept_snapshot(event[1])
        win.on_snap()
        self.assertEqual(controller.icon_state(ctl.snap), "unknown")
        ctl.stop()
        previous = ctl.snap
        ctl.accept_snapshot({"state": "READY"})
        self.assertIs(ctl.snap, previous)

    def test_defaults_are_independent_and_settings_sections_nullable(self):
        a = normalize_status({"settings": {"hotspot": None, "vpn": None, "behavior": None}})
        a["clients"].append("changed")
        self.assertEqual(normalize_status()["clients"], [])
        self.assertEqual(a["settings"]["hotspot"]["ssid"], "WireSpot")

    def test_frozen_entry_runs_real_tk_event_loop(self):
        from wirespot import desktop
        RelayRecord(state="READY", tunnel_name="test").save(self.state_path)
        ctl = self.make_controller()
        root = tk.Tk()
        root.withdraw()
        root.after(250, root.quit)
        self.addCleanup(lambda: (root.update_idletasks(), root.destroy()))
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(controller, "Controller", return_value=ctl), \
                patch.object(ctl, "start"), patch.object(tk, "Tk", return_value=root), \
                patch("wirespot.tray.NotifyIcon"), patch.object(desktop, "ensure_icons", return_value={}), \
                patch.object(desktop, "install_crash_handlers"):
            self.assertEqual(desktop.run(False, False), 0)
