"""Adapter correlation + ICS decisions, using an inventory shaped like the
reporting machine (Intel AX201, WireGuard tunnel, WAN miniports)."""
import copy
import unittest

from wirespot import ics, netid

WG = "7c17e93e-faa2-cdb6-a63a-e2195608fc06"
WIFI = "786645d3-fa9a-4449-9008-3a1e85f3566a"
WFD1 = "0e30d34f-6276-41c0-aacb-61074107a43b"
WFD2 = "b00df79a-4d22-40ba-b1ab-8cf4f32df379"
WAN6 = "ef56a2b3-3cb9-489b-a36b-535f18ee2786"

BASE = {
    "adapters": [
        {"name": "Local Area Connection* 11", "description": "WAN Miniport (IPv6)", "status": "Up", "ifindex": 24,
         "guid": "{" + WAN6.upper() + "}", "hidden": True},
        {"name": "Local Area Connection* 12", "description": "WAN Miniport (Network Monitor)", "status": "Up",
         "ifindex": 22, "guid": "c5da0c00-cf82-4e00-b9cd-4f0a0e428a12"},
        {"name": "Local Area Connection* 2", "description": "Microsoft Wi-Fi Direct Virtual Adapter #4",
         "status": "Disconnected", "ifindex": 20, "guid": WFD2, "mac": "A4-80-FC-9A-8E-3A",
         "component": "{5d624f94-8850-40c3-a3fa-a4fd2080baf3}\\vwifimp_wfd"},
        {"name": "Local Area Connection* 1", "description": "Microsoft Wi-Fi Direct Virtual Adapter #3",
         "status": "Disconnected", "ifindex": 4, "guid": WFD1, "mac": "A6-80-FC-9A-8E-3B",
         "component": "{5d624f94-8850-40c3-a3fa-a4fd2080baf3}\\vwifimp_wfd"},
        {"name": "pr_wg-US-FREE-5_154f3e", "description": "WireGuard Tunnel", "status": "Up", "ifindex": 47, "guid": WG},
        {"name": "Wi-Fi", "description": "Intel(R) Wi-Fi 6 AX201 160MHz", "status": "Up", "ifindex": 13, "guid": WIFI,
         "mac": "A6-80-FC-9A-8E-3A", "hardware": True, "media": "Native 802.11"},
    ],
    "ipv4": [{"ifindex": 47, "ip": "10.2.0.2"}, {"ifindex": 13, "ip": "192.168.1.50"}],
    "routes": [{"prefix": "0.0.0.0/0", "ifindex": 47, "nexthop": "0.0.0.0", "metric": 0}],
    "services": [{"name": "SharedAccess", "status": "Running"}],
    "ics_scope": "192.168.137.1",
    "ics": [
        {"name": "Wi-Fi", "guid": "{" + WIFI + "}", "sharing_enabled": False, "sharing_type": -1},
        {"name": "pr_wg-US-FREE-5_154f3e", "guid": WG, "sharing_enabled": False, "sharing_type": -1},
    ],
}


def hotspot_on(ics_state="windows-default"):
    d = copy.deepcopy(BASE)
    d["adapters"][2]["status"] = "Up"                       # WFD #4 carries the hotspot
    d["ipv4"].append({"ifindex": 20, "ip": "192.168.137.1"})
    wfd = {"name": "Local Area Connection* 2", "guid": WFD2, "sharing_enabled": True, "sharing_type": 1}
    if ics_state == "windows-default":                       # hotspot sourced from Wi-Fi
        d["ics"][0].update(sharing_enabled=True, sharing_type=0)
    elif ics_state == "vpn":                                 # hotspot sourced from WireGuard
        d["ics"][1].update(sharing_enabled=True, sharing_type=0)
    d["ics"].append(wfd)
    return d


class ClassifyTests(unittest.TestCase):
    def test_kinds(self):
        inv = netid.build_inventory(BASE)
        kinds = {a.name: a.kind for a in inv.adapters}
        self.assertEqual(kinds["Local Area Connection* 11"], netid.KIND_WAN_MINIPORT)
        self.assertEqual(kinds["Local Area Connection* 2"], netid.KIND_WIFI_DIRECT)
        self.assertEqual(kinds["pr_wg-US-FREE-5_154f3e"], netid.KIND_WIREGUARD)
        self.assertEqual(kinds["Wi-Fi"], netid.KIND_WIFI)

    def test_guid_normalisation(self):
        inv = netid.build_inventory(BASE)
        self.assertEqual(inv.by_guid("{" + WAN6.upper() + "}").name, "Local Area Connection* 11")
        self.assertIsNotNone(inv.ics_by_guid(WIFI.upper()))


class LocateHotspotTests(unittest.TestCase):
    def test_v011_bug_never_picks_wan_miniport(self):
        """Hotspot never started (WiFiDeviceOff): the old code chose 'Local Area
        Connection* 11' (WAN Miniport IPv6). Now: no candidate, explicit reason."""
        m = netid.locate_hotspot(netid.build_inventory(BASE), WIFI)
        self.assertIsNone(m.adapter)
        self.assertEqual(m.problem, "none_up")
        self.assertTrue(all(c[0].kind == netid.KIND_WIFI_DIRECT for c in m.candidates))

    def test_finds_up_wifi_direct_with_ics_address(self):
        m = netid.locate_hotspot(netid.build_inventory(hotspot_on()), WIFI)
        self.assertEqual(m.adapter.guid, WFD2)
        self.assertIn("has ICS address 192.168.137.1", m.reasons)
        self.assertTrue(any("visible to ICS" in r for r in m.reasons))

    def test_ambiguous_is_refused(self):
        d = hotspot_on()
        d["adapters"][3]["status"] = "Up"
        d["ipv4"].append({"ifindex": 4, "ip": "192.168.137.1"})
        d["ics"].append({"name": "Local Area Connection* 1", "guid": WFD1})
        d["adapters"][2]["mac"] = d["adapters"][3]["mac"] = ""
        m = netid.locate_hotspot(netid.build_inventory(d), WIFI)
        self.assertIsNone(m.adapter)
        self.assertEqual(m.problem, "ambiguous")

    def test_no_virtual_adapter(self):
        d = copy.deepcopy(BASE)
        d["adapters"] = [a for a in d["adapters"] if "Direct" not in a["description"]]
        self.assertEqual(netid.locate_hotspot(netid.build_inventory(d)).problem, "no_virtual_adapter")

    def test_uplink_kind(self):
        self.assertEqual(netid.uplink_kind({"description": "Realtek PCIe GbE Family Controller", "media": "802.3", "hardware": True}), "Ethernet")
        self.assertEqual(netid.uplink_kind({"description": "Intel(R) Wi-Fi 6 AX201", "media": "Native 802.11", "hardware": True}), "Wi-Fi")
        self.assertEqual(netid.uplink_kind({"description": "Remote NDIS based Internet Sharing Device", "media": "802.3"}), "USB tethering")

    def test_mac_siblings(self):
        self.assertTrue(netid.mac_siblings("A4-80-FC-9A-8E-3A", "A6-80-FC-9A-8E-3A"))
        self.assertFalse(netid.mac_siblings("A4-80-FC-9A-8E-3A", "00-15-5D-00-0E-00"))

    def test_correlate_reports_adapters_missing_from_ics(self):
        pairs, orphans = netid.correlate(netid.build_inventory(BASE))
        missing = {p.adapter.name for p in pairs if p.ics is None}
        self.assertIn("Local Area Connection* 2", missing)
        self.assertNotIn("Wi-Fi", missing)


class ClassicIcsTests(unittest.TestCase):
    """Mobile Hotspot does not use classic ICS flags (measured); they only matter as conflicts."""

    def flags(self, *items):
        return ics.parse_flags({"flags": [dict(zip(("guid", "name", "public", "private", "exists"), i)) for i in items]})

    def test_no_flags_no_conflicts(self):
        self.assertEqual(ics.conflicts([], WG, WFD2), [])
        self.assertEqual(ics.conflicts(None, WG, WFD2), [])

    def test_wifi_shared_into_hotspot_is_a_leak(self):
        c = ics.conflicts(self.flags((WIFI, "Wi-Fi", True, False, True), (WFD2, "LAC* 2", False, True, True)), WG, WFD2)
        self.assertEqual([x.kind for x in c], ["leak"])
        self.assertIn("Wi-Fi", c[0].detail)

    def test_vpn_shared_into_hotspot_is_fine(self):
        self.assertEqual(ics.conflicts(self.flags((WG, "wg", True, False, True), (WFD2, "LAC* 2", False, True, True)), WG, WFD2), [])

    def test_unrelated_sharing_is_informational(self):
        c = ics.conflicts(self.flags(("1111", "Ethernet", True, False, True), ("2222", "vEthernet", False, True, True)), WG, WFD2)
        self.assertEqual([x.kind for x in c], ["other"])

    def test_stale_flags_detected(self):
        c = ics.conflicts(self.flags(("dead", "pr_wg-old", True, False, False)), WG, WFD2)
        self.assertIn("stale", [x.kind for x in c])

    def test_inventory_carries_flags(self):
        d = copy.deepcopy(BASE)
        d["ics_flags"] = [{"guid": "{" + WIFI.upper() + "}", "name": "Wi-Fi", "public": True, "private": False, "exists": True}]
        inv = netid.build_inventory(d)
        self.assertEqual(inv.ics_flags[0].guid, WIFI)
        self.assertTrue(inv.ics_flags[0].public)

    def test_sharing_labels(self):
        c = netid.ics_from_data(hotspot_on("vpn")["ics"])
        self.assertEqual([x.sharing for x in c], ["disabled", "public", "private"])


if __name__ == "__main__":
    unittest.main()
