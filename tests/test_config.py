from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from devolo_watchdog.config import (
    Settings,
    candidate_ports,
    heartbeat_max_age_seconds_from_env,
    parse_ports,
)


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


class PortTests(unittest.TestCase):
    def test_ranges_lists_and_duplicates(self):
        self.assertEqual(parse_ports("5201-5203,5203,5210"), (5201, 5202, 5203, 5210))

    def test_descending_range_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_ports("5203-5201")

    def test_invalid_port_range_non_numeric(self):
        with self.assertRaises(ValueError):
            parse_ports("5201-abc")

    def test_invalid_port_non_numeric(self):
        with self.assertRaises(ValueError):
            parse_ports("invalid_port")

    def test_empty_port_spec_raises(self):
        with self.assertRaises(ValueError):
            parse_ports("  ,  ")

    def test_out_of_range_port_raises(self):
        with self.assertRaises(ValueError):
            parse_ports("0")
        with self.assertRaises(ValueError):
            parse_ports("70000")

    def test_direction_uses_different_rotated_ports(self):
        cfg = make_settings()
        forward = candidate_ports(cfg, False, now=0)
        reverse = candidate_ports(cfg, True, now=0)
        self.assertEqual(forward, (5201, 5202, 5203, 5204, 5205))
        self.assertEqual(reverse, (5203, 5204, 5205, 5201, 5202))
        self.assertNotEqual(forward[0], reverse[0])


class SettingsValidationTests(unittest.TestCase):
    def test_json_is_the_default_log_format(self):
        self.assertEqual(make_settings().log_format, "json")

    def test_direct_construction_runs_post_init_validation(self):
        with self.assertRaises(ValueError):
            Settings(
                iperf_server="iperf.example.com",
                iperf_ports=(5201,),
                remote_probe="192.168.1.1",
                devolo_ip="192.168.1.20",
                min_upload_mbps=100.0,
                min_download_mbps=80.0,
                max_reboots_in_window=0,
            )

    def test_empty_iperf_server_raises(self):
        with self.assertRaises(ValueError) as cm:
            make_settings(iperf_server="")
        self.assertIn("DW_IPERF_SERVER cannot be empty", str(cm.exception))

    def test_invalid_log_format_raises(self):
        with self.assertRaises(ValueError) as cm:
            make_settings(log_format="yaml")
        self.assertIn("DW_LOG_FORMAT must be", str(cm.exception))

    def test_invalid_test_bytes_format_raises(self):
        with self.assertRaises(ValueError) as cm:
            make_settings(test_bytes="invalid")
        self.assertIn("DW_TEST_BYTES must look like", str(cm.exception))

    def test_negative_or_nan_thresholds_raise(self):
        with self.assertRaises(ValueError):
            make_settings(min_upload_mbps=0.0)
        with self.assertRaises(ValueError):
            make_settings(min_download_mbps=-10.0)
        with self.assertRaises(ValueError):
            make_settings(min_upload_mbps=float("nan"))

    def test_non_finite_policy_thresholds_raise(self):
        with self.assertRaises(ValueError):
            make_settings(reboot_window_hours=float("nan"))
        with self.assertRaises(ValueError):
            make_settings(min_plc_phy_rate_mbps=float("inf"))

    def test_direct_construction_rejects_invalid_ports_and_hosts(self):
        with self.assertRaises(ValueError):
            make_settings(iperf_ports=(0,), iperf_tries=1)
        with self.assertRaises(ValueError):
            make_settings(remote_probe="")

    def test_tries_exceeding_ports_count_raises(self):
        with self.assertRaises(ValueError) as cm:
            make_settings(iperf_ports=(5201, 5202), iperf_tries=5)
        self.assertIn("DW_IPERF_TRIES cannot exceed", str(cm.exception))

    def test_negative_delay_raises(self):
        with self.assertRaises(ValueError):
            make_settings(initial_delay_seconds=-1)

    def test_notification_url_must_be_absolute_http_url(self):
        with self.assertRaisesRegex(ValueError, "DW_NTFY_URL"):
            make_settings(ntfy_url="ntfy.sh/watchdog")
        with self.assertRaisesRegex(ValueError, "DW_NTFY_URL"):
            make_settings(ntfy_url="file:///tmp/watchdog")

    def test_notification_token_requires_url(self):
        with self.assertRaisesRegex(ValueError, "DW_NTFY_TOKEN_FILE requires"):
            make_settings(ntfy_token_file="/run/secrets/ntfy-token")

    def test_notification_timeout_must_be_finite_and_positive(self):
        with self.assertRaisesRegex(ValueError, "DW_NTFY_TIMEOUT_SECONDS"):
            make_settings(ntfy_timeout_seconds=0.0)
        with self.assertRaisesRegex(ValueError, "DW_NTFY_TIMEOUT_SECONDS"):
            make_settings(ntfy_timeout_seconds=float("nan"))

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
            "DW_POST_REBOOT_DELAY_SECONDS": "60",
            "DW_LOG_FORMAT": "json",
            "DW_NTFY_URL": "https://ntfy.example.com/watchdog-alerts",
            "DW_NTFY_TOKEN_FILE": "/run/secrets/ntfy-token",
            "DW_NTFY_TIMEOUT_SECONDS": "2.5",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = Settings.from_env()
            self.assertEqual(cfg.remote_probe, "192.168.1.1")
            self.assertEqual(cfg.devolo_ip, "192.168.1.20")
            self.assertEqual(cfg.max_reboots_in_window, 3)
            self.assertEqual(cfg.post_reboot_delay_seconds, 60)
            self.assertEqual(cfg.log_format, "json")
            self.assertEqual(cfg.ntfy_url, "https://ntfy.example.com/watchdog-alerts")
            self.assertEqual(cfg.ntfy_token_file, "/run/secrets/ntfy-token")
            self.assertEqual(cfg.ntfy_timeout_seconds, 2.5)

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

    def test_invalid_safety_boolean_raises_instead_of_failing_open(self):
        env = {
            "DW_REMOTE_PROBE": "192.168.1.1",
            "DW_DEVOLO_IP": "192.168.1.20",
            "DW_MIN_UPLOAD_MBPS": "100",
            "DW_MIN_DOWNLOAD_MBPS": "100",
            "DW_REQUIRE_PLC_EVIDENCE_FOR_REBOOT": "perhaps",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "DW_REQUIRE_PLC_EVIDENCE_FOR_REBOOT"):
                Settings.from_env()

    def test_invalid_numeric_env_has_contextual_error(self):
        env = {
            "DW_REMOTE_PROBE": "192.168.1.1",
            "DW_DEVOLO_IP": "192.168.1.20",
            "DW_MIN_UPLOAD_MBPS": "fast",
            "DW_MIN_DOWNLOAD_MBPS": "100",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "DW_MIN_UPLOAD_MBPS must be a number"):
                Settings.from_env()

    def test_heartbeat_max_age_defaults_to_twice_interval(self):
        with patch.dict(os.environ, {"DW_INTERVAL_SECONDS": "600"}, clear=True):
            self.assertEqual(heartbeat_max_age_seconds_from_env(), 1200.0)

        with patch.dict(
            os.environ,
            {"DW_INTERVAL_SECONDS": "600", "DW_HEARTBEAT_MAX_AGE_SECONDS": "75"},
            clear=True,
        ):
            self.assertEqual(heartbeat_max_age_seconds_from_env(), 75.0)

        with patch.dict(os.environ, {"DW_INTERVAL_SECONDS": "0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "DW_INTERVAL_SECONDS"):
                heartbeat_max_age_seconds_from_env()
