"""Pure evaluation policy and state transition logic."""

from __future__ import annotations

import logging
import math

from devolo_watchdog.config import Settings
from devolo_watchdog.models import (
    ActionType,
    CycleResult,
    MeasurementReport,
    PlcPhyResult,
    Status,
    WanIperfResult,
    WatchdogState,
)

LOG = logging.getLogger("devolo-throughput-watchdog")


def _evaluate_gateway(report: MeasurementReport, settings: Settings) -> CycleResult | None:
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
    return None


def plc_phy_is_degraded(plc: PlcPhyResult | None, min_rate: float) -> bool:
    """Return True when PLC PHY rates are below the configured minimum."""
    if plc is None or not plc.reachable:
        return False
    rx = plc.rx_rate_mbps
    tx = plc.tx_rate_mbps
    if rx is None or tx is None:
        return False
    return rx < min_rate or tx < min_rate


def _evaluate_plc_phy(
    report: MeasurementReport, settings: Settings
) -> tuple[bool | None, CycleResult | None]:
    plc = report.plc_phy
    if plc is None or not plc.reachable:
        return None, None
    rx = plc.rx_rate_mbps
    tx = plc.tx_rate_mbps
    if rx is None or tx is None:
        return None, None
    if rx >= settings.min_plc_phy_rate_mbps and tx >= settings.min_plc_phy_rate_mbps:
        return True, None
    return False, CycleResult(
        status=Status.DEGRADED,
        reason=(
            f"PLC PHY link rate degraded (rx={rx:.1f}Mbps, tx={tx:.1f}Mbps < "
            f"{settings.min_plc_phy_rate_mbps:.1f}Mbps)"
        ),
        plc_rx_rate=rx,
        plc_tx_rate=tx,
    )


def _rate_failure(label: str, value: float | None, threshold: float) -> str | None:
    if value is None:
        return f"{label} failed"
    if not math.isfinite(value) or value < threshold:
        return f"{label} {value:.1f} < {threshold:.1f} Mbit/s"
    return None


def _cycle_with_wan(status: Status, reason: str, wan: WanIperfResult) -> CycleResult:
    return CycleResult(
        status=status,
        reason=reason,
        upload_mbps=wan.upload_mbps,
        download_mbps=wan.download_mbps,
        upload_port=wan.upload_port,
        download_port=wan.download_port,
    )


def _wan_result(wan: WanIperfResult, phy_healthy: bool | None, settings: Settings) -> CycleResult:
    if wan.error and (wan.upload_mbps is None or wan.download_mbps is None):
        reason = f"iperf test failed/unavailable: {wan.error}"
        if phy_healthy:
            reason = f"iperf test failed ({wan.error}), but local PLC link is verified healthy"
        return CycleResult(status=Status.UNAVAILABLE, reason=reason)

    low_reasons = tuple(
        reason
        for reason in (
            _rate_failure("upload", wan.upload_mbps, settings.min_upload_mbps),
            _rate_failure("download", wan.download_mbps, settings.min_download_mbps),
        )
        if reason is not None
    )
    if not low_reasons:
        return _cycle_with_wan(Status.HEALTHY, "Throughput is above configured thresholds", wan)

    low_description = "; ".join(low_reasons)
    if phy_healthy:
        return _cycle_with_wan(
            Status.UNAVAILABLE,
            f"iperf degraded ({low_description}), but local PLC link is verified healthy",
            wan,
        )
    if settings.require_plc_evidence_for_reboot:
        return _cycle_with_wan(
            Status.UNAVAILABLE,
            (
                f"Throughput low ({low_description}), but no PLC-specific "
                "evidence is configured/available"
            ),
            wan,
        )
    return _cycle_with_wan(Status.DEGRADED, low_description, wan)


def evaluate_report(report: MeasurementReport, settings: Settings) -> CycleResult:
    """Evaluate a MeasurementReport against policy thresholds."""
    if gateway_failure := _evaluate_gateway(report, settings):
        return gateway_failure

    phy_healthy, phy_failure = _evaluate_plc_phy(report, settings)
    if phy_failure:
        return phy_failure
    if report.wan_iperf is not None:
        return _wan_result(report.wan_iperf, phy_healthy, settings)
    if phy_healthy:
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
    window_seconds = settings.reboot_window_hours * 3600.0
    state.prune_history(now, max_age_seconds=max(86400 * 7, window_seconds))
    recent_reboots = state.recent_reboot_count(now, window_seconds)

    # A persisted breaker flag is informational, not a permanent latch. Re-arm
    # automatically once the configured moving window contains fewer attempts.
    if state.breaker_tripped and recent_reboots < settings.max_reboots_in_window:
        state.breaker_tripped = False

    if result.status == Status.DEGRADED:
        state.degraded_timestamps.append(now)
    elif result.status == Status.HEALTHY:
        state.degradation_notification_sent = False
        state.breaker_tripped = False
        state.degraded_timestamps.clear()
    else:
        # Reset notification state on UNAVAILABLE or MISCONFIGURED
        state.degradation_notification_sent = False

    cutoff = now - settings.fail_window_seconds
    state.degraded_timestamps = [ts for ts in state.degraded_timestamps if ts >= cutoff]
    degraded_count = len(state.degraded_timestamps)

    if degraded_count >= settings.fail_limit:
        if settings.action == "reboot":
            if recent_reboots >= settings.max_reboots_in_window:
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
                f"Fail limit reached ({degraded_count}/{settings.fail_limit})",
            )
        else:
            limit = settings.fail_limit
            return (
                state,
                ActionType.LOG,
                f"Fail limit reached ({degraded_count}/{limit}), action=log",
            )

    return state, ActionType.NONE, f"Status={result.status.value}"
