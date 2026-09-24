import tempfile
import unittest
from pathlib import Path

from tests.helpers import key, proton_conf
from wirespot import profiles
from wirespot.profiles import ConfigError


class ParseTests(unittest.TestCase):
    def test_proton_config_parses(self):
        cfg = profiles.parse_conf(proton_conf())
        self.assertEqual(cfg.interface.values["address"], "10.2.0.2/32")
        self.assertEqual(cfg.peers[0].values["endpoint"], "149.40.62.21:51820")
        self.assertEqual(cfg.peers[0].values["persistentkeepalive"], "25")
        self.assertIn("US-FREE#5", cfg.peers[0].comments)

    def test_rejects_lifecycle_hooks(self):
        for hook in ("PreUp", "PostUp", "PreDown", "postdown"):
            with self.assertRaisesRegex(ConfigError, "hook"):
                profiles.parse_conf(proton_conf(extra_iface=f"{hook} = calc.exe"))

    def test_rejects_unknown_keys_and_sections(self):
        with self.assertRaisesRegex(ConfigError, "unsupported key"):
            profiles.parse_conf(proton_conf(extra_iface="Table = off"))
        with self.assertRaisesRegex(ConfigError, "unknown section"):
            profiles.parse_conf(proton_conf() + "\n[Evil]\nx = 1")

    def test_rejects_bad_keys_and_addresses(self):
        with self.assertRaisesRegex(ConfigError, "PrivateKey"):
            profiles.parse_conf(proton_conf().replace("PrivateKey = ", "PrivateKey = x", 1))
        with self.assertRaisesRegex(ConfigError, "AllowedIPs"):
            profiles.parse_conf(proton_conf(allowed="0.0.0.0/0, banana"))
        with self.assertRaisesRegex(ConfigError, "Endpoint"):
            profiles.parse_conf(proton_conf(endpoint="1.2.3.4:99999"))

    def test_structure_requirements(self):
        with self.assertRaisesRegex(ConfigError, "exactly one"):
            profiles.parse_conf(f"[Peer]\nPublicKey = {key()}\nAllowedIPs = 0.0.0.0/0\n")
        with self.assertRaisesRegex(ConfigError, "at least one"):
            profiles.parse_conf(f"[Interface]\nPrivateKey = {key()}\nAddress = 10.0.0.2/32\n")
        with self.assertRaisesRegex(ConfigError, "NUL"):
            profiles.parse_conf("[Interface]\x00")

    def test_ipv6_endpoint(self):
        self.assertEqual(profiles.parse_endpoint("[2001:db8::1]:51820"), ("2001:db8::1", 51820))
        self.assertEqual(profiles.parse_endpoint("nl.example.net:51820"), ("nl.example.net", 51820))

    def test_inline_comment_and_blank_lines(self):
        text = proton_conf(extra_peer="\n\n").replace("DNS = 10.2.0.1", "DNS = 10.2.0.1 # proton")
        cfg = profiles.parse_conf(text)
        self.assertEqual(cfg.interface.values["dns"], "10.2.0.1")


class MetadataTests(unittest.TestCase):
    def detect(self, comment, stem="x"):
        return profiles.detect_server([comment] if comment else [], stem)

    def test_free_server(self):
        s = self.detect("US-FREE#5")
        self.assertEqual((s.name, s.country_code, s.free), ("US-FREE#5", "US", True))
        self.assertEqual(self.detect("NL-FREE#128").country_code, "NL")

    def test_city_server_is_not_canada(self):
        self.assertEqual(self.detect("US-CA#88").country_code, "US")
        self.assertEqual(self.detect("US-NY#12").country_code, "US")

    def test_secure_core(self):
        s = self.detect("CH-US#1")
        self.assertEqual((s.country_code, s.entry_country_code), ("US", "CH"))

    def test_from_filename(self):
        s = self.detect("", "wg-US-FREE-5")
        self.assertEqual((s.name, s.country_code), ("US-FREE#5", "US"))
        self.assertEqual(self.detect("", "jp-free").country_code, "JP")

    def test_profile_label_and_pubkey(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "wg-US-FREE-5.conf"
            p.write_text(proton_conf(), encoding="utf-8")
            prof = profiles.load_profile(p)
            self.assertIn("United States", prof.label)
            self.assertTrue(prof.full_tunnel)
            self.assertEqual(len(prof.public_key), 44)
            self.assertEqual(prof.features[0], "Bouncing = 2")


class TransformTests(unittest.TestCase):
    def test_split_default_routes(self):
        self.assertEqual(profiles.split_default_routes("0.0.0.0/0, ::/0"), "0.0.0.0/1, 128.0.0.0/1, ::/1, 8000::/1")
        self.assertEqual(profiles.split_default_routes("10.0.0.0/8"), "10.0.0.0/8")

    def test_runtime_config_is_canonical(self):
        cfg = profiles.parse_conf(proton_conf())
        strict = profiles.render_runtime_config(cfg, "strict")
        balanced = profiles.render_runtime_config(cfg, "balanced")
        self.assertNotIn("#", strict)
        self.assertIn("AllowedIPs = 0.0.0.0/0, ::/0", strict)
        self.assertIn("AllowedIPs = 0.0.0.0/1, 128.0.0.0/1, ::/1, 8000::/1", balanced)
        self.assertFalse(profiles.has_default_route(["0.0.0.0/1", "128.0.0.0/1"]))
        profiles.parse_conf(balanced)  # round-trips through the strict parser

    def test_tunnel_name(self):
        n = profiles.tunnel_name(Path("C:/x/vpn/wg-US-FREE-5.conf"))
        self.assertTrue(n.startswith("ws_wg-US-FREE-5_"))
        self.assertLessEqual(len(n), 32)
        self.assertRegex(n, r"^[A-Za-z0-9_=+.-]+$")
        long = profiles.tunnel_name(Path("C:/x/" + "é" * 80 + ".conf"))
        self.assertLessEqual(len(long), 32)
        self.assertRegex(long, r"^[A-Za-z0-9_=+.-]+$")

    def test_x25519_rfc7748_vector(self):
        priv = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        self.assertEqual(profiles.x25519_public(priv).hex(),
                         "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")

    def test_resolve_profile(self):
        with tempfile.TemporaryDirectory() as d:
            for name, server in (("a-US-FREE-5.conf", "US-FREE#5"), ("b-NL-FREE-1.conf", "NL-FREE#1")):
                (Path(d) / name).write_text(proton_conf(server=server), encoding="utf-8")
            (Path(d) / "bad.conf").write_text("[Interface]\nPostUp = x", encoding="utf-8")
            good, bad = profiles.list_profiles(Path(d))
            self.assertEqual(len(good), 2)
            self.assertEqual(bad[0][0].name, "bad.conf")
            self.assertEqual(profiles.resolve_profile("2", good).server_name, "NL-FREE#1")
            self.assertEqual(profiles.resolve_profile("us-free#5", good).path.name, "a-US-FREE-5.conf")
            self.assertEqual(profiles.resolve_profile(None, good, "b-NL-FREE-1.conf").server_name, "NL-FREE#1")
            self.assertIsNone(profiles.resolve_profile("9", good))
            self.assertIsNone(profiles.resolve_profile("free", good))  # ambiguous


if __name__ == "__main__":
    unittest.main()
