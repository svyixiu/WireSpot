"""Sync between CLI and app, device approval, installer, SVG icons and the shared panel model."""
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from wirespot import admission, clients, icons, installer, neighbors, panel, paths, sync
from wirespot.status import normalize_status

# Unicode symbols the desktop UI must not draw as text any more (they are SVG icons now).
BANNED = set("●○▶■↻❚✚⎘›☑☐✕◧▾▸⎿↓↑✻✖▲◎◈▤≡⚙◉✓")


class TempDirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        for target, value in (("wirespot.sync.BUS_PATH", self.d / "bus.json"),
                              ("wirespot.sync.PAUSE_PATH", self.d / "pause.json"),
                              ("wirespot.sync.LEGACY_PAUSE_PATH", self.d / "tray.json"),
                              ("wirespot.sync.JOURNAL_PATH", self.d / "activity.log"),
                              ("wirespot.admission.STORE_PATH", self.d / "devices.json")):
            p = mock.patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        sync._local.depth = 0


# ===================================================================== icons
class IconTests(unittest.TestCase):
    def test_every_icon_is_valid_svg_and_renders(self):
        for name in icons.ICONS:
            with self.subTest(name=name):
                doc = icons.svg(name)
                self.assertTrue(doc.startswith("<svg") and doc.endswith("</svg>"))
                px = icons.rasterize(doc, 20, "#D97757")
                self.assertEqual(len(px), 400)
                self.assertGreater(sum(1 for p in px if p[3] > 128), 8)          # visibly drawn
                self.assertEqual(px[0][3], 0)                                    # transparent corner

    def test_generated_documents_render(self):
        for doc in (icons.spinner_doc(3, 18, "#D97757", "#34332F"), icons.dot_doc("#D97757", "x", "#1F1E1D")):
            self.assertTrue(any(p[3] for p in icons.rasterize(doc, 16, "#000000")))

    def test_arc_and_relative_commands(self):
        subs = icons.parse_path("M21 12a9 9 0 1 1-9-9c2.5 0 4.9 1 6.7 2.7L21 8")
        pts = subs[0][0]
        self.assertAlmostEqual(pts[-1][0], 21, places=3)
        self.assertTrue(any(abs(x - 3) < 0.3 for x, _ in pts))                  # sweeps round the far side

    def test_png_structure(self):
        data = icons.png_bytes(2, 1, [(1, 2, 3, 4), (5, 6, 7, 8)])
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")

    def test_export_writes_svg_files(self):
        with tempfile.TemporaryDirectory() as t:
            files = icons.export(Path(t))
            self.assertEqual(len(files), len(icons.ICONS))
            self.assertIn("<svg", files[0].read_text(encoding="utf-8"))

    def test_desktop_sources_draw_no_unicode_symbols(self):
        root = Path(__file__).resolve().parent.parent / "wirespot"
        for mod in ("panel.py", "window.py", "tray.py", "setup.py", "chrome.py"):
            text = (root / mod).read_text(encoding="utf-8")
            # ignore the glyph table that *translates* CLI output into icons, and docstrings/comments
            code = "\n".join(ln for ln in text.splitlines()
                             if not ln.strip().startswith("#") and "GLYPHS" not in ln and "_STRIP" not in ln
                             and "replace(\"" not in ln and "startswith" not in ln and "Running" not in ln)
            code = re.sub(r'"""[\s\S]*?"""', "", code)
            found = sorted({ch for ch in code if ch in BANNED})
            self.assertEqual(found, [], f"{mod} still draws {found}")


# ===================================================================== panel model
def snap(state="READY", **kw):
    base = {"state": state, "fresh": True, "tunnel": "ws_x", "ssid": "Net", "profile_label": "NL#1 · Netherlands",
            "clients": [("192.168.137.2", "iPhone", "iPhone")]}
    base.update(kw)
    return normalize_status(base)


class PanelModelTests(unittest.TestCase):
    def ids(self, model):
        return [e.get("id") for e in model]

    def test_tray_and_window_are_the_same_list_except_open(self):
        tray = self.ids(panel.home_model(snap(), "tray"))
        win = self.ids(panel.home_model(snap(), "window"))
        self.assertIn("open", tray)
        self.assertNotIn("open", win)
        self.assertEqual([i for i in tray if i not in ("open", "r-open")], win)

    def test_live_actions_stay_while_busy_but_disabled(self):
        m = panel.home_model(snap(), "window", busy="Reconnecting")
        items = {e["id"]: e for e in m if e["t"] == "item"}
        self.assertTrue(items["disconnect"]["disabled"])
        self.assertIn("busy", self.ids(m))
        self.assertNotIn("golive", items)

    def test_states(self):
        for state, must in (("READY", "hotspot_off"), ("VPN_CONNECTED", "hotspot_on"), ("DISCONNECTED", "golive"),
                            ("ERROR", "reconnect")):
            with self.subTest(state=state):
                self.assertIn(must, self.ids(panel.home_model(snap(state), "tray")))
        paused = snap("DISCONNECTED", paused_until=time.time() + 600)
        self.assertIn("resume", self.ids(panel.home_model(paused, "tray")))

    def test_pending_devices_come_first_with_allow_block(self):
        m = panel.home_model(snap(pending=[{"mac": "AA:BB:CC:00:00:01", "ip": "192.168.137.9", "name": "Pixel",
                                            "device": "Google Pixel"}]), "tray")
        self.assertEqual(m[0]["t"], "section")
        self.assertEqual(m[1]["t"], "pending")
        self.assertEqual(m[1]["mac"], "AA:BB:CC:00:00:01")

    def test_model_text_has_no_unicode_symbols(self):
        for state in ("READY", "VPN_CONNECTED", "DISCONNECTED", "ERROR", "UNKNOWN"):
            for e in panel.home_model(snap(state, rx=10, tx=10), "tray", busy="Working", detail="● VPN up"):
                text = " ".join(str(e.get(k, "")) for k in ("text", "hint", "v", "k"))
                self.assertFalse(set(text) & BANNED, (state, e))

    def test_split_glyph(self):
        self.assertEqual(panel.split_glyph("● VPN connected"), ("dot", panel.CLAY, "VPN connected"))
        self.assertEqual(panel.split_glyph("✖ failed")[0], "x")
        self.assertEqual(panel.split_glyph("  ⎿ detail")[2], "detail")
        self.assertNotIn("↓", panel.split_glyph("↓ 1 MB ↑ 2 MB")[2])

    def test_dispatch_keeps_tray_open_except_navigation(self):
        ctl = mock.Mock()
        ctl.snap = snap()
        host = mock.Mock(is_tray=True)
        self.assertFalse(panel.dispatch(ctl, "check_ip", host))
        ctl.do.assert_called_with("check_ip", None)
        self.assertFalse(panel.dispatch(ctl, "allow:AA:BB:CC:00:00:01", host))
        ctl.device_verdict.assert_called_with("AA:BB:CC:00:00:01", "approve", "")
        self.assertTrue(panel.dispatch(ctl, "page:devices", host))
        self.assertTrue(panel.dispatch(ctl, "open", host))


# ===================================================================== sync
class SyncTests(TempDirs):
    def test_operation_is_published_and_cleared(self):
        sync.op_begin("start")
        bus = sync.read_bus()
        self.assertEqual(bus["op"]["label"], "Going live")
        self.assertEqual(bus["op"]["pid"], os.getpid())
        self.assertIsNone(sync.remote_op(bus))                  # our own op is not "remote"
        sync.op_begin("start")                                  # nested: no second record
        sync.op_end(True)
        self.assertIsNotNone(sync.read_bus()["op"])
        sync.op_end(False, "tunnel failed")
        bus = sync.read_bus()
        self.assertIsNone(bus["op"])
        self.assertEqual(bus["last"]["label"], "Going live")
        self.assertFalse(bus["last"]["ok"])

    def test_remote_op_from_other_live_process_and_dead_pid(self):
        sync._write(sync.BUS_PATH, {"op": {"who": "cli", "pid": 424242, "label": "Going live", "since": time.time()}})
        with mock.patch.object(sync, "pid_alive", return_value=True):
            self.assertEqual(sync.describe(sync.remote_op()), "Going live (from the CLI)")
        with mock.patch.object(sync, "pid_alive", return_value=False):
            self.assertIsNone(sync.remote_op())                  # crashed process: not busy forever

    def test_pause_is_shared_and_cancelled_by_start_or_stop(self):
        sync.set_pause(time.time() + 900, "NL.conf")
        self.assertEqual(sync.read_pause()["pause_profile"], "NL.conf")
        sync.op_begin("stop")
        sync.op_end(True)
        self.assertEqual(sync.read_pause(), {})

    def test_legacy_tray_json_pause_is_read(self):
        (self.d / "tray.json").write_text(json.dumps({"pause_until": time.time() + 60, "pause_profile": "a"}))
        self.assertTrue(sync.read_pause())
        sync.clear_pause()
        self.assertFalse((self.d / "tray.json").exists())

    def test_journal_is_redacted(self):
        from wirespot import log

        log.register_secret("hunter2hunter2")
        key = "A" * 43 + "="
        sync.journal(f"password hunter2hunter2 PrivateKey = {key}", "cli")
        (_, src, line), = sync.read_journal()
        self.assertEqual(src, "cli")
        self.assertNotIn("hunter2hunter2", line)
        self.assertNotIn(key, line)

    def test_watcher_reports_changes(self):
        f = self.d / "bus.json"
        w = sync.Watcher(lambda c: None, files=[f])
        self.assertEqual(w.poll(), [])
        f.write_text("{}")
        self.assertEqual(w.poll(), [f])
        self.assertEqual(w.poll(), [])


# ===================================================================== approval
def entry(ip, mac, permanent=False):
    return {"ip": ip, "mac": mac, "permanent": permanent, "state": 6 if permanent else 5}


class ApprovalTests(TempDirs):
    MAC_A, MAC_B = "AA-BB-CC-00-00-01", "AA-BB-CC-00-00-02"

    def store(self, **kw):
        s = {"approved": {}, "blocked": {}, "pending": {}}
        s.update(kw)
        return s

    def test_unapproved_device_is_pinned_to_the_sink(self):
        pins, rel, seen = admission.plan([entry("192.168.137.5", self.MAC_A)], self.store())
        self.assertEqual(pins, [("192.168.137.5", neighbors.SINK_MAC)])
        self.assertEqual(seen, {"AA:BB:CC:00:00:01": ["192.168.137.5"]})

    def test_approved_device_is_left_alone_and_its_hold_released(self):
        store = self.store(approved={"AA:BB:CC:00:00:01": {}})
        pins, rel, _ = admission.plan([entry("192.168.137.5", self.MAC_A)], store)
        self.assertEqual((pins, rel), ([], []))
        pins, rel, _ = admission.plan([entry("192.168.137.5", neighbors.SINK_MAC, True)], store)
        self.assertEqual(rel, ["192.168.137.5"])                 # hold without a waiting owner -> released

    def test_held_device_stays_held(self):
        store = self.store(pending={"AA:BB:CC:00:00:02": {"ips": ["192.168.137.6"]}})
        pins, rel, seen = admission.plan([entry("192.168.137.6", neighbors.SINK_MAC, True)], store)
        self.assertEqual((pins, rel), ([], []))
        self.assertIn("AA:BB:CC:00:00:02", seen)

    def test_broadcast_multicast_and_foreign_permanent_entries_ignored(self):
        entries = [entry("192.168.137.255", "FF-FF-FF-FF-FF-FF", True), entry("224.0.0.22", "01-00-5E-00-00-16", True),
                   entry("192.168.137.7", "00-11-22-33-44-55", True)]
        self.assertEqual(admission.plan(entries, self.store()), ([], [], {}))

    def test_decide_moves_between_lists(self):
        admission.decide(self.MAC_A, "approve", "iPhone")
        self.assertIn("AA:BB:CC:00:00:01", admission.load()["approved"])
        admission.decide(self.MAC_A, "block")
        s = admission.load()
        self.assertNotIn("AA:BB:CC:00:00:01", s["approved"])
        self.assertEqual(s["blocked"]["AA:BB:CC:00:00:01"]["name"], "iPhone")
        admission.decide(self.MAC_A, "forget")
        self.assertEqual(admission.status_of(self.MAC_A, admission.load()), "pending")

    def test_enforce_first_run_trusts_devices_already_connected_then_holds_new_ones(self):
        table = [entry("192.168.137.5", self.MAC_A)]
        calls = []
        with mock.patch.object(neighbors, "table", side_effect=lambda idx: list(table)), \
                mock.patch.object(neighbors, "pin", side_effect=lambda *a: calls.append(("pin",) + a) or 0), \
                mock.patch.object(neighbors, "delete", side_effect=lambda *a: calls.append(("del",) + a) or 0):
            admission.enforce(7)
            self.assertEqual(calls, [])                            # the phone already on it keeps working
            table.append(entry("192.168.137.9", self.MAC_B))
            notes = []
            admission.enforce(7, notify=lambda mac, ips: notes.append(mac))
            self.assertEqual(calls, [("pin", 7, "192.168.137.9", neighbors.SINK_MAC)])
            self.assertEqual(notes, ["AA:BB:CC:00:00:02"])
            self.assertEqual(admission.load()["pending"]["AA:BB:CC:00:00:02"]["ips"], ["192.168.137.9"])

    def test_clients_merge_hides_the_sink(self):
        cl = clients.merge({"neighbors": [{"ip": "192.168.137.9", "mac": neighbors.SINK_MAC, "state": "Permanent"}]})
        self.assertEqual(cl, [])


# ===================================================================== installer + paths
class InstallerTests(TempDirs):
    def payload(self):
        p = self.d / "payload"
        p.mkdir()
        for n in installer.EXES:
            (p / n).write_bytes(b"MZ" + n.encode())
        return p

    def test_install_records_versioned_acceptance(self):
        import json
        accepted = {"terms": "1", "privacy": "1", "at": "2026-09-24T00:00:00+00:00"}
        dest, data = self.d / "install", self.d / "data"
        with mock.patch.object(installer, "close_running_app", return_value=True), \
                mock.patch.object(installer.autostart, "is_enabled", return_value=False):
            installer.install(self.payload(), dest, data, shortcuts=False, registry=False, accepted=accepted)
        marker = json.loads((dest / "install.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["accepted"], accepted)

    def test_install_copies_marks_and_migrates_by_moving(self):
        legacy = self.d / "old" / "portable"
        (legacy / "vpn").mkdir(parents=True)
        (legacy / "vpn" / "NL.conf").write_text("[Interface]\n")
        (legacy / "settings.json").write_text("{}")
        dest, data = self.d / "Programs" / "WireSpot", self.d / "Roaming" / "WireSpot"
        with mock.patch.object(installer, "close_running_app", return_value=True), \
                mock.patch.object(installer.autostart, "is_enabled", return_value=False):
            r = installer.install(self.payload(), dest, data, legacy_near=legacy.parent, shortcuts=False, registry=False)
        self.assertTrue(paths.is_installed(dest))
        self.assertEqual(sorted(p.name for p in dest.glob("*.exe")), sorted(installer.EXES))
        self.assertTrue((data / "vpn" / "NL.conf").exists())
        self.assertFalse((legacy / "vpn" / "NL.conf").exists())      # moved, not copied (one private key copy)
        self.assertTrue((data / "settings.json").exists())
        self.assertEqual(r["moved"], 1)

    def test_install_refuses_while_the_app_cannot_be_closed(self):
        with mock.patch.object(installer, "close_running_app", return_value=False):
            with self.assertRaises(RuntimeError):
                installer.install(self.payload(), self.d / "a", self.d / "b", shortcuts=False, registry=False)

    def _installed(self):
        dest, data = self.d / "Programs" / "WireSpot", self.d / "Roaming" / "WireSpot"
        with mock.patch.object(installer, "close_running_app", return_value=True), \
                mock.patch.object(installer.autostart, "is_enabled", return_value=False):
            installer.install(self.payload(), dest, data, shortcuts=False, registry=False)
        (data / "vpn" / "NL.conf").write_text("x")
        (data / "logs").mkdir()
        (data / "logs" / "history.txt").write_text("x")
        pd = self.d / "ProgramData" / "WireSpot"
        (pd / "runtime").mkdir(parents=True)
        (pd / "state.json").write_text("{}")
        (pd / "devices.json").write_text("{}")
        return dest, data, pd

    def _uninstall(self, keep, relay=None):
        dest, data, pd = self._installed()
        with mock.patch.object(installer.paths, "PROGRAMDATA_DIR", pd), \
                mock.patch.object(installer.autostart, "disable", return_value=(True, "")), \
                mock.patch.object(installer, "shortcut_paths", return_value=[]), \
                mock.patch.object(installer, "unregister"):
            ok, msg = installer.uninstall(keep, relay=relay, settings={}, dest=dest, data=data, schedule=False)
        return ok, msg, dest, data, pd

    def test_uninstall_keep_data(self):
        ok, msg, dest, data, pd = self._uninstall(True)
        self.assertTrue(ok, msg)
        self.assertTrue((data / "vpn" / "NL.conf").exists())
        self.assertTrue((data / "settings.json").exists())
        self.assertFalse((data / "logs").exists())
        self.assertFalse((pd / "state.json").exists())
        self.assertTrue((pd / "devices.json").exists())           # approved devices are "config" too

    def test_uninstall_delete_everything(self):
        ok, msg, dest, data, pd = self._uninstall(False)
        self.assertTrue(ok, msg)
        self.assertFalse(data.exists())
        self.assertFalse(pd.exists())

    def test_uninstall_stops_the_session_first_and_aborts_if_it_cannot(self):
        relay = mock.Mock()
        relay.m.state = "READY"
        relay.stop.return_value = False
        ok, msg, dest, data, pd = self._uninstall(False, relay)
        self.assertFalse(ok)
        self.assertTrue((data / "vpn" / "NL.conf").exists())      # nothing removed
        relay.stop.assert_called_once()

    def test_portable_copy_is_never_uninstalled(self):
        ok, msg = installer.uninstall(True, dest=self.d / "portable", data=self.d / "x", schedule=False)
        self.assertFalse(ok)
        self.assertIn("portable", msg)

    def test_data_folder_layout(self):
        inst = self.d / "Programs" / "WireSpot"
        inst.mkdir(parents=True)
        (inst / paths.INSTALL_MARKER).write_text("{}")
        port = self.d / "portable"
        (port / "vpn").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"APPDATA": str(self.d / "Roaming")}), \
                mock.patch.dict(os.environ, {}, clear=False) as env:
            env.pop("WIRESPOT_HOME", None)
            with mock.patch.object(sys, "frozen", True, create=True):
                with mock.patch.object(sys, "executable", str(inst / "WireSpot.exe")):
                    self.assertEqual(paths.base_dir(), self.d / "Roaming" / "WireSpot")
                with mock.patch.object(sys, "executable", str(port / "WireSpot.exe")):
                    self.assertEqual(paths.base_dir(), port)


# ===================================================================== CLI line editor
class LineEditorResizeTests(unittest.TestCase):
    def test_rows_above_after_the_window_narrows(self):
        from wirespot.lineedit import LineEditor

        ed = LineEditor(lambda t: [])
        ed.drawn_width, ed.drawn_col = 150, 12
        with mock.patch("wirespot.ui.width", return_value=199):            # wider: nothing re-wrapped
            self.assertEqual(ed._rows_above(), 1)
        with mock.patch("wirespot.ui.width", return_value=79):             # 80 cols: box lines now take 2 rows
            self.assertEqual(ed._rows_above(), 0 + 2)
        ed.drawn_col = 100
        with mock.patch("wirespot.ui.width", return_value=79):
            self.assertEqual(ed._rows_above(), 1 + 2)


if __name__ == "__main__":
    unittest.main()
