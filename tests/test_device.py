from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from devolo_watchdog.device import load_device_class


class DeviceLoaderTests(unittest.TestCase):
    @patch("devolo_watchdog.device.import_module")
    def test_load_device_class_returns_callable_export(self, mock_import):
        device_class = MagicMock()
        mock_import.return_value = SimpleNamespace(Device=device_class)

        self.assertIs(load_device_class(), device_class)
        mock_import.assert_called_once_with("devolo_plc_api")

    @patch("devolo_watchdog.device.import_module")
    def test_load_device_class_rejects_missing_export(self, mock_import):
        mock_import.return_value = SimpleNamespace(Device=None)

        with self.assertRaisesRegex(ImportError, "Device is unavailable"):
            load_device_class()
