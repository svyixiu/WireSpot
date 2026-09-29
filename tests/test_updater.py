"""Checking for updates and downloading them, with GitHub replaced by fakes (no network)."""
import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from wirespot import paths, updater

PAYLOAD = b"MZ" + b"new WireSpot" * 5000
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
DOWNLOAD = "https://github.com/svyixiu/WireSpot/releases/download/v9.1.0/WireSpot.exe"


def release(tag="v9.1.0", url=DOWNLOAD, digest=f"sha256:{PAYLOAD_SHA}", notes="**New**\n- Something.", assets=None):
    if assets is None:
        assets = [{"name": "WireSpot.exe", "size": len(PAYLOAD), "browser_download_url": url, "digest": digest}]
    return {"tag_name": tag, "body": notes, "published_at": "2026-09-30T00:00:00Z",
            "html_url": "https://github.com/svyixiu/WireSpot/releases/tag/" + tag, "assets": assets}


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def serve(api: dict | None = None, file: bytes = PAYLOAD):
    """urlopen that answers the API with ``api`` and downloads with ``file``."""
    def urlopen(req, timeout=None):
        if req.full_url == updater.API:
            return FakeResponse(json.dumps(api).encode())
        return FakeResponse(file)
    return mock.patch("urllib.request.urlopen", side_effect=urlopen)


class VersionTests(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(updater.parse_version("v0.4.1"), (0, 4, 1))
        self.assertEqual(updater.parse_version("1.2"), (1, 2, 0))
        self.assertGreater(updater.parse_version("v0.10.0"), updater.parse_version("0.9.9"))
        for bad in ("", "latest", "v1.x", "1.2.3.4"):
            with self.assertRaises(ValueError):
                updater.parse_version(bad)

    def test_checksum_in_notes(self):
        notes = f"Download it below.\n\nSHA-256: `{PAYLOAD_SHA.upper()}`\n"
        self.assertEqual(updater.sha256_in_notes(notes), PAYLOAD_SHA)
        self.assertIsNone(updater.sha256_in_notes("no checksum"))
        self.assertIsNone(updater.sha256_in_notes("SHA-256: `abc123`"))


class CheckTests(unittest.TestCase):
    def test_newer_release_is_offered(self):
        with serve(release()):
            info = updater.check(current="0.4.1")
        self.assertTrue(info["available"])
        self.assertEqual((info["latest"], info["sha256"], info["size"]), ("9.1.0", PAYLOAD_SHA, len(PAYLOAD)))
        # the app only sees the public part: no download address or checksum
        self.assertEqual(set(updater.public(info)), set(updater.PUBLIC))
        self.assertNotIn("url", updater.public(info))

    def test_same_or_older_is_not(self):
        with serve(release(tag="v0.4.1")):
            self.assertFalse(updater.check(current="0.4.1")["available"])
        with serve(release(tag="v0.3.0")):
            self.assertFalse(updater.check(current="0.4.1")["available"])

    def test_checksum_from_notes_when_github_has_none(self):
        with serve(release(digest="", notes=f"SHA-256: `{PAYLOAD_SHA}`")):
            self.assertEqual(updater.check(current="0.4.1")["sha256"], PAYLOAD_SHA)

    def test_refuses_without_a_checksum(self):
        with serve(release(digest="", notes="nothing")), self.assertRaises(updater.UpdateError):
            updater.check(current="0.4.1")

    def test_refuses_files_from_elsewhere(self):
        with serve(release(url="https://example.com/WireSpot.exe")), self.assertRaises(updater.UpdateError):
            updater.check(current="0.4.1")

    def test_release_without_the_exe(self):
        with serve(release(assets=[])), self.assertRaises(updater.UpdateError):
            updater.check(current="0.4.1")


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(paths, "PROGRAMDATA_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        with serve(release()):
            self.info = updater.check(current="0.4.1")

    def test_downloads_and_checks_the_file(self):
        seen = []
        with serve(file=PAYLOAD):
            path = updater.download(self.info, lambda d, t: seen.append((d, t)), threading.Event())
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertEqual(path.name, "WireSpot-9.1.0.exe")
        self.assertEqual(path.parent, updater.updates_dir())
        self.assertEqual(seen[-1], (len(PAYLOAD), len(PAYLOAD)))
        self.assertEqual(list(updater.updates_dir().glob("*.part")), [])

    def test_a_different_file_is_thrown_away(self):
        with serve(file=PAYLOAD + b"tampered"), self.assertRaises(updater.UpdateError):
            updater.download(self.info, lambda d, t: None, threading.Event())
        self.assertEqual(list(updater.updates_dir().iterdir()), [])

    def test_cancel(self):
        cancel = threading.Event()
        cancel.set()
        with serve(file=PAYLOAD), self.assertRaises(updater.Cancelled):
            updater.download(self.info, lambda d, t: None, cancel)
        self.assertEqual(list(updater.updates_dir().iterdir()), [])

    def test_clean_downloads(self):
        with serve(file=PAYLOAD):
            updater.download(self.info, lambda d, t: None, threading.Event())
        updater.clean_downloads()
        self.assertFalse(updater.updates_dir().exists())


class BridgeTests(unittest.TestCase):
    """The app's requests: the check answers from its own thread, the download reports by events."""

    def setUp(self):
        from wirespot import bridge

        self.sent = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patcher in (mock.patch.object(paths, "PROGRAMDATA_DIR", Path(self.tmp.name)),
                        mock.patch.object(bridge, "send", side_effect=self.sent.append),
                        mock.patch.object(bridge.log, "event")):
            patcher.start()
            self.addCleanup(patcher.stop)
        # no Controller: only the parts the update requests use
        b = object.__new__(bridge.Bridge)
        b._rid, b._update, b._update_thread, b._update_cancel = None, None, None, threading.Event()
        self.b = b

    def wait_for(self, pred, timeout=5.0):
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(pred(m) for m in list(self.sent)):
                return
            time.sleep(0.02)
        self.fail(f"not sent: {self.sent}")

    def test_check_then_download(self):
        with serve(release(), file=PAYLOAD):
            self.b._handle_request({"id": 7, "method": "update_check"})
            self.wait_for(lambda m: m.get("id") == 7)
            reply = next(m for m in self.sent if m.get("id") == 7)
            self.assertTrue(reply["result"]["available"])
            self.assertNotIn("url", reply["result"])          # the address stays in the engine
            self.b._handle_request({"id": 8, "method": "update_download"})
            self.wait_for(lambda m: m.get("event") == "update_ready")
        ready = next(m for m in self.sent if m.get("event") == "update_ready")
        self.assertEqual(Path(ready["data"]["path"]).read_bytes(), PAYLOAD)
        self.assertTrue(any(m.get("event") == "update_progress" for m in self.sent))
        self.assertEqual(next(m for m in self.sent if m.get("id") == 8)["result"], True)

    def test_download_needs_a_check_first(self):
        self.b._handle_request({"id": 9, "method": "update_download"})
        self.assertIn("Check for updates first", next(m for m in self.sent if m.get("id") == 9)["error"])

    def test_check_errors_come_back_as_errors(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.b._handle_request({"id": 10, "method": "update_check"})
            self.wait_for(lambda m: m.get("id") == 10)
        self.assertIn("Couldn't reach GitHub", next(m for m in self.sent if m.get("id") == 10)["error"])


class AcceptanceTests(unittest.TestCase):
    def test_previous_acceptance_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            self.assertIsNone(updater.previous_acceptance(dest))
            accepted = {"terms": "3", "privacy": "2", "at": "2026-09-29T12:00:00+02:00"}
            (dest / paths.INSTALL_MARKER).write_text(json.dumps({"accepted": accepted}), encoding="utf-8")
            self.assertEqual(updater.previous_acceptance(dest), accepted)


if __name__ == "__main__":
    unittest.main()
