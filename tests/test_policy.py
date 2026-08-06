from __future__ import annotations

import unittest

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
from devolo_watchdog.policy import evaluate_report, plc_phy_is_degraded, transition


def make_settings(**kwargs) -> Settings:
    defaults = {
        "iperf_server": "iperf.example.com",
        "iperf_ports": tuple(range(5201, 5206)),
        "remote_probe": "192.168.1.1",
        "devolo_ip": "192.168.1.20",
        "min_upload_mbps": 100.0,
        "min_download_mbps": 80.0,
        "fail_limit": 3,
        "fail_window_seconds": 3600,
        "action": "reboot",
        "require_plc_evidence_for_reboot": True,
    }
    defaults.update(kwargs)
    return Settings(**defaults)


class PolicyEvaluationTests(unittest.TestCase):
    def test_unreachable_gateway_returns_unavailable(self):
        st = make_settings()
        gw_res = GatewayProbeResult(reachable=False, error="ping timeout")
        report = MeasurementReport(gateway=gw_res)
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.UNAVAILABLE)

    def test_missing_ping_returns_misconfigured(self):
        st = make_settings()
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=False, error="ping binary missing")
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.MISCONFIGURED)

    def test_wan_slowness_without_plc_evidence_returns_unavailable(self):
        st = make_settings(require_plc_evidence_for_reboot=True)
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertIn("no PLC-specific evidence", res.reason)

    def test_wan_slowness_with_healthy_plc_phy_returns_unavailable(self):
        st = make_settings()
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            plc_phy=PlcPhyResult(rx_rate_mbps=200.0, tx_rate_mbps=200.0),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertIn("verified healthy", res.reason)

    def test_degraded_plc_phy_returns_degraded(self):
        st = make_settings(min_plc_phy_rate_mbps=50.0)
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            plc_phy=PlcPhyResult(rx_rate_mbps=20.0, tx_rate_mbps=20.0),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.DEGRADED)

    def test_wan_iperf_error_with_healthy_plc_phy_returns_unavailable(self):
        st = make_settings()
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            plc_phy=PlcPhyResult(rx_rate_mbps=200.0, tx_rate_mbps=200.0),
            wan_iperf=WanIperfResult(error="all candidate ports rejected"),
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertIn("local PLC link is verified healthy", res.reason)

    def test_wan_iperf_error_without_plc_phy_returns_unavailable(self):
        st = make_settings()
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(error="connection refused"),
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertIn("iperf test failed/unavailable", res.reason)

    def test_wan_slowness_without_plc_requirement_returns_degraded(self):
        st = make_settings(require_plc_evidence_for_reboot=False)
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.DEGRADED)
        self.assertIn("upload 10.0 < 100.0 Mbit/s", res.reason)

    def test_wan_slowness_with_degraded_plc_phy_returns_degraded(self):
        st = make_settings(min_plc_phy_rate_mbps=50.0)
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            plc_phy=PlcPhyResult(rx_rate_mbps=10.0, tx_rate_mbps=10.0),
            wan_iperf=WanIperfResult(upload_mbps=10.0, download_mbps=10.0),
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.DEGRADED)
        self.assertIn("PLC PHY link rate degraded", res.reason)

    def test_wan_nan_or_none_rates(self):
        st = make_settings(require_plc_evidence_for_reboot=False)
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=None, download_mbps=float("nan")),
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.DEGRADED)
        self.assertIn("upload failed", res.reason)

    def test_healthy_wan_iperf_returns_healthy(self):
        st = make_settings()
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            wan_iperf=WanIperfResult(upload_mbps=150.0, download_mbps=120.0),
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.HEALTHY)
        self.assertIn("above configured thresholds", res.reason)

    def test_no_iperf_healthy_plc_returns_healthy(self):
        st = make_settings()
        report = MeasurementReport(
            gateway=GatewayProbeResult(reachable=True),
            plc_phy=PlcPhyResult(rx_rate_mbps=200.0, tx_rate_mbps=200.0),
        )
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.HEALTHY)
        self.assertIn("Local PLC link verified healthy", res.reason)

    def test_no_iperf_no_plc_returns_unavailable(self):
        st = make_settings()
        report = MeasurementReport(gateway=GatewayProbeResult(reachable=True))
        res = evaluate_report(report, st)
        self.assertEqual(res.status, Status.UNAVAILABLE)
        self.assertIn("No throughput tests were performed", res.reason)


class PlcPhyIsDegradedTests(unittest.TestCase):
    def test_degraded_when_rates_below_threshold(self):
        plc = PlcPhyResult(rx_rate_mbps=30.0, tx_rate_mbps=50.0)
        self.assertTrue(plc_phy_is_degraded(plc, 50.0))

    def test_not_degraded_when_rates_meet_threshold(self):
        plc = PlcPhyResult(rx_rate_mbps=100.0, tx_rate_mbps=80.0)
        self.assertFalse(plc_phy_is_degraded(plc, 50.0))

    def test_not_degraded_when_plc_is_none(self):
        self.assertFalse(plc_phy_is_degraded(None, 50.0))

    def test_not_degraded_when_rates_are_none(self):
        plc = PlcPhyResult(rx_rate_mbps=None, tx_rate_mbps=None)
        self.assertFalse(plc_phy_is_degraded(plc, 50.0))

    def test_not_degraded_when_unreachable(self):
        plc = PlcPhyResult(rx_rate_mbps=10.0, tx_rate_mbps=10.0, reachable=False)
        self.assertFalse(plc_phy_is_degraded(plc, 50.0))


class TransitionTests(unittest.TestCase):
    def test_unavailable_resets_consecutive_failure_streak(self):
        st = make_settings(fail_limit=3)
        state = WatchdogState(
            degradation_notification_sent=True,
            degraded_timestamps=[900.0, 950.0],
        )
        now = 1000.0

        result = CycleResult(status=Status.UNAVAILABLE, reason="busy")
        new_state, action, _ = transition(state, result, st, now)
        self.assertFalse(new_state.degradation_notification_sent)
        self.assertEqual(len(new_state.degraded_timestamps), 2)
        self.assertEqual(action, ActionType.NONE)

    def test_unavailable_does_not_clear_degradation_window(self):
        st = make_settings(fail_limit=3, fail_window_seconds=3600)
        state = WatchdogState(degraded_timestamps=[1000.0])
        now = 1050.0

        result = CycleResult(status=Status.UNAVAILABLE, reason="busy")
        new_state, _, _ = transition(state, result, st, now)

        self.assertEqual(new_state.degraded_timestamps, [1000.0])

    def test_healthy_clears_degradation_window(self):
        st = make_settings(fail_limit=3, fail_window_seconds=3600)
        state = WatchdogState(degraded_timestamps=[1000.0, 1050.0])
        now = 1100.0

        result = CycleResult(status=Status.HEALTHY, reason="ok")
        new_state, _, _ = transition(state, result, st, now)

        self.assertEqual(new_state.degraded_timestamps, [])

    def test_sliding_window_triggers_reboot_across_unavailable_gaps(self):
        st = make_settings(fail_limit=3, fail_window_seconds=3600)
        state = WatchdogState()

        # t=0 DEGRADED
        state, _, _ = transition(state, CycleResult(status=Status.DEGRADED, reason="1"), st, 0.0)
        # t=150 UNAVAILABLE
        state, _, _ = transition(
            state, CycleResult(status=Status.UNAVAILABLE, reason="2"), st, 150.0
        )
        # t=600 DEGRADED
        state, _, _ = transition(state, CycleResult(status=Status.DEGRADED, reason="3"), st, 600.0)
        # t=750 UNAVAILABLE
        state, _, _ = transition(
            state, CycleResult(status=Status.UNAVAILABLE, reason="4"), st, 750.0
        )
        # t=1200 DEGRADED
        state, action, _ = transition(
            state, CycleResult(status=Status.DEGRADED, reason="5"), st, 1200.0
        )

        self.assertEqual(action, ActionType.REBOOT)
        self.assertEqual(len(state.degraded_timestamps), 3)

    def test_degraded_timestamps_pruned_outside_window(self):
        st = make_settings(fail_limit=3, fail_window_seconds=3600)
        state = WatchdogState(degraded_timestamps=[100.0, 2000.0])
        now = 4000.0  # 4000 - 3600 = 400 (cutoff), so 100 should drop, 2000 stays

        result = CycleResult(status=Status.DEGRADED, reason="slow")
        new_state, action, _ = transition(state, result, st, now)

        self.assertEqual(new_state.degraded_timestamps, [2000.0, 4000.0])
        self.assertEqual(action, ActionType.NONE)

    def test_window_entries_age_out_without_action(self):
        st = make_settings(fail_limit=3, fail_window_seconds=3600)
        # t=100, 200 DEGRADED
        state = WatchdogState(degraded_timestamps=[100.0, 200.0])
        now = 3900.0  # cutoff = 300, so both drop. New entry makes 1

        result = CycleResult(status=Status.DEGRADED, reason="slow")
        new_state, action, _ = transition(state, result, st, now)

        self.assertEqual(new_state.degraded_timestamps, [3900.0])
        self.assertEqual(action, ActionType.NONE)

    def test_healthy_resets_breaker_tripped(self):
        st = make_settings(fail_limit=3)
        state = WatchdogState(breaker_tripped=True)
        now = 1000.0

        result = CycleResult(status=Status.HEALTHY, reason="ok")
        new_state, action, _ = transition(state, result, st, now)
        self.assertFalse(new_state.breaker_tripped)
        self.assertEqual(action, ActionType.NONE)

    def test_action_log_when_fail_limit_reached(self):
        st = make_settings(fail_limit=2, action="log")
        state = WatchdogState(degraded_timestamps=[900.0])
        now = 1000.0

        result = CycleResult(status=Status.DEGRADED, reason="slow")
        new_state, action, reason = transition(state, result, st, now)
        self.assertEqual(action, ActionType.LOG)
        self.assertIn("action=log", reason)

    def test_circuit_breaker_window_rate_limits_reboots(self):
        st = make_settings(fail_limit=1, max_reboots_in_window=3, reboot_window_hours=6.0)
        state = WatchdogState()
        now = 10000.0

        state.record_reboot(now - 100, accepted=True, reason="r1")
        state.record_reboot(now - 50, accepted=True, reason="r2")
        state.record_reboot(now - 10, accepted=True, reason="r3")

        result = CycleResult(status=Status.DEGRADED, reason="slow")
        new_state, action, reason = transition(state, result, st, now)

        self.assertEqual(action, ActionType.NONE)
        self.assertTrue(new_state.breaker_tripped)
        self.assertIn("Circuit breaker active", reason)

    def test_circuit_breaker_rearms_after_window_expires(self):
        st = make_settings(fail_limit=1, max_reboots_in_window=1, reboot_window_hours=6.0)
        now = 100_000.0
        state = WatchdogState(breaker_tripped=True)
        state.record_reboot(now - (7 * 3600), accepted=True, reason="old attempt")

        result = CycleResult(status=Status.DEGRADED, reason="slow")
        new_state, action, _ = transition(state, result, st, now)

        self.assertEqual(action, ActionType.REBOOT)
        self.assertFalse(new_state.breaker_tripped)

    def test_history_is_retained_for_windows_longer_than_seven_days(self):
        st = make_settings(fail_limit=1, max_reboots_in_window=1, reboot_window_hours=240.0)
        now = 1_000_000.0
        state = WatchdogState()
        state.record_reboot(now - (8 * 86400), accepted=True, reason="recent in long window")

        result = CycleResult(status=Status.DEGRADED, reason="slow")
        new_state, action, _ = transition(state, result, st, now)

        self.assertEqual(action, ActionType.NONE)
        self.assertTrue(new_state.breaker_tripped)
        self.assertEqual(len(new_state.reboot_history), 1)
