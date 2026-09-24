"""Profile checks distinguish a VPN handshake from a server ping."""
import io
import socket
import subprocess
import unittest
from unittest import mock

from wirespot import network_tests


class ProfileHealthTests(unittest.TestCase):
    def test_endpoint_parses_ipv4_hostnames_and_bracketed_ipv6(self):
        self.assertEqual(network_tests.endpoint_parts("vpn.example:51820"), ("vpn.example", 51820))
        self.assertEqual(network_tests.endpoint_parts("[2001:db8::1]:443"), ("2001:db8::1", 443))
        for bad in ("", "example", "example:0", "example:65536", "2001:db8::1:51820"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                network_tests.endpoint_parts(bad)

    def test_recent_handshake_is_stronger_than_ping(self):
        resolver = mock.Mock()
        result = network_tests.profile_health("vpn.example:51820", active=True, handshake_age=12,
                                              resolver=resolver)
        self.assertEqual(result["level"], "good")
        resolver.assert_not_called()

    def test_ping_response_is_not_called_verified_vpn(self):
        address = [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.0.2.1", 51820))]
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "Reply: time=24ms\n", ""))
        result = network_tests.profile_health("vpn.example:51820", resolver=lambda *_a, **_k: address,
                                              runner=runner)
        self.assertEqual(result["level"], "warn")
        self.assertEqual(result["latency_ms"], 24)
        self.assertIn("Connect to verify", result["detail"])
        self.assertEqual(runner.call_args.args[0][-1], "192.0.2.1")

    def test_no_ping_reply_does_not_mark_disconnected_profile_broken(self):
        address = [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.0.2.1", 51820))]
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 1, "", ""))
        result = network_tests.profile_health("vpn.example:51820", resolver=lambda *_a, **_k: address,
                                              runner=runner)
        self.assertEqual(result["level"], "warn")
        self.assertIn("ignore ping", result["detail"])


class SpeedTestTests(unittest.TestCase):
    def test_bounded_download_and_generated_upload(self):
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, request.data, timeout))
            if "__down?bytes=5" in request.full_url:
                return io.BytesIO(b"12345")
            return io.BytesIO(b"")

        times = iter([0, .04, 1, 1.05, 2, 2.06, 3, 3.5, 4, 4.25])
        phases = []
        result = network_tests.speed_test(phases.append, opener=opener, clock=lambda: next(times),
                                          download_bytes=5, upload_bytes=2)
        self.assertEqual(phases, ["Measuring latency", "Measuring download", "Measuring upload"])
        self.assertEqual(result["latency_ms"], 50)
        self.assertEqual(result["download_bytes"], 5)
        self.assertEqual(calls[-1][1], b"\0\0")
        self.assertEqual(len(calls), 5)

    def test_short_download_is_an_error(self):
        with self.assertRaisesRegex(OSError, "ended early"):
            network_tests.speed_test(opener=lambda *_a, **_k: io.BytesIO(b""),
                                     download_bytes=5, upload_bytes=1)


if __name__ == "__main__":
    unittest.main()
