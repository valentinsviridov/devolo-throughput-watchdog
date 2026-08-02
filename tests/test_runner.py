from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from devolo_watchdog.config import Settings
from devolo_watchdog.models import (
    ActionType,
    CycleResult,
    GatewayProbeResult,
    MeasurementReport,
    Status,
    WanIperfResult,
)
from devolo_watchdog.runner import collect_measurement_report, log_result, run_daemon


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


class LoggingAndCollectionTests(unittest.TestCase):
    @patch("devolo_watchdog.runner.LOG.info")
    def test_log_result_json_format(self, mock_log):
        res = CycleResult(
            status=Status.HEALTHY,
            reason="All good",
            upload_mbps=150.0,
            download_mbps=120.0,
            upload_port=5201,
            download_port=5202,
        )
        log_result(res, failures=0, fail_limit=3, action=ActionType.NONE, log_format="json")
        mock_log.assert_called_once()
        log_str = mock_log.call_args[0][0]
        self.assertIn('"status": "healthy"', log_str)
        self.assertIn('"upload_mbps": 150.0', log_str)

    @patch("devolo_watchdog.runner.probe_gateway")
    @patch("devolo_watchdog.runner.probe_plc_phy")
    @patch("devolo_watchdog.runner.probe_wan_iperf")
    def test_collect_measurement_report_handles_plc_exception(self, mock_wan, mock_plc, mock_gw):
        mock_gw.return_value = GatewayProbeResult(reachable=True)
        mock_plc.side_effect = RuntimeError("PLC interface error")
        mock_wan.return_value = WanIperfResult(upload_mbps=120.0, download_mbps=100.0)

        st = make_settings()
        report = collect_measurement_report(st, now=1000.0)
        self.assertTrue(report.gateway.reachable)
        self.assertIsNone(report.plc_phy)


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

    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_run_daemon_once_unavailable_returns_exit_code_2(self, mock_collect):
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=False, error="gateway unreachable")
        )
        cfg = make_settings()
        exit_code = run_daemon(cfg, once=True)
        self.assertEqual(exit_code, 2)

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
    def test_reboot_post_verification_success(self, mock_collect, mock_reboot):
        mock_collect.side_effect = [
            MeasurementReport(
                gateway=GatewayProbeResult(reachable=True),
                wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
            ),
            MeasurementReport(
                gateway=GatewayProbeResult(reachable=True),
                wan_iperf=WanIperfResult(upload_mbps=150.0, download_mbps=150.0),
            ),
        ]
        mock_reboot.return_value = True
        cfg = make_settings()
        exit_code = run_daemon(cfg, once=True, allow_action=True)
        self.assertEqual(exit_code, 1)

    @patch("devolo_watchdog.runner.restart_devolo")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_reboot_rejected_logging(self, mock_collect, mock_reboot):
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )
        mock_reboot.return_value = False
        cfg = make_settings()
        exit_code = run_daemon(cfg, once=True, allow_action=True)
        self.assertEqual(exit_code, 1)

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

    @patch("devolo_watchdog.runner.write_heartbeat")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_heartbeat_is_written_when_configured(self, mock_collect, mock_hb):
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=150.0, download_mbps=150.0),
        )
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            hb_file = tf.name

        try:
            cfg = make_settings(heartbeat_file=hb_file)
            run_daemon(cfg, once=True)
            mock_hb.assert_called_once()
        finally:
            if os.path.exists(hb_file):
                os.unlink(hb_file)

    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_unexpected_error_in_measurement_cycle_handled(self, mock_collect):
        mock_collect.side_effect = Exception("Unexpected network socket error")
        cfg = make_settings()
        exit_code = run_daemon(cfg, once=True)
        self.assertEqual(exit_code, 2)
