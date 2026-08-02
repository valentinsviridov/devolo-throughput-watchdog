from __future__ import annotations

import unittest
from unittest.mock import patch

from devolo_watchdog.config import Settings
from devolo_watchdog.core import IperfUnavailable
from devolo_watchdog.models import GatewayProbeResult, IperfSample
from devolo_watchdog.network import ping, run_iperf


def make_settings(**kwargs) -> Settings:
    defaults = {
        "iperf_server": "iperf.example.com",
        "iperf_ports": tuple(range(5201, 5206)),
        "remote_probe": "192.168.1.1",
        "devolo_ip": "192.168.1.20",
        "min_upload_mbps": 100.0,
        "min_download_mbps": 80.0,
    }
    defaults.update(kwargs)
    return Settings(**defaults)


class LegacyNetworkTests(unittest.TestCase):
    @patch("devolo_watchdog.network.probe_gateway")
    def test_ping_returns_reachable_status(self, mock_probe):
        mock_probe.return_value = GatewayProbeResult(reachable=True)
        st = make_settings()
        self.assertTrue(ping("192.168.1.1", st))
        mock_probe.assert_called_once_with("192.168.1.1", st.ping_count, st.ping_timeout_seconds)

    def test_run_iperf_with_custom_single_fn_success(self):
        st = make_settings()

        def custom_fn(_settings, _reverse, port):
            if port == 5202:
                return 150.0
            raise RuntimeError("port 5201 offline")

        sample = run_iperf(st, reverse=False, single_fn=custom_fn)
        self.assertEqual(sample, IperfSample(150.0, 5202))

    def test_run_iperf_with_custom_single_fn_all_failed_raises_iperf_unavailable(self):
        st = make_settings()

        def broken_fn(_settings, _reverse, port):
            raise RuntimeError(f"port {port} failed")

        with self.assertRaises(IperfUnavailable) as cm:
            run_iperf(st, reverse=True, single_fn=broken_fn, ports=(5201,))
        self.assertIn("all candidate ports failed", str(cm.exception))

    @patch("devolo_watchdog.network.probe_iperf_direction")
    def test_run_iperf_standard_upload_success(self, mock_probe):
        st = make_settings()
        mock_probe.return_value = (IperfSample(120.0, 5201), None)
        sample = run_iperf(st, reverse=False, ports=(5201,))
        self.assertEqual(sample, IperfSample(120.0, 5201))
        mock_probe.assert_called_once_with(st, (5201,), False)

    @patch("devolo_watchdog.network.probe_iperf_direction")
    def test_run_iperf_standard_download_success(self, mock_probe):
        st = make_settings()
        mock_probe.return_value = (IperfSample(95.0, 5202), None)
        sample = run_iperf(st, reverse=True, ports=(5202,))
        self.assertEqual(sample, IperfSample(95.0, 5202))
        mock_probe.assert_called_once_with(st, (5202,), True)

    @patch("devolo_watchdog.network.probe_iperf_direction")
    def test_run_iperf_standard_failure_raises_iperf_unavailable(self, mock_probe):
        st = make_settings()
        mock_probe.return_value = (None, "connection timed out")
        with self.assertRaises(IperfUnavailable) as cm:
            run_iperf(st, reverse=False)
        self.assertIn("public iperf test failed: connection timed out", str(cm.exception))
