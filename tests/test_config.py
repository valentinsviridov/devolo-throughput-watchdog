from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from devolo_watchdog.config import Settings, candidate_ports, parse_ports


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

    def test_direction_uses_different_rotated_ports(self):
        cfg = make_settings()
        forward = candidate_ports(cfg, False, now=0)
        reverse = candidate_ports(cfg, True, now=0)
        self.assertEqual(forward, (5201, 5202, 5203, 5204, 5205))
        self.assertEqual(reverse, (5203, 5204, 5205, 5201, 5202))
        self.assertNotEqual(forward[0], reverse[0])


class SettingsValidationTests(unittest.TestCase):
    def test_direct_construction_runs_post_init_validation(self):
        with self.assertRaises(ValueError):
            Settings(
                iperf_server="iperf.example.com",
                iperf_ports=(5201,),
                remote_probe="192.168.1.1",
                devolo_ip="192.168.1.20",
                min_upload_mbps=100.0,
                min_download_mbps=80.0,
                max_reboot_attempts=0,  # Invalid!
            )

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
            "DW_MAX_REBOOT_ATTEMPTS": "5",
            "DW_POST_REBOOT_DELAY_SECONDS": "60",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = Settings.from_env()
            self.assertEqual(cfg.remote_probe, "192.168.1.1")
            self.assertEqual(cfg.devolo_ip, "192.168.1.20")
            self.assertEqual(cfg.max_reboot_attempts, 5)
            self.assertEqual(cfg.post_reboot_delay_seconds, 60)

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
