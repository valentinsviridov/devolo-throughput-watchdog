from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from devolo_watchdog.probes import IperfError, parse_iperf_mbps, run_single_iperf


class ParseIperfTests(unittest.TestCase):
    def test_receiver_rate_is_converted_to_decimal_mbps(self):
        payload = json.dumps({"end": {"sum_received": {"bits_per_second": 123_456_789}}})
        self.assertAlmostEqual(parse_iperf_mbps(payload), 123.456789)

    def test_sum_fallback_rate_is_parsed(self):
        payload = json.dumps({"end": {"sum": {"bits_per_second": 100_000_000}}})
        self.assertEqual(parse_iperf_mbps(payload), 100.0)

    def test_iperf_error_is_raised(self):
        with self.assertRaises(IperfError):
            parse_iperf_mbps(json.dumps({"error": "server is busy"}))

    def test_missing_throughput_field_raises_iperf_error(self):
        with self.assertRaises(IperfError):
            parse_iperf_mbps(json.dumps({"end": {}}))


class SingleIperfCommandTests(unittest.TestCase):
    @patch("subprocess.run")
    def test_run_single_iperf_execution(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"end": {"sum_received": {"bits_per_second": 100_000_000}}}),
        )
        val = run_single_iperf(
            server="192.168.1.100",
            port=5201,
            test_bytes="64M",
            parallel=1,
            timeout_seconds=30,
            reverse=True,
        )
        self.assertEqual(val, 100.0)
        cmd = mock_run.call_args[0][0]
        self.assertIn("192.168.1.100", cmd)
        self.assertIn("--reverse", cmd)


class ProbeGatewayTests(unittest.TestCase):
    @patch("subprocess.run")
    def test_probe_gateway_success(self, mock_run):
        from devolo_watchdog.probes import probe_gateway

        mock_run.return_value = MagicMock(returncode=0, stderr="")
        res = probe_gateway("192.168.1.1")
        self.assertTrue(res.reachable)

    @patch("subprocess.run")
    def test_probe_gateway_fallback_on_exit_code_2(self, mock_run):
        from devolo_watchdog.probes import probe_gateway

        mock_run.side_effect = [
            MagicMock(returncode=2, stderr="ping: invalid option -- 'W'"),
            MagicMock(returncode=0, stderr=""),
        ]
        res = probe_gateway("192.168.1.1")
        self.assertTrue(res.reachable)
        self.assertEqual(mock_run.call_count, 2)
