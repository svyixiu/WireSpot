"""Transaction / rollback / state-machine behaviour of `start` and `stop`
against a fake Windows backend."""
import contextlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import Capture, key, proton_conf
from tests.test_netid_ics import WFD2, WG, WIFI, hotspot_on
from wirespot import netid, paths, relay, settings as settings_mod
from wirespot.backend import EnvReport
from wirespot.hotspot import HotspotResult
from wirespot.state import State
from wirespot.winexec import Result
from wirespot.wireguard import PeerStatus, TunnelService, WgStatus
from wirespot.wlan import WlanInterface

OK_START = HotspotResult(True, status="Success", state_after="On", applied_band="5",
                         source={"name": "ws_test_abc123", "kind": "vpn", "adapter": WG})
DEVICE_OFF = HotspotResult(False, status="WiFiDeviceOff", status_code=3, state_after="Off", code="start_status")


class FakeBackend:
    def __init__(self, *, uplink_band="5", starts=None, ics_flags=None, handshake_ok=True, ethernet=False):
        self.ethernet = ethernet
        self.calls: list[tuple] = []
        self.uplink_channel = 40 if uplink_band == "5" else 6
        self.starts = list(starts or [OK_START])
        self.ics_flags = ics_flags or []
        self.handshake_ok = handshake_ok
        self.installed: set[str] = set()
        self.hotspot_on = False

    def _c(self, *a):
        self.calls.append(a)

    def names(self):
        return [c[0] for c in self.calls]

    def env(self, wireguard_path=""):
        return EnvReport(True, 22631, True, Path("C:/wg/wireguard.exe"), Path("C:/wg/wg.exe"))

    def wlan(self):
        if self.ethernet:
            return [WlanInterface(WIFI, "Intel AX201", "disconnected", "on", "on")], ""
        return [WlanInterface(WIFI, "Intel AX201", "connected", "on", "on", channel=self.uplink_channel,
                              ssid="Home", signal=80)], ""

    def uplink(self):
        if self.ethernet:
            return [{"name": "Ethernet", "description": "Realtek PCIe GbE Family Controller", "media": "802.3",
                     "hardware": True, "gateway": "192.168.0.1", "ifindex": 16}]
        return [{"name": "Wi-Fi", "description": "Intel(R) Wi-Fi 6 AX201 160MHz", "media": "Native 802.11",
                 "hardware": True, "gateway": "192.168.1.1", "ifindex": 13}]

    def hotspot_status(self, tunnel_guid="", wifi_guid="", source="any"):
        self._c("hotspot_status", source)
        kind = "vpn" if source == "vpn" else "wifi"
        return {"ok": True, "state": "On" if self.hotspot_on else "Off", "max_clients": 8,
                "source": {"name": "x", "kind": kind, "capability": "Enabled", "level": "InternetAccess"},
                "bands": {"TwoPointFourGigahertz": True, "FiveGigahertz": True}, "auth_kinds": {}}, ""

    def wg_services(self):
        return [TunnelService(n, "Running", "Auto", "") for n in sorted(self.installed)], ""

    def write_runtime(self, profile, protection):
        self._c("write_runtime", protection)
        return Path("C:/ProgramData/WireSpot/runtime/ws_test.conf"), "ws_test_abc123"

    def remove_runtime(self, name):
        self._c("remove_runtime", name)

    def wg_install(self, wgx, conf):
        self._c("wg_install")
        self.installed.add("ws_test_abc123")
        return Result(0)

    def wg_uninstall(self, wgx, name):
        self._c("wg_uninstall", name)
        self.installed.discard(name)
        return Result(0)

    def adapter(self, name="", guid=""):
        if name:
            return {"name": name, "status": "Up", "ifindex": 47, "guid": WG, "ipv4": ["10.2.0.2"], "dns": ["10.2.0.1"]}
        return {"name": "Local Area Connection* 2", "status": "Up", "ifindex": 20, "guid": guid, "ipv4": ["192.168.137.1"]}

    def wg_handshake(self, wg, name, timeout=20.0):
        if not self.handshake_ok:
            return None, "no handshake with the VPN server yet"
        return WgStatus(key(), "51820", [PeerStatus(key(), "149.40.62.21:51820", ["0.0.0.0/1"], int(time.time()), 10, 20, "25")]), ""

    def route_check(self, targets=("1.1.1.1", "9.9.9.9")):
        return {"ok": True, "routes": [{"target": t, "ifindex": 47, "alias": "ws_test_abc123"} for t in targets]}

    def hotspot_start(self, **kw):
        self._c("hotspot_start", kw["band"], kw["source"])
        r = self.starts.pop(0) if self.starts else OK_START
        self.hotspot_on = r.ok
        return r

    def hotspot_stop(self, tunnel_guid="", wifi_guid=""):
        self._c("hotspot_stop")
        self.hotspot_on = False
        return HotspotResult(True, state_after="Off")

    def hotspot_timeout(self, enable):
        self._c("hotspot_timeout", enable)
        return {"ok": True, "before": True, "after": enable}

    def inventory(self, with_ics=True):
        d = hotspot_on("none")
        d.pop("ics")                      # Mobile Hotspot: no classic ICS flags
        d["ics_flags"] = self.ics_flags
        return netid.build_inventory(d)

    def ics_disable(self, guids):
        self._c("ics_disable", tuple(guids))
        self.ics_flags = []
        return True, ""

    def dns_lock(self, action, servers=None):
        self._c("dns_lock", action)
        return {"ok": True, "effective": [{"namespace": "."}], "removed": ["x"] if action == "remove" else []}

    def public_ip(self):
        return "149.40.62.24"


class RelayTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "vpn").mkdir()
        (base / "vpn" / "wg-US-FREE-5.conf").write_text(proton_conf(), encoding="utf-8")
        self.patch = mock.patch.object(paths, "VPN_DIR", base / "vpn")
        self.patch.start()
        self.settings, _ = settings_mod.migrate({
            "hotspot": {"ssid": "JulyVPN", "password": "88888888", "security": "wpa2", "band": "5"},
            "vpn": {"protection": "balanced"}})
        self.saved = []
        self.answers: list[str] = []
        self.questions: list[str] = []

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def decide(self, question, choices, default=0, cancel=None):
        self.questions.append(question)
        keys = [c.key for c in choices]
        ans = self.answers.pop(0) if self.answers else keys[default]
        self.assertIn(ans, keys, f"scripted answer {ans!r} not offered for {question!r}: {keys}")
        return ans

    def relay(self, backend):
        return relay.Relay(backend, decide=self.decide, state_path=Path(self.tmp.name) / "state.json",
                           save_settings=lambda s: self.saved.append(dict(s["vpn"])), sleep=lambda s: None)

    def run_start(self, r, token="1", opts=None):
        with Capture() as cap, contextlib.redirect_stdout(io.StringIO()):
            ok = r.start(self.settings, token, opts)
        return ok, cap.text()


class StartTests(RelayTestCase):
    def test_happy_path_hotspot_sourced_from_tunnel_no_ics_writes(self):
        b = FakeBackend()
        r = self.relay(b)
        ok, out = self.run_start(r)
        self.assertTrue(ok, out)
        self.assertEqual(r.m.state, State.READY)
        self.assertEqual([c[2] for c in b.calls if c[0] == "hotspot_start"], ["vpn"])
        self.assertNotIn("ics_disable", b.names())
        self.assertIn("tethering source = tunnel", out)
        self.assertIn(("dns_lock", "add"), b.calls)
        self.assertIn("READY", out)
        self.assertIn("Hotspot interface identified", out)
        self.assertIn(WFD2, out)
        r.stop_guard()

    def test_wifideviceoff_stops_before_ics_and_keeps_vpn(self):
        b = FakeBackend(starts=[DEVICE_OFF])
        r = self.relay(b)
        self.answers = ["vpn-only"]
        ok, out = self.run_start(r)
        self.assertFalse(ok)
        self.assertEqual(r.m.state, State.VPN_CONNECTED)
        self.assertNotIn("ics_disable", b.names())
        self.assertNotIn("wg_uninstall", b.names())
        self.assertIn("Mobile Hotspot could not start: WiFiDeviceOff", out)
        self.assertIn("VPN remains connected", out)
        self.assertIn("Wi-Fi radio: software on", out)             # failure context captured
        self.assertIn("Microsoft Wi-Fi Direct Virtual Adapter #4", out)
        for line in out.splitlines():  # never a success marker next to a failure status
            if "WiFiDeviceOff" in line:
                self.assertFalse(line.lstrip().startswith(("+", "✔")), line)

    def test_wifideviceoff_retry_with_auto(self):
        b = FakeBackend(starts=[DEVICE_OFF, OK_START])
        r = self.relay(b)
        self.answers = ["auto"]
        ok, out = self.run_start(r)
        self.assertTrue(ok, out)
        starts = [c for c in b.calls if c[0] == "hotspot_start"]
        self.assertEqual([c[1] for c in starts], ["5", "auto"])
        self.assertEqual(self.settings["hotspot"]["band"], "5")   # saved setting untouched
        r.stop_guard()

    def test_band_mismatch_keeps_requested_band_with_hint(self):
        b = FakeBackend(uplink_band="2.4")
        r = self.relay(b)
        ok, out = self.run_start(r)
        self.assertTrue(ok, out)
        self.assertIn("Uplink is on 2.4 GHz", out)
        self.assertEqual([c[1] for c in b.calls if c[0] == "hotspot_start"], ["5"])
        self.assertEqual(self.questions, [])
        r.stop_guard()

    def test_ethernet_uplink(self):
        b = FakeBackend(ethernet=True)
        r = self.relay(b)
        ok, out = self.run_start(r)
        self.assertTrue(ok, out)
        self.assertIn("Uplink Ethernet 'Ethernet'", out)
        self.assertIn("Wi-Fi radio is free", out)
        self.assertNotIn("Uplink is on", out)                          # no band hint without a Wi-Fi uplink
        self.assertEqual([c[1] for c in b.calls if c[0] == "hotspot_start"], ["5"])
        self.assertEqual(r.m.state, State.READY)
        r.stop_guard()

    def test_no_uplink_stops_before_touching_anything(self):
        b = FakeBackend(ethernet=True)
        b.uplink = lambda: []
        r = self.relay(b)
        ok, out = self.run_start(r)
        self.assertFalse(ok)
        self.assertIn("no internet uplink", out)
        self.assertNotIn("wg_install", b.names())

    def test_adapter_unsupported_band_offers_alternatives(self):
        b = FakeBackend()
        orig = b.hotspot_status

        def status(*a, **k):
            data, e = orig(*a, **k)
            return {**data, "bands": {"TwoPointFourGigahertz": True, "FiveGigahertz": False}}, e

        b.hotspot_status = status
        r = self.relay(b)
        ok, out = self.run_start(r)          # default answer = recommended "auto"
        self.assertTrue(ok, out)
        self.assertIn("5 GHz hotspot is unavailable", out)
        self.assertEqual([c[1] for c in b.calls if c[0] == "hotspot_start"], ["auto"])
        r.stop_guard()

    def test_strict_full_tunnel_offers_balanced_for_session(self):
        self.settings["vpn"]["protection"] = "strict"
        b = FakeBackend()
        r = self.relay(b)
        self.answers = ["once"]
        ok, out = self.run_start(r)
        self.assertTrue(ok, out)
        self.assertIn(("write_runtime", "balanced"), b.calls)
        self.assertEqual(self.settings["vpn"]["protection"], "strict")
        self.assertIn("kill-switch", out)
        r.stop_guard()

    def test_classic_ics_leak_is_cleared_with_consent(self):
        leak = [{"guid": WIFI, "name": "Wi-Fi", "public": True, "private": False, "exists": True},
                {"guid": WFD2, "name": "LAC* 2", "public": False, "private": True, "exists": True}]
        b = FakeBackend(ics_flags=leak)
        r = self.relay(b)
        ok, out = self.run_start(r)                       # default = clear it
        self.assertTrue(ok, out)
        self.assertIn(("ics_disable", (WIFI, WFD2)), b.calls)
        self.assertIn("bypass the VPN", " ".join(self.questions))
        r.stop_guard()

    def test_classic_ics_leak_declined_rolls_back_hotspot_keeps_vpn(self):
        leak = [{"guid": WIFI, "name": "Wi-Fi", "public": True, "private": False, "exists": True},
                {"guid": WFD2, "name": "LAC* 2", "public": False, "private": True, "exists": True}]
        b = FakeBackend(ics_flags=leak)
        r = self.relay(b)
        self.answers = ["stop"]
        ok, out = self.run_start(r)
        self.assertFalse(ok)
        self.assertIn("hotspot_stop", b.names())              # share layer rolled back
        self.assertNotIn("wg_uninstall", b.names())           # VPN kept
        self.assertNotIn("ics_disable", b.names())            # other software's setting untouched
        self.assertEqual(r.m.state, State.VPN_CONNECTED)

    def test_hotspot_not_sourced_from_tunnel_is_refused(self):
        wrong = HotspotResult(True, status="Success", state_after="On", applied_band="5",
                              source={"name": "Home", "kind": "wifi", "adapter": WIFI})
        b = FakeBackend(starts=[wrong])
        r = self.relay(b)
        ok, out = self.run_start(r)
        self.assertFalse(ok)
        self.assertIn("instead of the VPN", out)
        self.assertIn("hotspot_stop", b.names())
        self.assertEqual(r.m.state, State.VPN_CONNECTED)

    def test_no_wifi_fallback_when_vpn_source_unavailable(self):
        b = FakeBackend()
        orig = b.hotspot_status

        def status(*a, **k):
            data, e = orig(*a, **k)
            data["source"] = {**data["source"], "capability": "DisabledDueToUnknownCause"}
            return data, e

        b.hotspot_status = status
        r = self.relay(b)
        ok, out = self.run_start(r)          # preflight capability check fails first
        self.assertFalse(ok)
        self.assertNotIn("hotspot_start", b.names())

    def test_handshake_failure_rolls_back_everything(self):
        b = FakeBackend(handshake_ok=False)
        r = self.relay(b)
        self.answers = ["stop"]
        ok, out = self.run_start(r)
        self.assertFalse(ok)
        self.assertIn(("wg_uninstall", "ws_test_abc123"), b.calls)
        self.assertIn(("remove_runtime", "ws_test_abc123"), b.calls)
        self.assertNotIn("hotspot_start", b.names())
        self.assertEqual(r.m.state, State.DISCONNECTED)

    def test_unexpected_exception_after_vpn_rolls_back_share_only(self):
        b = FakeBackend()
        b.inventory = mock.Mock(side_effect=RuntimeError("COM blew up"))
        r = self.relay(b)
        ok, out = self.run_start(r)
        self.assertFalse(ok)
        self.assertIn("COM blew up", out)
        self.assertIn("hotspot_stop", b.names())
        self.assertNotIn("wg_uninstall", b.names())
        self.assertEqual(r.m.state, State.VPN_CONNECTED)


class StopTests(RelayTestCase):
    def test_stop_removes_only_owned_state(self):
        b = FakeBackend()
        r = self.relay(b)
        self.run_start(r)
        r.stop_guard()
        b.calls.clear()
        b.hotspot_status = lambda *a, **k: ({"ok": True, "state": "On"}, "")
        with Capture(), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(r.stop(self.settings))
        names = b.names()
        self.assertIn(("dns_lock", "remove"), b.calls)
        self.assertIn("hotspot_stop", names)
        self.assertIn(("wg_uninstall", "ws_test_abc123"), b.calls)
        self.assertEqual(r.m.state, State.DISCONNECTED)


if __name__ == "__main__":
    unittest.main()
