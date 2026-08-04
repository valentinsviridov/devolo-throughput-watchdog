from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from devolo_watchdog.config import Settings
from devolo_watchdog.models import (
    ActionType,
    CycleResult,
    GatewayProbeResult,
    MeasurementReport,
    PlcPhyResult,
    Status,
    WanIperfResult,
    WatchdogState,
)
from devolo_watchdog.runner import (
    LOG,
    RestartPersistenceError,
    collect_measurement_report,
    format_log_timestamp,
    log_result,
    log_startup,
    request_restart,
    run_daemon,
)
from devolo_watchdog.state import StateStore


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
    def test_format_log_timestamp_uses_iso_8601_utc(self):
        self.assertEqual(format_log_timestamp(0.0), "1970-01-01T00:00:00.000Z")

    def test_log_startup_text_format(self):
        with self.assertLogs(LOG.name, level="INFO") as captured:
            log_startup(
                make_settings(initial_delay_seconds=30, log_format="text"),
                once=False,
            )

        self.assertEqual(
            captured.records[0].getMessage(),
            "watchdog started mode=daemon action=reboot initial_delay=30s",
        )

    def test_log_startup_json_format(self):
        with self.assertLogs(LOG.name, level="INFO") as captured:
            log_startup(make_settings(log_format="json"), once=True)

        payload = json.loads(captured.records[0].getMessage())
        self.assertEqual(payload["event"], "watchdog_started")
        self.assertEqual(payload["mode"], "once")
        self.assertEqual(payload["action"], "reboot")
        self.assertEqual(payload["initial_delay_seconds"], 0)
        self.assertIsInstance(payload["timestamp"], str)
        self.assertTrue(payload["timestamp"].endswith("Z"))

    def test_log_result_json_format(self):
        res = CycleResult(
            status=Status.HEALTHY,
            reason="All good",
            upload_mbps=150.0,
            download_mbps=120.0,
            upload_port=5201,
            download_port=5202,
        )
        with self.assertLogs(LOG.name, level="INFO") as captured:
            log_result(res, failures=0, fail_limit=3, action=ActionType.NONE, log_format="json")

        payload = json.loads(captured.records[0].getMessage())
        self.assertTrue(payload["timestamp"].endswith("Z"))
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["metrics"]["upload_mbps"], 150.0)

    def test_log_result_text_omits_unknown_ports(self):
        result = CycleResult(
            status=Status.HEALTHY,
            reason="All good",
            upload_mbps=150.0,
            download_mbps=120.0,
        )

        with self.assertLogs(LOG.name, level="INFO") as captured:
            log_result(result, failures=0, fail_limit=3, action=ActionType.NONE)

        rendered_log = captured.records[0].getMessage()
        self.assertIn("upload=150.0Mbps", rendered_log)
        self.assertIn("download=120.0Mbps", rendered_log)
        self.assertNotIn("@None", rendered_log)

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

    @patch("devolo_watchdog.runner.probe_gateway")
    @patch("devolo_watchdog.runner.probe_plc_phy")
    @patch("devolo_watchdog.runner.probe_wan_iperf")
    def test_unreachable_gateway_short_circuits_expensive_probes(self, mock_wan, mock_plc, mock_gw):
        mock_gw.return_value = GatewayProbeResult(reachable=False, error="unreachable")

        report = collect_measurement_report(make_settings(), now=1000.0)

        self.assertFalse(report.gateway.reachable)
        self.assertIsNone(report.wan_iperf)
        mock_plc.assert_not_called()
        mock_wan.assert_not_called()

    @patch("devolo_watchdog.runner.probe_gateway")
    @patch("devolo_watchdog.runner.probe_plc_phy")
    @patch("devolo_watchdog.runner.probe_wan_iperf")
    def test_degraded_plc_phy_short_circuits_iperf(self, mock_wan, mock_plc, mock_gw):
        mock_gw.return_value = GatewayProbeResult(reachable=True)
        mock_plc.return_value = PlcPhyResult(rx_rate_mbps=10.0, tx_rate_mbps=5.0)

        report = collect_measurement_report(make_settings(min_plc_phy_rate_mbps=50.0), now=1000.0)

        self.assertIsNotNone(report.plc_phy)
        self.assertIsNone(report.wan_iperf)
        mock_wan.assert_not_called()

    @patch("devolo_watchdog.runner.read_password")
    @patch("devolo_watchdog.runner.probe_gateway")
    @patch("devolo_watchdog.runner.probe_plc_phy")
    @patch("devolo_watchdog.runner.probe_wan_iperf")
    def test_invalid_password_file_is_not_forwarded_as_a_literal_password(
        self, mock_wan, mock_plc, mock_gw, mock_read
    ):
        mock_gw.return_value = GatewayProbeResult(reachable=True)
        mock_read.side_effect = ValueError("password file missing")
        mock_wan.return_value = WanIperfResult(upload_mbps=120.0, download_mbps=100.0)

        report = collect_measurement_report(
            make_settings(password_file="/missing/password"), now=1000.0
        )

        self.assertIsNone(report.plc_phy)
        mock_plc.assert_not_called()


class RestartRequestTests(unittest.TestCase):
    @patch("devolo_watchdog.runner.restart_devolo", return_value=True)
    def test_accepted_restart_is_recorded_before_and_after_api_call(self, mock_restart):
        settings = make_settings()
        store = MagicMock(spec=StateStore)
        store.save.return_value = True
        state = WatchdogState()

        accepted = request_restart(
            settings,
            store,
            state,
            now=123.0,
            reason="manual restart command",
        )

        self.assertTrue(accepted)
        mock_restart.assert_called_once_with(settings)
        self.assertEqual(store.save.call_count, 2)
        self.assertEqual(len(state.reboot_history), 1)
        self.assertEqual(state.reboot_history[0].timestamp, 123.0)
        self.assertEqual(state.reboot_history[0].reason, "manual restart command")
        self.assertTrue(state.reboot_history[0].accepted)

    @patch("devolo_watchdog.runner.restart_devolo")
    def test_restart_is_skipped_when_attempt_cannot_be_recorded(self, mock_restart):
        store = MagicMock(spec=StateStore)
        store.save.return_value = False
        state = WatchdogState()

        with self.assertRaisesRegex(RestartPersistenceError, "could not be persisted"):
            request_restart(
                make_settings(),
                store,
                state,
                now=123.0,
                reason="manual restart command",
            )

        mock_restart.assert_not_called()
        self.assertEqual(len(state.reboot_history), 1)
        self.assertFalse(state.reboot_history[0].accepted)

    @patch("devolo_watchdog.runner.restart_devolo", side_effect=RuntimeError("API failed"))
    def test_failed_api_call_remains_counted(self, mock_restart):
        store = MagicMock(spec=StateStore)
        store.save.return_value = True
        state = WatchdogState()

        with self.assertRaisesRegex(RuntimeError, "API failed"):
            request_restart(
                make_settings(),
                store,
                state,
                now=123.0,
                reason="manual restart command",
            )

        mock_restart.assert_called_once()
        store.save.assert_called_once_with(state)
        self.assertFalse(state.reboot_history[0].accepted)

    @patch("devolo_watchdog.runner.restart_devolo")
    @patch("devolo_watchdog.runner.send_ntfy_notification")
    def test_pre_reboot_notification_is_sent_after_persistence_and_before_api_call(
        self, mock_notify, mock_restart
    ):
        events: list[str] = []
        store = MagicMock(spec=StateStore)
        store.save.side_effect = lambda _state: events.append("save") or True
        mock_notify.side_effect = lambda *_args: events.append("notify")
        mock_restart.side_effect = lambda _settings: events.append("restart") or True

        request_restart(
            make_settings(ntfy_url="https://ntfy.example.com/watchdog-alerts"),
            store,
            WatchdogState(),
            now=123.0,
            reason="automatic recovery",
        )

        self.assertEqual(events, ["save", "notify", "restart", "save"])
        notification = mock_notify.call_args.args[1]
        self.assertEqual(notification.event, "pre_reboot")

    @patch("devolo_watchdog.runner.restart_devolo", return_value=True)
    @patch(
        "devolo_watchdog.runner.send_ntfy_notification",
        side_effect=TimeoutError("notification timed out"),
    )
    def test_notification_failure_does_not_block_restart(self, mock_notify, mock_restart):
        store = MagicMock(spec=StateStore)
        store.save.return_value = True

        with self.assertLogs(LOG.name, level="WARNING") as captured:
            accepted = request_restart(
                make_settings(ntfy_url="https://ntfy.example.com/watchdog-alerts"),
                store,
                WatchdogState(),
                now=123.0,
                reason="automatic recovery",
            )

        self.assertTrue(accepted)
        mock_notify.assert_called_once()
        mock_restart.assert_called_once()
        self.assertIn("notification event=pre_reboot result=error", captured.output[0])


class DaemonExecutionTests(unittest.TestCase):
    @patch("devolo_watchdog.runner.log_startup")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_run_daemon_logs_startup(self, mock_collect, mock_startup):
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=150.0, download_mbps=150.0),
        )
        cfg = make_settings()

        run_daemon(cfg, once=True)

        mock_startup.assert_called_once_with(cfg, once=True)

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

    @patch("devolo_watchdog.runner.send_ntfy_notification")
    @patch("devolo_watchdog.runner.StateStore.load")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_new_degradation_episode_sends_one_notification(
        self, mock_collect, mock_load, mock_notify
    ):
        mock_load.return_value = WatchdogState(last_status=Status.HEALTHY)
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )

        run_daemon(
            make_settings(ntfy_url="https://ntfy.example.com/watchdog-alerts"),
            once=True,
        )

        mock_notify.assert_called_once()
        notification = mock_notify.call_args.args[1]
        self.assertEqual(notification.event, "degradation_detected")
        self.assertTrue(mock_load.return_value.degradation_notification_sent)

    @patch("devolo_watchdog.runner.send_ntfy_notification")
    @patch("devolo_watchdog.runner.StateStore.load")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_continuing_degradation_does_not_repeat_notification(
        self, mock_collect, mock_load, mock_notify
    ):
        mock_load.return_value = WatchdogState(
            consecutive_failures=1,
            degradation_notification_sent=True,
            last_status=Status.DEGRADED,
        )
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )

        run_daemon(
            make_settings(ntfy_url="https://ntfy.example.com/watchdog-alerts"),
            once=True,
        )

        mock_notify.assert_not_called()

    @patch(
        "devolo_watchdog.runner.send_ntfy_notification",
        side_effect=TimeoutError("notification timed out"),
    )
    @patch("devolo_watchdog.runner.StateStore.load")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_failed_degradation_notification_remains_eligible_for_retry(
        self, mock_collect, mock_load, mock_notify
    ):
        mock_load.return_value = WatchdogState(last_status=Status.DEGRADED)
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )

        run_daemon(
            make_settings(ntfy_url="https://ntfy.example.com/watchdog-alerts"),
            once=True,
        )

        mock_notify.assert_called_once()
        self.assertFalse(mock_load.return_value.degradation_notification_sent)

    @patch("devolo_watchdog.runner.send_ntfy_notification")
    @patch("devolo_watchdog.runner.StateStore.load")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_recovery_after_degradation_sends_notification(
        self, mock_collect, mock_load, mock_notify
    ):
        mock_load.return_value = WatchdogState(
            consecutive_failures=1,
            degradation_notification_sent=True,
            last_status=Status.DEGRADED,
        )
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=500.0, download_mbps=500.0),
        )

        run_daemon(
            make_settings(ntfy_url="https://ntfy.example.com/watchdog-alerts"),
            once=True,
        )

        mock_notify.assert_called_once()
        notification = mock_notify.call_args.args[1]
        self.assertEqual(notification.event, "degradation_resolved")

    @patch("devolo_watchdog.runner.send_ntfy_notification")
    @patch("devolo_watchdog.runner.StateStore.load")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_healthy_without_prior_degradation_does_not_send_recovery(
        self, mock_collect, mock_load, mock_notify
    ):
        mock_load.return_value = WatchdogState(
            consecutive_failures=0,
            degradation_notification_sent=False,
            last_status=Status.HEALTHY,
        )
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=500.0, download_mbps=500.0),
        )

        run_daemon(
            make_settings(ntfy_url="https://ntfy.example.com/watchdog-alerts"),
            once=True,
        )

        mock_notify.assert_not_called()

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

    @patch("devolo_watchdog.runner.request_restart", return_value=False)
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_automated_reboot_uses_shared_restart_path(self, mock_collect, mock_restart):
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )
        settings = make_settings()

        exit_code = run_daemon(settings, once=True, allow_action=True)

        self.assertEqual(exit_code, 1)
        mock_restart.assert_called_once()
        self.assertIs(mock_restart.call_args.args[0], settings)
        self.assertIn("upload 10.0", mock_restart.call_args.kwargs["reason"])

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

    @patch("devolo_watchdog.runner.StateStore.save", side_effect=[True, False])
    @patch("devolo_watchdog.runner.restart_devolo")
    @patch("devolo_watchdog.runner.collect_measurement_report")
    def test_reboot_is_skipped_when_attempt_cannot_be_persisted(
        self, mock_collect, mock_reboot, mock_save
    ):
        mock_collect.return_value = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )

        exit_code = run_daemon(
            make_settings(state_file="/tmp/state.json"), once=True, allow_action=True
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(mock_save.call_count, 2)
        mock_reboot.assert_not_called()

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
