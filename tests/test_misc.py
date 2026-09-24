import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import key, proton_conf
from wirespot import clients, inbox, log, state, winexec, wireguard, wlan


class WireGuardDumpTests(unittest.TestCase):
    def test_private_and_preshared_keys_dropped(self):
        priv, pub, peer, psk = key(), key(), key(), key()
        dump = (f"{priv}\t{pub}\t51820\toff\n"
                f"{peer}\t{psk}\t149.40.62.21:51820\t0.0.0.0/1,128.0.0.0/1\t{int(time.time()) - 5}\t1024\t2048\t25\n")
        st = wireguard.parse_dump(dump)
        self.assertEqual(st.public_key, pub)
        p = st.peers[0]
        self.assertEqual((p.endpoint, p.rx, p.tx), ("149.40.62.21:51820", 1024, 2048))
        self.assertTrue(p.has_preshared_key)
        self.assertLess(p.handshake_age(), 60)
        blob = repr(st)
        self.assertNotIn(priv, blob)
        self.assertNotIn(psk, blob)

    def test_never_handshaked(self):
        st = wireguard.parse_dump(f"{key()}\t{key()}\t1\toff\n{key()}\t(none)\t(none)\t0.0.0.0/0\t0\t0\t0\toff\n")
        self.assertIsNone(st.peers[0].handshake_age())
        self.assertEqual(wireguard.fmt_age(None), "never")

    def test_empty(self):
        self.assertIsNone(wireguard.parse_dump(""))


class StateMachineTests(unittest.TestCase):
    def test_happy_path_and_invalid_jump(self):
        m = state.Machine(state.RelayRecord(), None)
        for s in ("VPN_CONNECTING", "VPN_CONNECTED", "HOTSPOT_STARTING", "HOTSPOT_ACTIVE", "SHARING_CONFIGURING", "READY"):
            m.to(state.State(s))
        self.assertEqual(m.state, state.State.READY)
        m2 = state.Machine(state.RelayRecord(), None)
        with self.assertRaises(state.InvalidTransition):
            m2.to(state.State.READY)          # cannot skip from DISCONNECTED to READY

    def test_record_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            r = state.RelayRecord(state="READY", tunnel_name="ws_x", ics_guids=["a", "b"])
            r.save(p)
            r2 = state.RelayRecord.load(p)
            self.assertEqual((r2.state, r2.tunnel_name, r2.ics_guids), ("READY", "ws_x", ["a", "b"]))
            p.write_text('{"state": "BOGUS", "unknown_field": 1}', encoding="utf-8")
            self.assertEqual(state.RelayRecord.load(p).state, "ERROR")

    def test_transaction_layers_and_order(self):
        done = []
        tx = state.Transaction()
        tx.add("vpn", "a", lambda: done.append("vpn-a"))
        tx.add("share", "b", lambda: done.append("share-b"))
        tx.add("share", "c", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        tx.add("share", "d", lambda: done.append("share-d"))
        errors = tx.rollback({"share"})
        self.assertEqual(done, ["share-d", "share-b"])     # reverse order, continues past failure
        self.assertEqual(len(errors), 1)
        tx.rollback()
        self.assertEqual(done[-1], "vpn-a")
        self.assertEqual(tx.stack, [])


class ClientTests(unittest.TestCase):
    DATA = {
        "tethering": [
            {"mac": "3a:11:22:33:44:55", "hostnames": [{"name": "Galaxy-S23", "type": "DomainName"},
                                                        {"name": "192.168.137.45", "type": "Ipv4"}]},
            {"mac": "F0:18:98:01:02:03", "hostnames": [{"name": "Julys-iPhone", "type": "DomainName"}]},
        ],
        "neighbors": [
            {"ip": "192.168.137.12", "mac": "F0-18-98-01-02-03", "state": "Reachable"},
            {"ip": "192.168.137.200", "mac": "04-03-D6-AA-BB-CC", "state": "Stale", "ptr": "switch.mshome.net"},
            {"ip": "192.168.137.255", "mac": "FF-FF-FF-FF-FF-FF", "state": "Permanent"},
        ],
    }

    def test_merge_and_guess(self):
        cl = clients.merge(self.DATA)
        self.assertEqual([c.ip for c in cl], ["192.168.137.12", "192.168.137.45", "192.168.137.200"])
        iphone, galaxy, switch = cl
        self.assertEqual(iphone.device, "iPhone")
        self.assertEqual(iphone.vendor, "Apple")
        self.assertEqual(galaxy.device, "Samsung Galaxy")
        self.assertTrue(galaxy.randomized)
        self.assertEqual(galaxy.vendor, "")
        self.assertEqual(switch.device, "Nintendo Switch")
        self.assertEqual(switch.display_name, "switch")

    def test_private_mac_without_name(self):
        self.assertEqual(clients.guess_type([], "DA:00:00:00:00:01"), "Phone/tablet (private MAC)")
        self.assertEqual(clients.guess_type([], "00:11:22:33:44:55"), "Unknown")


class InboxTests(unittest.TestCase):
    def test_path_from_input(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "My Profile.conf"
            p.write_text("x", encoding="utf-8")
            self.assertEqual(inbox.path_from_input(f'"{p}"'), p)
            self.assertEqual(inbox.path_from_input(f"& '{p}'"), p)
            self.assertIsNone(inbox.path_from_input("start 1"))
            self.assertIsNone(inbox.path_from_input(str(Path(d) / "missing.conf")))

    def test_scan_detects_new_wireguard_file_once_stable(self):
        with tempfile.TemporaryDirectory() as watch, tempfile.TemporaryDirectory() as vpn:
            ib = inbox.Inbox(vpn_dir=Path(vpn), watch_dirs=[Path(watch)])
            ib.declined = set()
            (Path(watch) / "notes.conf").write_text("[section]\nfoo=bar", encoding="utf-8")
            (Path(watch) / "US-FREE-7.conf").write_text(proton_conf("US-FREE#7"), encoding="utf-8")
            self.assertEqual(ib.scan_once(), [])                 # first sight: size recorded
            found = ib.scan_once()
            self.assertEqual([c.path.name for c in found], ["US-FREE-7.conf"])
            self.assertEqual(ib.scan_once(), [])                 # not reported twice
            self.assertEqual(len(ib.take_pending()), 1)

    def test_import_move_and_name_conflict(self):
        with tempfile.TemporaryDirectory() as watch, tempfile.TemporaryDirectory() as vpn:
            ib = inbox.Inbox(vpn_dir=Path(vpn), watch_dirs=[])
            (Path(vpn) / "a.conf").write_text("existing", encoding="utf-8")
            src = Path(watch) / "a.conf"
            src.write_text(proton_conf(), encoding="utf-8")
            dest = ib.import_file(src, move=True)
            self.assertEqual(dest.name, "a-2.conf")
            self.assertFalse(src.exists())

    def test_looks_like_wireguard(self):
        self.assertTrue(inbox.looks_like_wireguard(proton_conf().encode()))
        self.assertFalse(inbox.looks_like_wireguard(b"[Interface]\nfoo"))


class WinexecTests(unittest.TestCase):
    def test_split_param_block(self):
        head, rest = winexec.split_param_block("\nparam([string]$A = ')', [int]$B)\n$x = 1")
        self.assertEqual(head.strip(), "param([string]$A = ')', [int]$B)")
        self.assertEqual(rest.strip(), "$x = 1")
        self.assertEqual(winexec.split_param_block("$x = 1"), ("", "$x = 1"))

    def test_json_marker(self):
        out = "noise\n@@WSJSON@@{\"ok\": true, \"n\": 1}\nmore noise"
        self.assertEqual(winexec.parse_json_marker(out), {"ok": True, "n": 1})
        self.assertIsNone(winexec.parse_json_marker("no marker"))


class WlanTests(unittest.TestCase):
    DRIVERS = """
Interface name: Wi-Fi

    Driver                    : Intel(R) Wi-Fi 6 AX201 160MHz
    Vendor                    : Intel Corporation
    Provider                  : Intel
    Date                      : 6/11/2026
    Version                   : 24.60.0.3
    Radio types supported     : 802.11b 802.11g 802.11n 802.11a 802.11ac 802.11ax
    Hosted network supported  : No
    Authentication and cipher supported in infrastructure mode:
                                WPA2-Personal    CCMP
                                WPA3-Personal    CCMP
    Number of supported bands : 2
                                2.4 GHz [ 0 MHz - 0 MHz]
                                5 GHz   [ 0 MHz - 0 MHz]
    IHV service present       : Yes
"""

    def test_parse_drivers(self):
        d = wlan.parse_netsh_drivers(self.DRIVERS)[0]
        self.assertEqual((d.version, d.hosted_network, d.wpa3_personal), ("24.60.0.3", "No", True))
        self.assertEqual(d.bands, ["2.4 GHz", "5 GHz"])

    def test_band_from_channel(self):
        self.assertEqual(wlan.band_from_channel(2), "2.4")
        self.assertEqual(wlan.band_from_channel(40), "5")
        self.assertEqual(wlan.band_from_channel(37, "6 GHz"), "6")
        self.assertEqual(wlan.band_from_channel(0), "")


class LogRedactionTests(unittest.TestCase):
    def test_redacts_keys_and_registered_secrets(self):
        k = key()
        log.register_secret("88888888")
        text = log.redact(f"PrivateKey = {k}\npassword is 88888888\nprivate key: {k}")
        self.assertNotIn(k, text)
        self.assertNotIn("88888888", text)

    def test_log_file_never_contains_secret(self):
        with tempfile.TemporaryDirectory() as d:
            path = log.enable(Path(d))
            try:
                k = key()
                log.register_secret("hunter2hunter2")
                log.event("info", f"conf PrivateKey = {k} pass hunter2hunter2")
            finally:
                log.disable()
            content = path.read_text(encoding="utf-8")
            self.assertNotIn(k, content)
            self.assertNotIn("hunter2hunter2", content)


if __name__ == "__main__":
    unittest.main()
