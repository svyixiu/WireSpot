"""Autocomplete menu and input-box behaviour (no console needed)."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import proton_conf
from wirespot import cli, lineedit, paths, ui


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        vpn = Path(self.tmp.name) / "vpn"
        vpn.mkdir()
        (vpn / "a-US-FREE-5.conf").write_text(proton_conf("US-FREE#5"), encoding="utf-8")
        (vpn / "b-NL-FREE-1.conf").write_text(proton_conf("NL-FREE#1"), encoding="utf-8")
        self.patches = [mock.patch.object(paths, "VPN_DIR", vpn),
                        mock.patch.object(paths, "HISTORY_PATH", Path(self.tmp.name) / "history.txt")]
        for p in self.patches:
            p.start()
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = cli.App()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def labels(self, text):
        return [s.label for s in self.app.complete(text)]

    def test_first_word_filters_commands_with_descriptions(self):
        sug = self.app.complete("st")
        self.assertEqual([s.value.strip() for s in sug], ["start", "stop", "status"])
        self.assertTrue(all(s.desc for s in sug))
        self.assertIn("clear", [s.value.strip() for s in self.app.complete("cl")])

    def test_start_suggests_profiles_then_options(self):
        sug = self.app.complete("start ")
        self.assertEqual(sug[0].label, "1")
        self.assertIn("US-FREE#5", sug[0].desc)
        self.assertIn("--band", self.labels("start 1 "))
        self.assertNotIn("1", self.labels("start 1 "))
        self.assertEqual(self.labels("start --band "), ["auto", "2.4", "5", "6"])

    def test_set_keys_and_values(self):
        self.assertIn("password", self.labels("set "))
        self.assertEqual(self.labels("set prot"), ["protection"])
        self.assertEqual(self.labels("set protection "), ["balanced", "strict"])
        self.assertEqual(self.labels("set band 2"), ["2.4"])

    def test_subcommands_and_doctor(self):
        self.assertEqual(self.labels("hotspot s"), ["start", "stop", "status"])
        self.assertIn("full", self.labels("doctor "))
        self.assertNotIn("full", self.labels("doctor full "))
        self.assertEqual(self.labels("profile use "), ["1", "2"])

    def test_runnable(self):
        self.assertTrue(self.app.is_runnable("start 1"))
        self.assertTrue(self.app.is_runnable("st"))            # alias for status
        self.assertFalse(self.app.is_runnable("sto"))

    def test_source_option_removed(self):
        self.assertNotIn("--source", self.labels("start 1 "))
        self.assertNotIn("source", self.labels("set "))


class EditorLogicTests(unittest.TestCase):
    def editor(self, completer, runnable=lambda t: False):
        e = lineedit.LineEditor(completer)
        e.runnable = runnable
        return e

    def type(self, e, text):
        e.buf, e.pos = list(text), len(text)
        e._refresh_menu()

    def test_enter_on_partial_word_accepts_instead_of_running(self):
        e = self.editor(lambda t: [lineedit.Suggestion("stop ", "stop", "Stop everything")])
        self.type(e, "sto")
        self.assertTrue(e._should_accept_on_enter())      # never silently runs 'stop'
        e._accept()
        self.assertEqual(e.text, "stop ")

    def test_enter_runs_complete_command(self):
        e = self.editor(lambda t: [lineedit.Suggestion("start 1 ", "1", "US")],
                        runnable=lambda t: t.startswith("start"))
        self.type(e, "start ")
        self.assertFalse(e._should_accept_on_enter())

    def test_menu_hidden_when_it_only_repeats_input(self):
        e = self.editor(lambda t: [lineedit.Suggestion("status ", "status")])
        self.type(e, "status ")
        self.assertEqual(e.menu, [])

    def test_escape_closes_menu_until_text_changes(self):
        e = self.editor(lambda t: [lineedit.Suggestion("start ", "start")])
        self.type(e, "sta")
        self.assertTrue(e.menu)
        e.menu_closed_for = e.text
        e._refresh_menu()
        self.assertEqual(e.menu, [])

    def test_history_excludes_passwords(self):
        with tempfile.TemporaryDirectory() as d:
            e = lineedit.LineEditor(lambda t: [], Path(d) / "h.txt")
            e.remember("set password hunter22")
            e.remember("start 1")
            self.assertEqual(e.history, ["start 1"])
            self.assertNotIn("hunter22", (Path(d) / "h.txt").read_text(encoding="utf-8"))

    def test_render_structure(self):
        e = self.editor(lambda t: [lineedit.Suggestion("start ", "start [profile]", "Connect"),
                                   lineedit.Suggestion("status ", "status", "Show")])
        e.active = True
        self.type(e, "st")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            e.redraw()
        out = buf.getvalue()
        self.assertIn("> st", out)
        self.assertIn("start [profile]", out)
        self.assertIn("Connect", out)
        self.assertIn("\x1b[J", out)          # clears leftovers of a longer previous menu
        e.active = False
        ui.set_editor(None)


if __name__ == "__main__":
    unittest.main()
