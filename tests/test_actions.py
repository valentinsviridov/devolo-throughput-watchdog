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

    def test_empty_password_file_raises_value_error(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            path = tf.name
        try:
            with self.assertRaisesRegex(ValueError, "is empty"):
                read_password(path)
        finally:
            os.unlink(path)

    def test_missing_password_file_raises_value_error(self):
        with self.assertRaises(ValueError):
            read_password("/path/to/non_existent_file.password")

    @patch("pathlib.Path.read_text")
    def test_permission_error_on_password_file_raises_value_error(self, mock_read):
        mock_read.side_effect = PermissionError("Access denied")
        with self.assertRaises(ValueError) as cm:
            read_password("/etc/shadow_password")
        self.assertIn("DW_PASSWORD_FILE unreadable", str(cm.exception))


class ActionsTests(unittest.TestCase):
    @patch("devolo_watchdog.actions.Device")
    def test_restart_devolo_success(self, mock_device_cls):
        mock_inst = MagicMock()
        mock_device_cls.return_value = mock_inst
        mock_inst.__aenter__.return_value = mock_inst

        async def fake_restart():
            return True

        mock_inst.device.async_restart = fake_restart

        cfg = make_settings()
        self.assertTrue(restart_devolo(cfg))

    @patch("devolo_watchdog.actions.read_password")
    def test_restart_devolo_with_password(self, mock_pw):
        mock_pw.return_value = "my_secret_pass"
        mock_device_cls = MagicMock()
        mock_inst = MagicMock()
        mock_device_cls.return_value = mock_inst
        mock_inst.__aenter__.return_value = mock_inst

        async def fake_restart():
            return True

        mock_inst.device.async_restart = fake_restart

        cfg = make_settings(password_file="/tmp/pw.txt")
        self.assertTrue(restart_devolo(cfg, device_class=mock_device_cls))
        self.assertEqual(mock_inst.password, "my_secret_pass")

    def test_restart_devolo_missing_device_api_raises(self):
        mock_device_cls = MagicMock()
        mock_inst = MagicMock()
        mock_device_cls.return_value = mock_inst
        mock_inst.__aenter__.return_value = mock_inst
        mock_inst.device = None

        cfg = make_settings()
        with self.assertRaises(RuntimeError):
            restart_devolo(cfg, device_class=mock_device_cls)

    def test_restart_devolo_rejects_suppressed_context_error(self):
        class SuppressingDevice:
            device = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return True

        with self.assertRaisesRegex(RuntimeError, "context suppressed the restart failure"):
            restart_devolo(
                make_settings(),
                device_class=lambda **_kwargs: SuppressingDevice(),
            )
