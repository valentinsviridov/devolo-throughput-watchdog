from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from devolo_watchdog.config import Settings
from devolo_watchdog.notifications import (
    Notification,
    degradation_notification,
    pre_reboot_notification,
    send_ntfy_notification,
)


def make_settings(**kwargs) -> Settings:
    defaults = {
        "iperf_server": "iperf.example.com",
        "iperf_ports": tuple(range(5201, 5206)),
        "remote_probe": "192.168.1.1",
        "devolo_ip": "192.168.1.20",
        "min_upload_mbps": 100.0,
        "min_download_mbps": 80.0,
        "ntfy_url": "https://ntfy.example.com/watchdog-alerts",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


class NtfyAdapterTests(unittest.TestCase):
    @patch("devolo_watchdog.notifications.urlopen")
    def test_notification_is_published_with_metadata(self, mock_urlopen):
        response = MagicMock(status=200)
        mock_urlopen.return_value.__enter__.return_value = response
        notification = Notification(
            event="degradation_detected",
            title="Network degradation detected",
            message="PLC PHY link is slow",
            priority="high",
            tags="warning,signal_strength",
        )

        send_ntfy_notification(make_settings(ntfy_timeout_seconds=2.5), notification)

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://ntfy.example.com/watchdog-alerts")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.data, b"PLC PHY link is slow")
        self.assertEqual(request.get_header("Title"), "Network degradation detected")
        self.assertEqual(request.get_header("Priority"), "high")
        self.assertEqual(request.get_header("Tags"), "warning,signal_strength")
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 2.5)

    @patch("devolo_watchdog.notifications.urlopen")
    def test_bearer_token_is_read_from_file(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value = MagicMock(status=200)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as token_file:
            token_file.write("secret-token\n")
            token_file.flush()

            send_ntfy_notification(
                make_settings(ntfy_token_file=token_file.name),
                degradation_notification("slow", 1, 3),
            )

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")

    @patch("devolo_watchdog.notifications.urlopen")
    def test_disabled_notification_is_a_no_op(self, mock_urlopen):
        send_ntfy_notification(
            make_settings(ntfy_url=None),
            degradation_notification("slow", 1, 3),
        )

        mock_urlopen.assert_not_called()

    def test_empty_token_file_is_rejected(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as token_file:
            with self.assertRaisesRegex(ValueError, "DW_NTFY_TOKEN_FILE is empty"):
                send_ntfy_notification(
                    make_settings(ntfy_token_file=token_file.name),
                    degradation_notification("slow", 1, 3),
                )

    def test_missing_token_file_is_rejected(self):
        missing_path = str(Path(tempfile.gettempdir()) / "missing-watchdog-ntfy-token")
        with self.assertRaisesRegex(ValueError, "DW_NTFY_TOKEN_FILE unreadable"):
            send_ntfy_notification(
                make_settings(ntfy_token_file=missing_path),
                degradation_notification("slow", 1, 3),
            )

    @patch("devolo_watchdog.notifications.urlopen")
    def test_unexpected_http_status_is_rejected(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value = MagicMock(status=500)

        with self.assertRaisesRegex(RuntimeError, "ntfy returned HTTP 500"):
            send_ntfy_notification(
                make_settings(),
                degradation_notification("slow", 1, 3),
            )


class NotificationContentTests(unittest.TestCase):
    def test_degradation_content_includes_streak(self):
        notification = degradation_notification("PLC PHY link is slow", 2, 3)

        self.assertEqual(notification.event, "degradation_detected")
        self.assertIn("PLC PHY link is slow", notification.message)
        self.assertIn("2/3", notification.message)
        self.assertEqual(notification.priority, "high")

    def test_pre_reboot_content_identifies_device_and_reason(self):
        notification = pre_reboot_notification("192.168.1.20", "Fail limit reached")

        self.assertEqual(notification.event, "pre_reboot")
        self.assertIn("192.168.1.20", notification.message)
        self.assertIn("Fail limit reached", notification.message)
        self.assertEqual(notification.priority, "max")
