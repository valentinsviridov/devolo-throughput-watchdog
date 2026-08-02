from __future__ import annotations

import unittest

from devolo_watchdog.config import Settings
from devolo_watchdog.core import IperfThroughputTimeout, IperfUnavailable, evaluate_cycle
from devolo_watchdog.models import IperfSample, Status


def make_settings(**kwargs) -> Settings:
    defaults = {
        "iperf_server": "iperf.example.com",
        "iperf_ports": tuple(range(5201, 5206)),
        "remote_probe": "192.168.1.1",
        "devolo_ip": "192.168.1.20",
        "min_upload_mbps": 100.0,
        "min_download_mbps": 80.0,
        "require_plc_evidence_for_reboot": False,
    }
    defaults.update(kwargs)
    return Settings(**defaults)


class CoreCycleTests(unittest.TestCase):
    def test_evaluate_cycle_unreachable_gateway(self):
        st = make_settings()

        def mock_ping(_host, _settings):
            return False

        def mock_iperf(_settings, _reverse):
            raise AssertionError("iperf should not be called when gateway is unreachable")

        res = evaluate_cycle(st, mock_ping, mock_iperf)
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertIn("unreachable", res.reason)

    def test_evaluate_cycle_success(self):
        st = make_settings()

        def mock_ping(_host, _settings):
            return True

        def mock_iperf(_settings, reverse):
            if reverse:
                return IperfSample(150.0, 5202)
            return IperfSample(120.0, 5201)

        res = evaluate_cycle(st, mock_ping, mock_iperf)
        self.assertEqual(res.status, Status.HEALTHY)
        self.assertEqual(res.upload_mbps, 120.0)
        self.assertEqual(res.download_mbps, 150.0)

    def test_evaluate_cycle_iperf_exceptions_handled(self):
        st = make_settings()

        def mock_ping(_host, _settings):
            return True

        def mock_iperf_unavailable(_settings, _reverse):
            raise IperfUnavailable("all ports failed")

        res1 = evaluate_cycle(st, mock_ping, mock_iperf_unavailable)
        self.assertEqual(res1.status, Status.UNAVAILABLE)
        self.assertIn("all ports failed", res1.reason)

        def mock_iperf_timeout(_settings, _reverse):
            raise IperfThroughputTimeout("transfer timed out")

        res2 = evaluate_cycle(st, mock_ping, mock_iperf_timeout)
        self.assertEqual(res2.status, Status.UNAVAILABLE)
        self.assertIn("transfer timed out", res2.reason)
