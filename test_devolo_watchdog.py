from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from devolo_watchdog.__main__ import build_parser
from devolo_watchdog.actions import read_password
from devolo_watchdog.config import Settings, candidate_ports, parse_ports
from devolo_watchdog.core import (
    CycleResult,
    IperfSample,
    IperfThroughputTimeout,
    IperfUnavailable,
    Status,
    evaluate_cycle,
)
from devolo_watchdog.network import parse_iperf_mbps, run_iperf
from devolo_watchdog.runner import run_daemon


def settings() -> Settings:
    return Settings(
        iperf_server="iperf.example.com",
        iperf_ports=tuple(range(5201, 5206)),
        remote_probe="192.168.1.1",
        devolo_ip="192.168.1.20",
        min_upload_mbps=100.0,
        min_download_mbps=80.0,
    )


class PortTests(unittest.TestCase):
    def test_ranges_lists_and_duplicates(self):
        self.assertEqual(parse_ports("5201-5203,5203,5210"), (5201, 5202, 5203, 5210))

    def test_descending_range_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_ports("5203-5201")

    def test_invalid_port_range_non_numeric(self):
        with self.assertRaises(ValueError):
            parse_ports("5201-abc")

    def test_direction_uses_different_rotated_ports(self):
        cfg = settings()
        forward = candidate_ports(cfg, False, now=0)
        reverse = candidate_ports(cfg, True, now=0)
        self.assertEqual(forward, (5201, 5202, 5203, 5204, 5205))
        self.assertEqual(reverse, (5201, 5202, 5203, 5204, 5205))


class ParseIperfTests(unittest.TestCase):
    def test_receiver_rate_is_converted_to_decimal_mbps(self):
        payload = json.dumps({"end": {"sum_received": {"bits_per_second": 123_456_789}}})
        self.assertAlmostEqual(parse_iperf_mbps(payload), 123.456789)

    def test_sum_fallback_rate_is_parsed(self):
        payload = json.dumps({"end": {"sum": {"bits_per_second": 100_000_000}}})
        self.assertEqual(parse_iperf_mbps(payload), 100.0)

    def test_iperf_error_is_raised(self):
        with self.assertRaises(RuntimeError):
            parse_iperf_mbps(json.dumps({"error": "server is busy"}))

    def test_missing_throughput_field_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_iperf_mbps(json.dumps({"end": {}}))


class PublicServerRetryTests(unittest.TestCase):
    def test_busy_port_is_skipped(self):
        attempted = []

        def fake(_settings, _reverse, port):
            attempted.append(port)
            if port == 5201:
                raise RuntimeError("server is busy")
            return 222.0

        sample = run_iperf(settings(), False, fake, ports=(5201, 5202))
        self.assertEqual(sample, IperfSample(222.0, 5202))
        self.assertEqual(attempted, [5201, 5202])

    def test_all_failed_ports_are_unavailable(self):
        def fake(*_args):
            raise RuntimeError("server is busy")

        with self.assertRaises(IperfUnavailable):
            run_iperf(settings(), False, fake, ports=(5201, 5202))

    def test_transfer_timeout_is_not_treated_as_busy_server(self):
        def fake(*_args):
            import subprocess
            raise subprocess.TimeoutExpired("iperf3", 30)

        with self.assertRaises(IperfThroughputTimeout):
            run_iperf(settings(), False, fake, ports=(5201, 5202))


class EvaluationTests(unittest.TestCase):
    def test_healthy_when_both_directions_are_above_threshold(self):
        samples = iter([IperfSample(150.0, 5201), IperfSample(120.0, 5202)])
        result = evaluate_cycle(settings(), lambda *_: True, lambda *_: next(samples))
        self.assertEqual(result.status, Status.HEALTHY)

    def test_degraded_when_one_direction_is_slow(self):
        samples = iter([IperfSample(150.0, 5201), IperfSample(40.0, 5202)])
        result = evaluate_cycle(settings(), lambda *_: True, lambda *_: next(samples))
        self.assertEqual(result.status, Status.DEGRADED)
        self.assertIn("download", result.reason)

    def test_public_server_failure_does_not_blame_plc_when_gateway_works(self):
        def fail(*_args):
            raise IperfUnavailable("all ports busy")

        result = evaluate_cycle(settings(), lambda *_: True, fail)
        self.assertEqual(result.status, Status.UNAVAILABLE)

    def test_unreachable_gateway_short_circuits_without_calling_iperf(self):
        iperf_called = False

        def iperf_mock(*_args):
            nonlocal iperf_called
            iperf_called = True
            raise IperfUnavailable("should not be called")

        result = evaluate_cycle(settings(), lambda *_: False, iperf_mock)
        self.assertEqual(result.status, Status.DEGRADED)
        self.assertFalse(iperf_called)
        self.assertIn("unreachable via ping", result.reason)

    def test_fixed_size_transfer_timeout_is_degraded(self):
        def fail(*_args):
            raise IperfThroughputTimeout("too slow")

        result = evaluate_cycle(settings(), lambda *_: True, fail)
        self.assertEqual(result.status, Status.DEGRADED)


class SettingsFromEnvTests(unittest.TestCase):
    def test_missing_required_env_var_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_valid_env_parsing(self):
        env = {
            "DW_REMOTE_PROBE": "192.168.1.1",
            "DW_DEVOLO_IP": "192.168.1.20",
            "DW_MIN_UPLOAD_MBPS": "100",
            "DW_MIN_DOWNLOAD_MBPS": "100",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = Settings.from_env()
            self.assertEqual(cfg.remote_probe, "192.168.1.1")
            self.assertEqual(cfg.devolo_ip, "192.168.1.20")

    def test_invalid_action_value_raises(self):
        env = {
            "DW_REMOTE_PROBE": "192.168.1.1",
            "DW_DEVOLO_IP": "192.168.1.20",
            "DW_MIN_UPLOAD_MBPS": "100",
            "DW_MIN_DOWNLOAD_MBPS": "100",
            "DW_ACTION": "destroy",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()


class PasswordFileTests(unittest.TestCase):
    def test_none_path_returns_none(self):
        self.assertIsNone(read_password(None))

    def test_reads_and_strips_password(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write(" secret_pass \n")
            tf.flush()
            self.assertEqual(read_password(tf.name), "secret_pass")
        os.unlink(tf.name)

    def test_empty_password_file_returns_none(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write("   \n")
            tf.flush()
            self.assertIsNone(read_password(tf.name))
        os.unlink(tf.name)

    def test_missing_password_file_raises_value_error(self):
        with self.assertRaises(ValueError):
            read_password("/path/to/non_existent_file.password")


class CliParserTests(unittest.TestCase):
    def test_once_flag_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["--once"])
        self.assertTrue(args.once)

    def test_check_config_flag_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["--check-config"])
        self.assertTrue(args.check_config)


class DaemonExecutionTests(unittest.TestCase):
    @patch("devolo_watchdog.runner.evaluate_cycle")
    def test_run_daemon_once_healthy(self, mock_eval):
        mock_eval.return_value = CycleResult(Status.HEALTHY, "ok", 150.0, 150.0, 5201, 5202)
        cfg = settings()
        exit_code = run_daemon(cfg, once=True)
        self.assertEqual(exit_code, 0)

    @patch("devolo_watchdog.runner.evaluate_cycle")
    def test_run_daemon_once_degraded(self, mock_eval):
        mock_eval.return_value = CycleResult(Status.DEGRADED, "slow", 10.0, 10.0, 5201, 5202)
        cfg = settings()
        exit_code = run_daemon(cfg, once=True)
        self.assertEqual(exit_code, 1)

    @patch("devolo_watchdog.runner.restart_devolo")
    @patch("devolo_watchdog.runner.evaluate_cycle")
    def test_daemon_triggers_reboot_on_fail_limit(self, mock_eval, mock_reboot):
        mock_eval.return_value = CycleResult(Status.DEGRADED, "slow", 10.0, 10.0, 5201, 5202)
        mock_reboot.return_value = True

        cfg = Settings(
            iperf_server="iperf.example.com",
            iperf_ports=tuple(range(5201, 5206)),
            remote_probe="192.168.1.1",
            devolo_ip="192.168.1.20",
            min_upload_mbps=100.0,
            min_download_mbps=80.0,
            fail_limit=1,
            action="reboot",
            initial_delay_seconds=0,
        )

        exit_code = run_daemon(cfg, once=True)
        self.assertEqual(exit_code, 1)
        mock_reboot.assert_called_once_with(cfg)


if __name__ == "__main__":
    unittest.main()
