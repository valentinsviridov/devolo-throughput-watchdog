from __future__ import annotations

import unittest

from devolo_watchdog.models import CycleResult, Status, WatchdogState


class ModelsTests(unittest.TestCase):
    def test_watchdog_state_serialization(self):
        state = WatchdogState(
            consecutive_failures=2,
            last_reboot_timestamp=1000.0,
            breaker_tripped=True,
            last_status=Status.DEGRADED,
            last_reason="Local link slow",
        )
        state.record_reboot(1000.0, accepted=True, reason="Fail limit reached")

        d = state.to_dict()
        self.assertEqual(d["consecutive_failures"], 2)
        self.assertTrue(d["breaker_tripped"])
        self.assertEqual(d["last_status"], "degraded")
        self.assertEqual(len(d["reboot_history"]), 1)

        restored = WatchdogState.from_dict(d)
        self.assertEqual(restored.consecutive_failures, 2)
        self.assertTrue(restored.breaker_tripped)
        self.assertEqual(restored.last_status, Status.DEGRADED)
        self.assertEqual(len(restored.reboot_history), 1)

    def test_recent_reboot_count_and_pruning(self):
        state = WatchdogState()
        now = 10000.0
        state.record_reboot(now - 1000, accepted=True, reason="r1")
        state.record_reboot(now - 500, accepted=True, reason="r2")
        state.record_reboot(now - 100000, accepted=True, reason="r3")

        self.assertEqual(state.recent_reboot_count(now, window_seconds=3600), 2)
        state.prune_history(now, max_age_seconds=5000)
        self.assertEqual(len(state.reboot_history), 2)

    def test_cycle_result_to_dict(self):
        res = CycleResult(
            status=Status.HEALTHY,
            reason="Good",
            upload_mbps=150.0,
            download_mbps=120.0,
            upload_port=5201,
            download_port=5202,
            plc_rx_rate=300.0,
            plc_tx_rate=250.0,
        )
        d = res.to_dict()
        self.assertEqual(d["status"], "healthy")
        self.assertEqual(d["upload_mbps"], 150.0)
        self.assertEqual(d["plc_rx_rate"], 300.0)
