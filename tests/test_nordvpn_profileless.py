"""Provider identity, no-profile hosting and fail-closed ownership."""
import contextlib
import ctypes
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_relay import FakeBackend, RelayTestCase
from tests.test_netid_ics import WG
from wirespot import netid, paths, relay
from wirespot import forward_guard
from wirespot.hotspot import HotspotResult
from wirespot.nordvpn import NordVPNProvider
from wirespot.state import State
from wirespot.vpn_provider import ProviderState

NORD = "3113a8fa-5e2b-44de-bf34-fb4883c4f352"


class NordBackend(FakeBackend):
    def __init__(self, connected=True):
        super().__init__()
        self.connected = connected
        self.route_good = True
        self.starts = [HotspotResult(True, status="Success", state_after="On", applied_band="5",
                                     source={"name": "NordLynx", "kind": "vpn", "adapter": NORD})]

    def env(self, wireguard_path="", require_wireguard=True):
        self._c("env", require_wireguard)
        return super().env(wireguard_path)

    def inventory(self, with_ics=True):
        from tests.test_netid_ics import hotspot_on
        data = hotspot_on("none")
        data.pop("ics")
        data["ics_flags"] = []
        data["adapters"].append({"name": "NordLynx", "description": "NordLynx Tunnel", "status": "Up" if self.connected else "Disconnected",
                                 "ifindex": 47, "guid": NORD, "hardware": False, "virtual": True})
        data["ipv4"].append({"ifindex": 47, "ip": "10.5.0.2"})
        data.setdefault("dns", []).append({"ifindex": 47, "servers": ["103.86.96.100"]})
        return netid.build_inventory(data)

    def route_check(self, targets=("1.1.1.1", "9.9.9.9")):
        idx = 47 if self.route_good else 13
        return {"ok": True, "routes": [{"target": t, "ifindex": idx} for t in targets]}

    def hotspot_status(self, tunnel_guid="", wifi_guid="", source="any"):
        data, error = super().hotspot_status(tunnel_guid, wifi_guid, source)
        data["source"]["adapter"] = NORD if source == "vpn" else WG
        data["source"]["name"] = "NordLynx" if source == "vpn" else "Wi-Fi"
        return data, error

    def guard_tick(self, tunnel, hotspot_guid):
        return {"ok": True, "tunnel_state": "Missing", "hotspot_state": "On" if self.hotspot_on else "Off",
                "flags": [], "neighbors": [], "tethering": []}


class ProviderTests(unittest.TestCase):
    def test_wfp_filter_layout_matches_windows_sdk_on_x64(self):
        if sys.maxsize > 2**32:
            self.assertEqual(ctypes.sizeof(forward_guard.Filter), 200)
            self.assertEqual(forward_guard.Filter.reserved.offset, 168)

    def test_requires_provider_identity_and_selected_route(self):
        b = NordBackend()
        with mock.patch("wirespot.nordvpn._bound_internet", return_value=True), \
             mock.patch("wirespot.nordvpn._bound_dns", return_value=True):
            status = NordVPNProvider(b).detect(validate=True)
        self.assertEqual(status.state, ProviderState.READY)
        self.assertEqual(status.adapter.guid, NORD)
        self.assertEqual(status.protocol, "NordLynx")
        b.route_good = False
        self.assertEqual(NordVPNProvider(b).detect().state, ProviderState.INCOMPATIBLE)
        b.connected = False
        self.assertEqual(NordVPNProvider(b).detect().state, ProviderState.NOT_CONNECTED)

    def test_unrelated_virtual_adapter_not_mistaken_for_nordvpn(self):
        b = NordBackend(connected=False)
        self.assertEqual(NordVPNProvider(b).detect().state, ProviderState.NOT_CONNECTED)


class ProfileLessTests(RelayTestCase):
    def setUp(self):
        super().setUp()
        self.settings["behavior"]["profile_less"] = True
        self.settings["behavior"]["approve_devices"] = False
        for conf in paths.VPN_DIR.glob("*.conf"):
            conf.unlink()

    def test_disconnected_never_starts_hotspot_or_wireguard(self):
        b = NordBackend(connected=False)
        r = self.relay(b)
        ok, out = self.run_start(r, token=None)
        self.assertFalse(ok)
        self.assertIn("NordVPN not connected", out)
        self.assertNotIn("hotspot_start", b.names())
        self.assertNotIn("wg_install", b.names())

    def test_hosts_without_conf_and_does_not_disconnect_nordvpn(self):
        b = NordBackend()
        r = self.relay(b)
        with mock.patch("wirespot.nordvpn._bound_internet", return_value=True), \
             mock.patch("wirespot.nordvpn._bound_dns", return_value=True), \
             mock.patch("wirespot.relay.forward_guard.install") as install, \
             mock.patch("wirespot.relay.forward_guard.installed", return_value=True), \
             mock.patch("wirespot.relay.forward_guard.remove") as remove:
            ok, out = self.run_start(r, token=None)
            self.assertTrue(ok, out)
            self.assertEqual(r.m.state, State.READY)
            self.assertEqual(r.record.provider, "nordvpn")
            self.assertTrue(r.record.forward_guard)
            install.assert_called_once_with(NORD)
            self.assertNotIn("wg_install", b.names())
            self.assertNotIn("dns_lock", b.names())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(r.stop(self.settings))
            remove.assert_called_once()
            self.assertNotIn("wg_uninstall", b.names())
            self.assertEqual(r.record.provider, "")
            r.stop_guard()

    def test_guard_install_failure_prevents_hotspot(self):
        b = NordBackend()
        r = self.relay(b)
        with mock.patch("wirespot.nordvpn._bound_internet", return_value=True), \
             mock.patch("wirespot.nordvpn._bound_dns", return_value=True), \
             mock.patch("wirespot.relay.forward_guard.install", side_effect=relay.forward_guard.ForwardGuardError("blocked")):
            ok, out = self.run_start(r, token=None)
        self.assertFalse(ok)
        self.assertIn("Cannot safely share NordVPN", out)
        self.assertNotIn("hotspot_start", b.names())

    def test_route_loss_stops_hotspot_and_retains_forwarding_rule(self):
        b = NordBackend()
        r = self.relay(b)
        with mock.patch("wirespot.nordvpn._bound_internet", return_value=True), \
             mock.patch("wirespot.nordvpn._bound_dns", return_value=True), \
             mock.patch("wirespot.relay.forward_guard.install"), \
             mock.patch("wirespot.relay.forward_guard.installed", return_value=True), \
             mock.patch("wirespot.relay.forward_guard.remove") as remove:
            ok, out = self.run_start(r, token=None)
            self.assertTrue(ok, out)
            r.stop_guard()
            b.route_good = False
            with contextlib.redirect_stdout(io.StringIO()):
                relay.Guard(r).tick()
            self.assertEqual(r.m.state, State.ERROR)
            self.assertFalse(b.hotspot_on)
            self.assertTrue(r.record.forward_guard)
            remove.assert_not_called()

    def test_stop_keeps_rule_if_hotspot_cannot_be_verified_off(self):
        b = NordBackend()
        r = self.relay(b)
        with mock.patch("wirespot.nordvpn._bound_internet", return_value=True), \
             mock.patch("wirespot.nordvpn._bound_dns", return_value=True), \
             mock.patch("wirespot.relay.forward_guard.install"), \
             mock.patch("wirespot.relay.forward_guard.installed", return_value=True), \
             mock.patch("wirespot.relay.forward_guard.remove") as remove:
            ok, out = self.run_start(r, token=None)
            self.assertTrue(ok, out)
            r.stop_guard()
            b.hotspot_status = lambda *a, **k: (None, "status unavailable")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(r.stop(self.settings))
            self.assertTrue(r.record.forward_guard)
            remove.assert_not_called()
