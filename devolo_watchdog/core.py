"""Domain models and evaluation compatibility layer."""

from __future__ import annotations

from collections.abc import Callable

from devolo_watchdog.config import Settings
from devolo_watchdog.models import (
    CycleResult,
    GatewayProbeResult,
    IperfSample,
    MeasurementReport,
    WanIperfResult,
)
from devolo_watchdog.policy import evaluate_report
from devolo_watchdog.probes import ProbeError


class IperfUnavailable(RuntimeError):
    """All candidate public iperf3 ports rejected or failed the test."""


class IperfThroughputTimeout(RuntimeError):
    """A fixed-size iperf3 transfer did not complete before the deadline."""


def evaluate_cycle(
    settings: Settings,
    ping_fn: Callable[[str, Settings], bool],
    iperf_fn: Callable[[Settings, bool], IperfSample],
) -> CycleResult:
    """Legacy/compatibility entrypoint for single cycle evaluation."""
    # 1. Ping gateway
    gw_ok = ping_fn(settings.remote_probe, settings)
    gw_res = GatewayProbeResult(reachable=gw_ok)

    # 2. WAN/iperf probe
    wan_res: WanIperfResult | None = None
    if gw_ok:
        try:
            up_sample = iperf_fn(settings, False)
            down_sample = iperf_fn(settings, True)
            wan_res = WanIperfResult(
                upload_mbps=up_sample.mbps,
                download_mbps=down_sample.mbps,
                upload_port=up_sample.port,
                download_port=down_sample.port,
            )
        except (RuntimeError, ProbeError, IperfUnavailable, IperfThroughputTimeout) as exc:
            wan_res = WanIperfResult(error=str(exc))

    report = MeasurementReport(
        gateway=gw_res,
        wan_iperf=wan_res,
    )
    return evaluate_report(report, settings)
