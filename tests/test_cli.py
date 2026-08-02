from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from devolo_watchdog.__main__ import (
    build_parser,
    find_executable,
    load_env_file_if_present,
    main,
    run_calibrate,
    run_discover,
    run_doctor,
    run_restart,
)
from devolo_watchdog.actions import ActionDependencyError
from devolo_watchdog.config import Settings
from devolo_watchdog.runner import RestartPersistenceError


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
    def test_find_executable_uses_linux_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            executable = Path(tmpdir) / "test-command"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)

            with patch.dict(os.environ, {"PATH": tmpdir}):
                self.assertEqual(find_executable("test-command"), str(executable))
                self.assertIsNone(find_executable("missing-command"))

    def test_subcommand_is_required(self):
        parser = build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_run_flags_are_rejected_at_top_level(self):
        parser = build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["--once", "run"])

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

    def test_calibrate_rejects_non_positive_sample_count(self):
        parser = build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["calibrate", "--samples", "0"])

    def test_healthcheck_subcommand_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["healthcheck"])
        self.assertEqual(args.subcommand, "healthcheck")

    def test_restart_subcommand_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["restart", "--json"])
        self.assertEqual(args.subcommand, "restart")
        self.assertTrue(args.json)

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
            with patch("sys.argv", ["devolo-watchdog", "run", "--check-config"]):
                self.assertEqual(main(), 0)

    @patch("devolo_watchdog.__main__.probe_gateway")
    def test_check_config_gateway_unreachable(self, mock_ping):
        from devolo_watchdog.models import GatewayProbeResult

        mock_ping.return_value = GatewayProbeResult(reachable=False, error="timeout")

        env = {
            "DW_REMOTE_PROBE": "192.168.1.1",
            "DW_DEVOLO_IP": "192.168.1.20",
            "DW_MIN_UPLOAD_MBPS": "100",
            "DW_MIN_DOWNLOAD_MBPS": "100",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.argv", ["devolo-watchdog", "run", "--check-config"]):
                self.assertEqual(main(), 2)

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
        with tempfile.TemporaryDirectory() as tmpdir:
            st = make_settings(state_file=os.path.join(tmpdir, "state.json"))
            code = run_doctor(
                st,
                json_output=False,
                executable_finder=lambda command: f"/usr/bin/{command}",
            )
            self.assertEqual(code, 0)

    @patch("devolo_watchdog.probes.probe_plc_phy")
    @patch("devolo_watchdog.__main__.probe_gateway")
    def test_run_doctor_json_output(self, mock_ping, mock_plc):
        from devolo_watchdog.models import GatewayProbeResult, PlcPhyResult

        mock_ping.return_value = GatewayProbeResult(reachable=True)
        mock_plc.return_value = PlcPhyResult(reachable=True, rx_rate_mbps=200.0, tx_rate_mbps=200.0)
        st = make_settings()
        code = run_doctor(
            st,
            json_output=True,
            executable_finder=lambda command: f"/usr/bin/{command}",
        )
        self.assertEqual(code, 0)

    @patch("devolo_watchdog.probes.probe_plc_phy")
    @patch("devolo_watchdog.__main__.probe_gateway")
    def test_run_doctor_reports_missing_iperf3(self, mock_ping, mock_plc):
        from devolo_watchdog.models import GatewayProbeResult, PlcPhyResult

        mock_ping.return_value = GatewayProbeResult(reachable=True)
        mock_plc.return_value = PlcPhyResult(reachable=True, rx_rate_mbps=200.0, tx_rate_mbps=200.0)
        stdout = io.StringIO()

        def binary_path(command):
            return None if command == "iperf3" else f"/usr/bin/{command}"

        with redirect_stdout(stdout):
            code = run_doctor(
                make_settings(),
                json_output=True,
                executable_finder=binary_path,
            )

        self.assertEqual(code, 1)
        payload = json.loads(stdout.getvalue())
        iperf_check = next(
            check for check in payload["checks"] if check["check"] == "iperf3_binary"
        )
        self.assertFalse(iperf_check["passed"])
        self.assertEqual(iperf_check["detail"], "not found on PATH")

    @patch("time.sleep")
    @patch("devolo_watchdog.probes.probe_wan_iperf")
    def test_run_calibrate(self, mock_probe, mock_sleep):
        from devolo_watchdog.models import WanIperfResult

        mock_probe.return_value = WanIperfResult(upload_mbps=150.0, download_mbps=120.0)
        st = make_settings()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = run_calibrate(st, samples_count=2, json_output=True)
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertNotIn("Sample 1/2", stdout.getvalue())
        self.assertIn("Sample 1/2", stderr.getvalue())
        mock_sleep.assert_called_once_with(2)

    @patch("time.sleep")
    @patch("devolo_watchdog.probes.probe_wan_iperf")
    def test_run_calibrate_failure_with_error_reporting(self, mock_probe, mock_sleep):
        from devolo_watchdog.models import WanIperfResult

        mock_probe.return_value = WanIperfResult(
            error="WAN upload test failed: port 5201: connect failed"
        )
        st = make_settings()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = run_calibrate(st, samples_count=2, json_output=False)
        self.assertEqual(code, 1)
        self.assertIn("upload=unavailable Mbps", stdout.getvalue())
        self.assertIn("download=unavailable Mbps", stdout.getvalue())
        self.assertIn("Calibration failed", stderr.getvalue())
        mock_sleep.assert_called_once_with(2)

    @patch("time.sleep")
    @patch("devolo_watchdog.probes.probe_wan_iperf")
    def test_run_calibrate_requires_both_directions(self, mock_probe, mock_sleep):
        from devolo_watchdog.models import WanIperfResult

        mock_probe.return_value = WanIperfResult(upload_mbps=150.0, error="download failed")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = run_calibrate(make_settings(), samples_count=2, json_output=True)

        self.assertEqual(code, 1)
        mock_sleep.assert_called_once_with(2)

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

    def test_healthcheck_json_output_is_valid_json(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            hb_path = tf.name

        try:
            from devolo_watchdog.state import write_heartbeat

            write_heartbeat(hb_path)
            stdout = io.StringIO()
            argv = [
                "devolo-watchdog",
                "--json",
                "healthcheck",
                "--heartbeat-file",
                hb_path,
                "--max-age-seconds",
                "30",
            ]
            with patch("sys.argv", argv), redirect_stdout(stdout):
                self.assertEqual(main(), 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["max_age_seconds"], 30.0)
        finally:
            os.unlink(hb_path)

    @patch("devolo_watchdog.__main__.run_daemon")
    def test_json_run_enables_structured_logging(self, mock_run):
        mock_run.return_value = 0
        env = {
            "DW_REMOTE_PROBE": "192.168.1.1",
            "DW_DEVOLO_IP": "192.168.1.20",
            "DW_MIN_UPLOAD_MBPS": "100",
            "DW_MIN_DOWNLOAD_MBPS": "100",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.argv", ["devolo-watchdog", "--json", "run", "--once"]):
                self.assertEqual(main(), 0)

        settings = mock_run.call_args.args[0]
        self.assertEqual(settings.log_format, "json")

    def test_run_discover_success(self):
        from unittest.mock import AsyncMock

        mock_device_cls = MagicMock()
        mock_device = AsyncMock()
        mock_device_cls.return_value = mock_device
        mock_device.__aenter__.return_value = mock_device
        mock_device.serial_number = "123456"
        mock_device.mac = "AA:BB:CC:DD:EE:FF"

        mock_overview = MagicMock()
        node = MagicMock(
            mac_address="11:22:33:44:55:66",
            user_device_name="Living Room",
            product_name="Magic 2 LAN",
            attached_to_router=False,
        )
        mock_overview.devices = [node]
        mock_overview.data_rates = []

        mock_device.plcnet.async_get_network_overview.return_value = mock_overview

        st = make_settings()
        code = run_discover(st, json_output=True, device_class=mock_device_cls)
        self.assertEqual(code, 0)

    @patch.dict("sys.modules", {"devolo_plc_api": None})
    def test_run_discover_missing_library(self):
        st = make_settings()
        with patch("sys.stderr"):
            code = run_discover(st, json_output=False)
        self.assertEqual(code, 3)

    @patch("devolo_watchdog.__main__.time.time", return_value=123.0)
    @patch("devolo_watchdog.__main__.request_restart", return_value=True)
    def test_run_restart_uses_shared_restart_path(self, mock_restart, mock_time):
        settings = make_settings(action="log")
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            code = run_restart(settings, json_output=False)

        self.assertEqual(code, 0)
        self.assertIn("Restart request accepted", stdout.getvalue())
        mock_restart.assert_called_once()
        self.assertIs(mock_restart.call_args.args[0], settings)
        self.assertEqual(mock_restart.call_args.kwargs["now"], 123.0)
        self.assertEqual(mock_restart.call_args.kwargs["reason"], "manual restart command")
        mock_time.assert_called_once_with()

    @patch("devolo_watchdog.__main__.request_restart", return_value=False)
    def test_run_restart_rejected_json(self, mock_restart):
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            code = run_restart(make_settings(), json_output=True)

        self.assertEqual(code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "rejected")
        self.assertEqual(payload["action"], "restart")
        self.assertEqual(payload["device"], "192.168.1.20")
        mock_restart.assert_called_once()

    @patch(
        "devolo_watchdog.__main__.request_restart",
        side_effect=RuntimeError("device unreachable"),
    )
    def test_run_restart_failure_json(self, mock_restart):
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            code = run_restart(make_settings(), json_output=True)

        self.assertEqual(code, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertIn("device unreachable", payload["detail"])
        mock_restart.assert_called_once()

    @patch(
        "devolo_watchdog.__main__.request_restart",
        side_effect=ActionDependencyError("devolo_plc_api library is not installed"),
    )
    def test_run_restart_missing_dependency_is_misconfigured(self, mock_restart):
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            code = run_restart(make_settings(), json_output=True)

        self.assertEqual(code, 3)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "error")
        self.assertIn("Restart unavailable", payload["detail"])
        mock_restart.assert_called_once()

    @patch(
        "devolo_watchdog.__main__.request_restart",
        side_effect=RestartPersistenceError("restart attempt could not be persisted"),
    )
    def test_run_restart_skips_action_when_state_cannot_be_persisted(self, mock_restart):
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            code = run_restart(make_settings(), json_output=False)

        self.assertEqual(code, 2)
        self.assertIn("Restart skipped", stderr.getvalue())
        mock_restart.assert_called_once()

    @patch("devolo_watchdog.__main__.run_restart", return_value=0)
    def test_main_dispatches_restart(self, mock_restart):
        env = {
            "DW_REMOTE_PROBE": "192.168.1.1",
            "DW_DEVOLO_IP": "192.168.1.20",
            "DW_MIN_UPLOAD_MBPS": "100",
            "DW_MIN_DOWNLOAD_MBPS": "100",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.argv", ["devolo-watchdog", "restart"]):
                self.assertEqual(main(), 0)

        settings = mock_restart.call_args.args[0]
        self.assertEqual(settings.devolo_ip, "192.168.1.20")
        self.assertFalse(mock_restart.call_args.args[1])

    def test_env_loader_parses_file(self):
        with tempfile.NamedTemporaryFile("w+", delete=False, dir=".") as tf:
            tf.write("# Comment\nDW_TEST_KEY=test_value\n")
            tf.flush()
            temp_name = os.path.basename(tf.name)

        try:
            with patch("pathlib.Path.is_file") as mock_is_file:
                mock_is_file.return_value = True
                with patch("pathlib.Path.read_text", return_value="DW_CUSTOM_ENV_VAR=loaded_ok\n"):
                    load_env_file_if_present()
                    self.assertEqual(os.environ.get("DW_CUSTOM_ENV_VAR"), "loaded_ok")
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
            os.environ.pop("DW_CUSTOM_ENV_VAR", None)
