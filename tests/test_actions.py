from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from devolo_watchdog.actions import read_password, restart_devolo
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


class PasswordFileTests(unittest.TestCase):
    def test_none_path_returns_none(self):
        self.assertIsNone(read_password(None))

    def test_reads_and_strips_password(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write(" secret_pass \n")
            tf.flush()
            self.assertEqual(read_password(tf.name), "secret_pass")
        os.unlink(tf.name)

    def test_missing_password_file_raises_value_error(self):
        with self.assertRaises(ValueError):
            read_password("/path/to/non_existent_file.password")


class ActionsTests(unittest.TestCase):
    @patch("devolo_plc_api.Device")
    def test_restart_devolo_success(self, mock_device_cls):
        mock_inst = MagicMock()
        mock_device_cls.return_value = mock_inst
        mock_inst.__aenter__.return_value = mock_inst

        async def fake_restart():
            return True

        mock_inst.device.async_restart = fake_restart

        cfg = make_settings()
        self.assertTrue(restart_devolo(cfg))

    @patch("devolo_plc_api.Device")
    def test_restart_devolo_missing_device_api_raises(self, mock_device_cls):
        mock_inst = MagicMock()
        mock_device_cls.return_value = mock_inst
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.device = None

        cfg = make_settings()
        with self.assertRaises(RuntimeError):
            restart_devolo(cfg)
