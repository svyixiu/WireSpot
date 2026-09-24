import unittest

from wirespot import hotspot


class OperationResultTests(unittest.TestCase):
    """The v0.1.1 bug: any returned status was printed as '[+] Hotspot start: <status>'."""

    def test_wifideviceoff_is_failure(self):
        r = hotspot.parse_result({"ok": False, "action": "start", "status": "WiFiDeviceOff", "status_code": 3,
                                  "state_after": "Off", "code": "start_status"})
        self.assertFalse(r.ok)
        self.assertEqual(r.status, "WiFiDeviceOff")
        self.assertIn("access-point role", hotspot.describe_status(r.status))

    def test_every_non_success_status_fails(self):
        for status in ("Unknown", "MobileBroadbandDeviceOff", "WiFiDeviceOff", "EntitlementCheckTimeout",
                       "EntitlementCheckFailure", "OperationInProgress", "BluetoothDeviceOff",
                       "NetworkLimitedConnectivity", "SomethingNew"):
            # even if a buggy script claimed ok=True
            r = hotspot.parse_result({"ok": True, "action": "start", "status": status, "state_after": "On"})
            self.assertFalse(r.ok, status)

    def test_success_requires_state_on(self):
        self.assertTrue(hotspot.parse_result({"ok": True, "status": "Success", "state_after": "On"}).ok)
        self.assertFalse(hotspot.parse_result({"ok": True, "status": "Success", "state_after": "InTransition"}).ok)
        self.assertFalse(hotspot.parse_result({"ok": True, "status": "Success", "state_after": "Off"}).ok)

    def test_missing_payload_is_failure(self):
        r = hotspot.parse_result(None, 1, "Exception calling StartTetheringAsync")
        self.assertFalse(r.ok)
        self.assertIn("StartTetheringAsync", r.error)

    def test_stop_semantics(self):
        self.assertTrue(hotspot.parse_result({"ok": True, "action": "stop", "state_after": "Off"}).ok)
        self.assertFalse(hotspot.parse_result({"ok": False, "action": "stop", "state_after": "On"}).ok)

    def test_applied_band_mapped(self):
        r = hotspot.parse_result({"ok": True, "status": "Success", "state_after": "On", "applied_band": "FiveGigahertz"})
        self.assertEqual(r.applied_band, "5")


class BandPlanTests(unittest.TestCase):
    SUP = {"2.4": True, "5": True, "6": None}

    def test_auto_always_ok(self):
        self.assertTrue(hotspot.plan_band("auto", "2.4", self.SUP).ok)

    def test_5ghz_hotspot_with_24ghz_uplink_is_a_hint_not_a_blocker(self):
        # Measured on Intel AX201: a 5 GHz hotspot starts while the uplink is on 2.4 GHz.
        plan = hotspot.plan_band("5", "2.4", self.SUP)
        self.assertTrue(plan.ok)
        self.assertIn("WiFiDeviceOff", plan.note)

    def test_matching_band_ok(self):
        plan = hotspot.plan_band("5", "5", self.SUP)
        self.assertTrue(plan.ok)
        self.assertEqual(plan.note, "")

    def test_stop_summary(self):
        r = hotspot.parse_result({"ok": True, "action": "stop", "state_after": "Off"})
        self.assertEqual(r.summary, "hotspot is Off")

    def test_no_wifi_uplink_is_unconstrained(self):
        self.assertTrue(hotspot.plan_band("5", "", self.SUP).ok)

    def test_adapter_unsupported(self):
        plan = hotspot.plan_band("5", "5", {"2.4": True, "5": False, "6": None})
        self.assertFalse(plan.ok)
        self.assertEqual(plan.alternatives, ["auto", "2.4"])

    def test_6ghz_unknown_to_os(self):
        self.assertFalse(hotspot.plan_band("6", "", self.SUP).ok)

    def test_supported_bands_parsing(self):
        data = {"bands": {"TwoPointFourGigahertz": True, "FiveGigahertz": "error: E_FAIL"}}
        self.assertEqual(hotspot.supported_bands(data), {"2.4": True, "5": None, "6": None})


if __name__ == "__main__":
    unittest.main()
