from __future__ import annotations

import unittest
from unittest.mock import patch

from devolo_watchdog.config import Settings
from devolo_watchdog.models import (
    GatewayProbeResult,
    MeasurementReport,
    WanIperfResult,
)
from devolo_watchdog.runner import run_daemon


def make_settings(**kwargs) -> Settings:
    defaults = {
        "iperf_server": "iperf.example.com",
        "iperf_ports": tuple(range(5201, 5206)),
        "remote_probe": "192.168.1.1",
        "devolo_ip": "192.168.1.20",
        "min_upload_mbps": 100.0,
        "min_download_mbps": 80.0,
        "fail_limit": 1,
        "action": "reboot",
        "initial_delay_seconds": 0,
        "post_reboot_delay_seconds": 0,
        "require_plc_evidence_for_reboot": False,
    }
    defaults.update(kwargs)
    return Settings(**defaults)


class DaemonExecutionTests(unittest.TestCase):
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_run_daemon_once_healthy(self, mock_collect):
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=150.0, download_mbps=150.0),
        )
        cfg = make_settings()
        exit_code = run_daemon(cfg, once=True)
        self.assertEqual(exit_code, 0)

    @patch("devolo_watchdog.runner.restart_devolo")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_once_defaults_to_dry_run_without_allow_action(self, mock_collect, mock_reboot):
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )
        cfg = make_settings()
        exit_code = run_daemon(cfg, once=True, allow_action=False)
        self.assertEqual(exit_code, 1)
        mock_reboot.assert_not_called()

    @patch("devolo_watchdog.runner.restart_devolo")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_once_triggers_reboot_when_allow_action_is_true(self, mock_collect, mock_reboot):
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )
        mock_reboot.return_value = True
        cfg = make_settings()
        exit_code = run_daemon(cfg, once=True, allow_action=True)
        self.assertEqual(exit_code, 1)
        mock_reboot.assert_called_once_with(cfg)

    @patch("devolo_watchdog.runner.restart_devolo")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_every_reboot_attempt_is_counted_even_if_call_fails(self, mock_collect, mock_reboot):
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )
        mock_reboot.side_effect = RuntimeError("Device communication error")
        cfg = make_settings()

        run_daemon(cfg, once=True, allow_action=True)
        mock_reboot.assert_called_once()
