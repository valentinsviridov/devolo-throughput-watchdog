"""Pure evaluation policy and state transition logic."""

from __future__ import annotations

import logging
import math

from devolo_watchdog.config import Settings
from devolo_watchdog.models import (
    ActionType,
    CycleResult,
    MeasurementReport,
    Status,
    WatchdogState,
)

LOG = logging.getLogger("devolo-throughput-watchdog")


def evaluate_report(report: MeasurementReport, settings: Settings) -> CycleResult:
    """Evaluate a MeasurementReport against policy thresholds."""

    # 1. Gateway probe check
    if not report.gateway.reachable:
        if report.gateway.error and "missing" in report.gateway.error.lower():
            return CycleResult(
                status=Status.MISCONFIGURED,
                reason=f"Gateway monitoring misconfigured: {report.gateway.error}",
            )
        err = report.gateway.error or "ping failed"
        return CycleResult(
            status=Status.UNAVAILABLE,
            reason=f"Local gateway ({settings.remote_probe}) is unreachable: {err}",
        )

    # 2. Check PLC PHY rates if available
    phy_healthy: bool | None = None
    if report.plc_phy is not None and report.plc_phy.reachable:
        rx = report.plc_phy.rx_rate_mbps
        tx = report.plc_phy.tx_rate_mbps
        if rx is not None and tx is not None:
            if rx < settings.min_plc_phy_rate_mbps or tx < settings.min_plc_phy_rate_mbps:
                phy_healthy = False
                return CycleResult(
                    status=Status.DEGRADED,
                    reason=(
                        f"PLC PHY link rate degraded (rx={rx:.1f}Mbps, tx={tx:.1f}Mbps < "
                        f"{settings.min_plc_phy_rate_mbps:.1f}Mbps)"
                    ),
                    plc_rx_rate=rx,
                    plc_tx_rate=tx,
                )
            else:
                phy_healthy = True

    # 3. Check iperf probe
    if report.wan_iperf is not None:
        if report.wan_iperf.error and (
            report.wan_iperf.upload_mbps is None or report.wan_iperf.download_mbps is None
        ):
            if phy_healthy is True:
                err_msg = report.wan_iperf.error
                return CycleResult(
                    status=Status.UNAVAILABLE,
                    reason=(
                        f"iperf test failed ({err_msg}), but local PLC link is verified healthy"
                    ),
                )
            return CycleResult(
                status=Status.UNAVAILABLE,
                reason=f"iperf test failed/unavailable: {report.wan_iperf.error}",
            )

        up = report.wan_iperf.upload_mbps
        down = report.wan_iperf.download_mbps

        low_reasons = []
        if up is None or not math.isfinite(up) or up < settings.min_upload_mbps:
            low_reasons.append(
                f"upload {up:.1f} < {settings.min_upload_mbps:.1f} Mbit/s"
                if up is not None
                else "upload failed"
            )
        if down is None or not math.isfinite(down) or down < settings.min_download_mbps:
            low_reasons.append(
                f"download {down:.1f} < {settings.min_download_mbps:.1f} Mbit/s"
                if down is not None
                else "download failed"
            )

        if low_reasons:
            low_str = "; ".join(low_reasons)
            if phy_healthy is True:
                return CycleResult(
                    status=Status.UNAVAILABLE,
                    reason=f"iperf degraded ({low_str}), but local PLC link is verified healthy",
                    upload_mbps=up,
                    download_mbps=down,
                    upload_port=report.wan_iperf.upload_port,
                    download_port=report.wan_iperf.download_port,
                )

            if phy_healthy is False:
                return CycleResult(
                    status=Status.DEGRADED,
                    reason=f"PLC throughput degraded ({low_str})",
                    upload_mbps=up,
                    download_mbps=down,
                    upload_port=report.wan_iperf.upload_port,
                    download_port=report.wan_iperf.download_port,
                )

            # No PLC PHY evidence was performed or available
            if settings.require_plc_evidence_for_reboot:
                return CycleResult(
                    status=Status.UNAVAILABLE,
                    reason=(
                        f"Throughput low ({low_str}), but no PLC-specific "
                        "evidence is configured/available"
                    ),
                    upload_mbps=up,
                    download_mbps=down,
                    upload_port=report.wan_iperf.upload_port,
                    download_port=report.wan_iperf.download_port,
                )

            return CycleResult(
                status=Status.DEGRADED,
                reason=low_str,
                upload_mbps=up,
                download_mbps=down,
                upload_port=report.wan_iperf.upload_port,
                download_port=report.wan_iperf.download_port,
            )

        return CycleResult(
            status=Status.HEALTHY,
            reason="Throughput is above configured thresholds",
            upload_mbps=up,
            download_mbps=down,
            upload_port=report.wan_iperf.upload_port,
            download_port=report.wan_iperf.download_port,
        )

    # Fallback if no iperf was run
    if phy_healthy is True:
        return CycleResult(status=Status.HEALTHY, reason="Local PLC link verified healthy")

    return CycleResult(status=Status.UNAVAILABLE, reason="No throughput tests were performed")


def transition(
    state: WatchdogState,
    result: CycleResult,
    settings: Settings,
    now: float,
) -> tuple[WatchdogState, ActionType, str]:
    """Pure state transition based on current state, cycle result, and settings."""
    state.last_status = result.status
    state.last_reason = result.reason
    state.last_check_timestamp = now
    state.prune_history(now)

    if result.status == Status.DEGRADED:
        state.consecutive_failures += 1
    else:
        # Reset consecutive failure streak on HEALTHY, UNAVAILABLE, or MISCONFIGURED
        state.consecutive_failures = 0

    if result.status == Status.HEALTHY:
        state.breaker_tripped = False

    if state.consecutive_failures >= settings.fail_limit:
        if settings.action == "reboot":
            window_seconds = settings.reboot_window_hours * 3600.0
            recent_reboots = state.recent_reboot_count(now, window_seconds)

            if recent_reboots >= settings.max_reboots_in_window or state.breaker_tripped:
                state.breaker_tripped = True
                max_w = settings.max_reboots_in_window
                w_h = settings.reboot_window_hours
                return (
                    state,
                    ActionType.NONE,
                    f"Circuit breaker active: {recent_reboots}/{max_w} reboots in last {w_h}h",
                )

            return (
                state,
                ActionType.REBOOT,
                f"Fail limit reached ({state.consecutive_failures}/{settings.fail_limit})",
            )
        else:
            fails = state.consecutive_failures
            limit = settings.fail_limit
            return (
                state,
                ActionType.LOG,
                f"Fail limit reached ({fails}/{limit}), action=log",
            )

    return state, ActionType.NONE, f"Status={result.status.value}"
