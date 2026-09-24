import json
import tempfile
import unittest
from pathlib import Path

from wirespot import settings


class SettingsTests(unittest.TestCase):
    LEGACY = {
        "hotspot": {"ssid": "JulyVPN", "password": "88888888", "security": "wpa2", "band": "5"},
        "vpn": {"default_profile": "wg-US-FREE-5.conf", "protection": "strict", "wireguard_path": ""},
        "behavior": {"auto_start_hotspot": True, "auto_bind_ics": True},
    }

    def test_legacy_file_migrates_unchanged(self):
        s, notes = settings.migrate(self.LEGACY)
        self.assertEqual(s["hotspot"]["ssid"], "JulyVPN")
        self.assertEqual(s["hotspot"]["band"], "5")
        self.assertEqual(s["vpn"]["default_profile"], "wg-US-FREE-5.conf")
        self.assertEqual(s["hotspot"]["source"], "vpn")
        self.assertTrue(s["behavior"]["dns_lock"])
        self.assertEqual(s["schema"], settings.SCHEMA)
        self.assertEqual(notes, [])

    def test_loose_values_normalised(self):
        s, notes = settings.migrate({"hotspot": {"band": 5, "security": "WPA2"}, "behavior": {"guard": "off"}})
        self.assertEqual(s["hotspot"]["band"], "5")
        self.assertEqual(s["hotspot"]["security"], "wpa2")
        self.assertFalse(s["behavior"]["guard"])
        self.assertEqual(settings.normalize_band("2.4GHz"), "2.4")
        self.assertEqual(settings.normalize_band("5 GHz"), "5")

    def test_invalid_values_fall_back_with_notes(self):
        s, notes = settings.migrate({"hotspot": {"band": "7"}, "vpn": {"protection": "paranoid"}})
        self.assertEqual(s["hotspot"]["band"], "auto")
        self.assertEqual(s["vpn"]["protection"], "balanced")
        self.assertEqual(len(notes), 2)

    def test_wifi_source_is_retired(self):
        s, notes = settings.migrate({"hotspot": {"source": "wifi"}})
        self.assertEqual(s["hotspot"]["source"], "vpn")
        self.assertTrue(any("no longer supported" in n for n in notes))

    def test_unknown_keys_preserved(self):
        s, _ = settings.migrate({"custom": {"x": 1}, "hotspot": {"note": "hi"}})
        self.assertEqual(s["custom"], {"x": 1})
        self.assertEqual(s["hotspot"]["note"], "hi")

    def test_validate(self):
        s, _ = settings.migrate(self.LEGACY)
        self.assertEqual(settings.validate_hotspot(s), [])
        s["hotspot"]["password"] = "short"
        self.assertTrue(any("8-63" in p for p in settings.validate_hotspot(s)))
        s["hotspot"]["password"] = "pässwörd123"
        self.assertTrue(any("ASCII" in p for p in settings.validate_hotspot(s)))

    def test_redacted(self):
        s, _ = settings.migrate(self.LEGACY)
        r = settings.redacted(s)
        self.assertNotIn("88888888", json.dumps(r))
        self.assertEqual(s["hotspot"]["password"], "88888888")

    def test_roundtrip_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            s, _ = settings.migrate(self.LEGACY)
            settings.save(s, p)
            s2, _ = settings.load(p)
            self.assertEqual(s, s2)
            p.write_text("{not json", encoding="utf-8")
            s3, notes = settings.load(p)
            self.assertEqual(s3["hotspot"]["ssid"], "WireSpot")
            self.assertTrue((Path(d) / "settings.json.bad").exists())
            self.assertTrue(notes)


if __name__ == "__main__":
    unittest.main()
