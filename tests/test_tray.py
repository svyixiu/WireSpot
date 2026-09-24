"""Tray, autostart, icon, cross-process lock and palette - the parts testable without a desktop."""
import re
import struct
import time
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from wirespot import autostart, icon, oplock, ui
from wirespot.state import State

try:
    from wirespot import tray
except Exception:  # pragma: no cover - tray needs Windows
    tray = None


class PaletteTests(unittest.TestCase):
    ALLOWED = set(ui.PALETTE)

    def test_cli_uses_only_claude_palette(self):
        for name in ("claude", "shimmer", "success", "error", "warning", "suggestion", "muted", "text"):
            code = getattr(ui.C, name)
            rgb = tuple(int(x) for x in re.findall(r"\d+", code)[2:5])
            self.assertIn(rgb, self.ALLOWED, name)

    def test_no_green_or_blue_aliases_left(self):
        for legacy in ("green", "cyan", "yellow", "red", "magenta"):
            self.assertFalse(hasattr(ui.C, legacy), legacy)

    def test_gui_hex_colours_are_palette_or_warm_neutral(self):
        from wirespot import theme
        def warm_neutral(h):
            r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
            return r >= g >= b and r - b <= 12          # greys with a warm cast
        allowed = {"#%02X%02X%02X" % c for c in ui.PALETTE}
        for name in ("BG", "SURFACE", "ROW_HOVER", "SELECTED", "BORDER", "RULE", "CLAY", "SHIMMER", "CRAIL",
                     "CREAM", "LIGHT", "STONE", "DIM", "INPUT"):
            h = getattr(theme, name).upper()
            self.assertTrue(h in allowed or warm_neutral(h), f"{name}={h}")


class IconTests(unittest.TestCase):
    def test_ico_structure(self):
        data = icon.ico_bytes("live", sizes=(16, 32))
        reserved, typ, count = struct.unpack_from("<HHH", data)
        self.assertEqual((reserved, typ, count), (0, 1, 2))
        w, h, _, _, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6)
        self.assertEqual((w, h, planes, bpp), (16, 16, 1, 32))
        self.assertEqual(struct.unpack_from("<I", data, offset)[0], 40)      # BITMAPINFOHEADER

    def test_every_state_renders_distinctly(self):
        imgs = {s: icon.render(16, s) for s in icon.STYLES}
        self.assertEqual(len({tuple(v) for v in imgs.values()}), len(imgs))
        self.assertEqual(imgs["live"][0][3], 0)                               # transparent corner

    def test_icon_colours_are_palette(self):
        allowed = set(ui.PALETTE) | {icon.DARK}
        for badge, mark, _ in icon.STYLES.values():
            self.assertIn(badge, allowed)
            self.assertIn(mark, allowed)


class AutostartTests(unittest.TestCase):
    def test_task_xml(self):
        xml = autostart.task_xml(r"C:\Apps\WireSpot\WireSpotTray.exe", "--autostart", r"PC\july")
        root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.find(".//t:RunLevel", ns).text, "HighestAvailable")       # no UAC prompt
        self.assertEqual(root.find(".//t:DisallowStartIfOnBatteries", ns).text, "false")
        self.assertEqual(root.find(".//t:StopIfGoingOnBatteries", ns).text, "false")
        self.assertEqual(root.find(".//t:ExecutionTimeLimit", ns).text, "PT0S")        # never killed
        self.assertEqual(root.find(".//t:LogonTrigger/t:UserId", ns).text, r"PC\july")
        self.assertEqual(root.find(".//t:Command", ns).text, r"C:\Apps\WireSpot\WireSpotTray.exe")

    def test_xml_escapes(self):
        xml = autostart.task_xml(r"C:\A&B\t.exe", "--x", "u<1>")
        self.assertIn("A&amp;B", xml)
        self.assertIn("u&lt;1&gt;", xml)


class OpLockTests(unittest.TestCase):
    def test_reentrant_and_exclusive_name(self):
        m = oplock.NamedMutex("Local\\WireSpot.Test.%d" % time.time_ns())
        self.assertTrue(m.acquire(0))
        self.assertTrue(m.acquire(0))            # same thread may re-enter
        self.assertTrue(oplock.exists(m.name))
        m.close()

    def test_operation_context(self):
        with oplock.operation():
            with oplock.operation():            # nested: hotspot start -> start
                pass
        self.assertFalse(oplock.operation_busy())


@unittest.skipIf(tray is None, "Windows only")
class TrayLogicTests(unittest.TestCase):
    def test_icon_state(self):
        self.assertEqual(tray.icon_state({"state": State.READY.value}), "live")
        self.assertEqual(tray.icon_state({"state": State.VPN_CONNECTED.value}), "vpn")
        self.assertEqual(tray.icon_state({"state": State.DISCONNECTED.value}), "idle")
        self.assertEqual(tray.icon_state({"state": State.ERROR.value}), "error")
        self.assertEqual(tray.icon_state({"state": State.READY.value, "busy": "Reconnecting"}), "busy")
        self.assertEqual(tray.icon_state({"state": State.DISCONNECTED.value, "paused_until": time.time() + 60}), "paused")

    def test_tooltip(self):
        tip = tray.tooltip({"state": State.READY.value, "ssid": "JulyVPN", "clients": [1, 2]})
        self.assertEqual(tip, "WireSpot · live · JulyVPN · 2 devices")
        self.assertLessEqual(len(tray.tooltip({"state": "DISCONNECTED"})), 127)

    def test_place_above_bottom_taskbar(self):
        with mock.patch.object(tray, "work_area", return_value=(0, 0, 1920, 1040)):
            x, y = tray.place(1800, 1060, 400, 500)
            self.assertEqual((x, y), (1920 - 400 - 8, 1040 - 500 - 8))
            x, y = tray.place(1800, 10, 400, 500)                  # taskbar on top
            self.assertEqual(y, 8)

    def test_activity_collects_lines_without_ansi(self):
        a = tray.Activity()
        a.write("\x1b[38;2;1;2;3m● hello\x1b[0m\nwor")
        a.write("ld\n")
        self.assertEqual(a.last(2), ["● hello", "world"])

    def test_fmt_duration(self):
        self.assertEqual(tray.fmt_duration(59), "59s")
        self.assertEqual(tray.fmt_duration(3725), "1h 02m")


@unittest.skipIf(tray is None, "Windows only")
class ControllerTests(unittest.TestCase):
    def setUp(self):
        import sys
        self._out, self._err = sys.stdout, sys.stderr
        from wirespot.controller import Controller
        self.ctl = Controller()

    def tearDown(self):
        import sys
        sys.stdout, sys.stderr = self._out, self._err
        ui.INTERACTIVE, ui.FORCE_TIER = True, None
        self.ctl.stop()

    def choices(self):
        from wirespot.ui import Choice
        return [Choice("once", "Use balanced"), Choice("strict", "Keep strict"), Choice("stop", "Stop")]

    def test_decide_uses_recommended_when_window_hidden(self):
        self.assertEqual(self.ctl.decide("route?", self.choices(), 0, "stop"), "once")
        self.assertTrue(self.ctl.events.empty())

    def test_decide_asks_the_window_when_visible(self):
        import threading
        self.ctl.window_visible = True
        result = {}
        t = threading.Thread(target=lambda: result.setdefault("k", self.ctl.decide("route?", self.choices(), 0, "stop")))
        t.start()
        ev = self.ctl.events.get(timeout=5)
        self.assertEqual(ev[0], "ask")
        holder = ev[4]
        holder["answer"] = "strict"
        holder["event"].set()
        t.join(5)
        self.assertEqual(result["k"], "strict")

    def test_busy_blocks_a_second_action(self):
        self.ctl.busy = "Reconnecting"
        self.ctl.do("disconnect")
        ev = self.ctl.events.get(timeout=2)
        self.assertEqual(ev[0], "toast")
        self.assertIn("Still working", ev[2])

    def test_activity_is_captured_with_timestamps(self):
        ui.ok("hello")
        self.assertEqual(self.ctl.activity.last(1), ["● hello"])
        self.assertRegex(self.ctl.activity.all()[-1], r"^\d\d:\d\d:\d\d ● hello$")


if __name__ == "__main__":
    unittest.main()
