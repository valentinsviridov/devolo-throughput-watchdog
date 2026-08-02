from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from devolo_watchdog.__main__ import build_parser, main, run_calibrate, run_doctor
from devolo_watchdog.config import Settings


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


class CliParserTests(unittest.TestCase):
    def test_once_and_allow_action_flag_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["run", "--once", "--allow-action"])
        self.assertTrue(args.once)
        self.assertTrue(args.allow_action)

    def test_doctor_subcommand_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["doctor", "--json"])
        self.assertEqual(args.subcommand, "doctor")
        self.assertTrue(args.json)

        args_before = parser.parse_args(["--json", "doctor"])
        self.assertEqual(args_before.subcommand, "doctor")
        self.assertTrue(args_before.json)

    def test_calibrate_subcommand_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["calibrate", "--samples", "5"])
        self.assertEqual(args.subcommand, "calibrate")
        self.assertEqual(args.samples, 5)

    def test_healthcheck_subcommand_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["healthcheck"])
        self.assertEqual(args.subcommand, "healthcheck")

    @patch("devolo_watchdog.__main__.probe_gateway")
    def test_check_config_main_execution(self, mock_ping):
        from devolo_watchdog.models import GatewayProbeResult

        mock_ping.return_value = GatewayProbeResult(reachable=True)

        env = {
            "DW_REMOTE_PROBE": "192.168.1.1",
            "DW_DEVOLO_IP": "192.168.1.20",
            "DW_MIN_UPLOAD_MBPS": "100",
            "DW_MIN_DOWNLOAD_MBPS": "100",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.argv", ["devolo-watchdog", "--check-config"]):
                self.assertEqual(main(), 0)

    @patch("devolo_watchdog.__main__.run_doctor")
    def test_doctor_main_execution(self, mock_doctor):
        mock_doctor.return_value = 0
        with patch("sys.argv", ["devolo-watchdog", "doctor"]):
            self.assertEqual(main(), 0)
            mock_doctor.assert_called_once()

    @patch("devolo_watchdog.probes.probe_plc_phy")
    @patch("devolo_watchdog.__main__.probe_gateway")
    def test_run_doctor_checks(self, mock_ping, mock_plc):
        from devolo_watchdog.models import GatewayProbeResult, PlcPhyResult

        mock_ping.return_value = GatewayProbeResult(reachable=True)
        mock_plc.return_value = PlcPhyResult(reachable=True, rx_rate_mbps=200.0, tx_rate_mbps=200.0)
        st = make_settings()
        code = run_doctor(st, json_output=False)
        self.assertEqual(code, 0)

    @patch("devolo_watchdog.probes.probe_wan_iperf")
    def test_run_calibrate(self, mock_probe):
        from devolo_watchdog.models import WanIperfResult

        mock_probe.return_value = WanIperfResult(upload_mbps=150.0, download_mbps=120.0)
        st = make_settings()
        code = run_calibrate(st, samples_count=2, json_output=True)
        self.assertEqual(code, 0)

    @patch("devolo_watchdog.probes.probe_wan_iperf")
    def test_run_calibrate_failure_with_error_reporting(self, mock_probe):
        from devolo_watchdog.models import WanIperfResult

        mock_probe.return_value = WanIperfResult(
            error="WAN upload test failed: port 5201: connect failed"
        )
        st = make_settings()
        with patch("sys.stderr"):
            code = run_calibrate(st, samples_count=2, json_output=False)
        self.assertEqual(code, 1)

    @patch("devolo_watchdog.probes.probe_local_iperf")
    @patch("devolo_watchdog.probes.probe_wan_iperf")
    def test_run_calibrate_local_iperf(self, mock_wan, mock_local):
        from devolo_watchdog.models import LocalIperfResult, WanIperfResult

        mock_wan.return_value = WanIperfResult(error="WAN probe unavailable")
        mock_local.return_value = LocalIperfResult(
            upload_mbps=200.0, download_mbps=180.0, port=5201
        )
        st = make_settings(local_iperf_server="192.168.1.100")
        code = run_calibrate(st, samples_count=2, json_output=True)
        self.assertEqual(code, 0)

    def test_healthcheck_execution(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            hb_path = tf.name

        try:
            from devolo_watchdog.state import write_heartbeat

            write_heartbeat(hb_path)
            with patch("sys.argv", ["devolo-watchdog", "healthcheck", "--heartbeat-file", hb_path]):
                self.assertEqual(main(), 0)
        finally:
            if os.path.exists(hb_path):
                os.unlink(hb_path)
