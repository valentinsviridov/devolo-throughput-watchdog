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
from devolo_watchdog.policy import evaluate_report, transition


def make_settings(**kwargs) -> Settings:
    defaults = {
        "iperf_server": "iperf.example.com",
        "iperf_ports": tuple(range(5201, 5206)),
        "remote_probe": "192.168.1.1",
        "devolo_ip": "192.168.1.20",
        "min_upload_mbps": 100.0,
        "min_download_mbps": 80.0,
        "fail_limit": 3,
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


class TransitionTests(unittest.TestCase):
    def test_unavailable_resets_consecutive_failure_streak(self):
        st = make_settings(fail_limit=3)
        state = WatchdogState(consecutive_failures=2)
        now = 1000.0

        result = CycleResult(status=Status.UNAVAILABLE, reason="busy")
        new_state, action, _ = transition(state, result, st, now)
        self.assertEqual(new_state.consecutive_failures, 0)
        self.assertEqual(action, ActionType.NONE)

    def test_healthy_resets_breaker_tripped(self):
        st = make_settings(fail_limit=3)
        state = WatchdogState(consecutive_failures=2, breaker_tripped=True)
        now = 1000.0

        result = CycleResult(status=Status.HEALTHY, reason="ok")
        new_state, action, _ = transition(state, result, st, now)
        self.assertFalse(new_state.breaker_tripped)
        self.assertEqual(new_state.consecutive_failures, 0)
        self.assertEqual(action, ActionType.NONE)

    def test_action_log_when_fail_limit_reached(self):
        st = make_settings(fail_limit=2, action="log")
        state = WatchdogState(consecutive_failures=1)
        now = 1000.0

        result = CycleResult(status=Status.DEGRADED, reason="slow")
        new_state, action, reason = transition(state, result, st, now)
        self.assertEqual(action, ActionType.LOG)
        self.assertIn("action=log", reason)

    def test_circuit_breaker_window_rate_limits_reboots(self):
        st = make_settings(fail_limit=1, max_reboots_in_window=3, reboot_window_hours=6.0)
        state = WatchdogState(consecutive_failures=0)
        now = 10000.0

        state.record_reboot(now - 100, accepted=True, reason="r1")
        state.record_reboot(now - 50, accepted=True, reason="r2")
        state.record_reboot(now - 10, accepted=True, reason="r3")

        result = CycleResult(status=Status.DEGRADED, reason="slow")
        new_state, action, reason = transition(state, result, st, now)

        self.assertEqual(action, ActionType.NONE)
        self.assertTrue(new_state.breaker_tripped)
        self.assertIn("Circuit breaker active", reason)
