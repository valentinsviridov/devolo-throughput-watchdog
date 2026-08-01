"""Domain models and evaluation compatibility layer."""

from __future__ import annotations

from collections.abc import Callable

from devolo_watchdog.config import Settings
from devolo_watchdog.models import (
    CycleResult,
    GatewayProbeResult,
    IperfSample,
    LocalIperfResult,
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
    local_iperf_fn: Callable[[Settings, bool], IperfSample] | None = None,
) -> CycleResult:
    """Legacy/compatibility entrypoint for single cycle evaluation."""
    # 1. Ping gateway
    gw_ok = ping_fn(settings.remote_probe, settings)
    gw_res = GatewayProbeResult(reachable=gw_ok)

    # 2. Local iperf
    local_res: LocalIperfResult | None = None
    if settings.local_iperf_server or local_iperf_fn is not None:
        if local_iperf_fn is not None:
            try:
                up = local_iperf_fn(settings, False)
                down = local_iperf_fn(settings, True)
                local_res = LocalIperfResult(
                    upload_mbps=up.mbps,
                    download_mbps=down.mbps,
                    port=up.port,
                )
            except Exception as exc:
                local_res = LocalIperfResult(error=str(exc))
        else:
            from devolo_watchdog.probes import probe_local_iperf

            local_res = probe_local_iperf(settings)

    # 3. WAN iperf
    wan_res: WanIperfResult | None = None
    if gw_ok and (local_res is None or local_res.error is None):
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
        local_iperf=local_res,
        wan_iperf=wan_res,
    )
    return evaluate_report(report, settings)
