"""Pure domain models, exceptions, and measurement cycle evaluation logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from devolo_watchdog.config import Settings


class Status(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "measurement-unavailable"


class IperfUnavailable(RuntimeError):
    """All candidate public iperf3 ports rejected or failed the test."""


class IperfThroughputTimeout(RuntimeError):
    """A fixed-size iperf3 transfer did not complete before the deadline."""


@dataclass(frozen=True)
class IperfSample:
    mbps: float
    port: int


@dataclass(frozen=True)
class CycleResult:
    status: Status
    reason: str
    upload_mbps: float | None = None
    download_mbps: float | None = None
    upload_port: int | None = None
    download_port: int | None = None


def evaluate_cycle(
    settings: Settings,
    ping_fn: Callable[[str, Settings], bool],
    iperf_fn: Callable[[Settings, bool], IperfSample],
) -> CycleResult:
    """Evaluate a single measurement cycle using local-first short-circuit probing."""
    # Infrastructure Best Practice: Verify local gateway reachability first.
    # If local gateway is unreachable, short-circuit immediately without wasting WAN timeouts.
    gateway_ok = ping_fn(settings.remote_probe, settings)
    if not gateway_ok:
        return CycleResult(
            Status.DEGRADED,
            f"Local gateway ({settings.remote_probe}) is unreachable via ping",
        )

    try:
        upload = iperf_fn(settings, False)
        download = iperf_fn(settings, True)
    except IperfThroughputTimeout as exc:
        return CycleResult(Status.DEGRADED, f"fixed-size throughput test timed out: {exc}")
    except IperfUnavailable as exc:
        return CycleResult(
            Status.UNAVAILABLE,
            f"public iperf service unavailable while local gateway is reachable: {exc}",
        )

    low = []
    if upload.mbps < settings.min_upload_mbps:
        low.append(f"upload {upload.mbps:.1f} < {settings.min_upload_mbps:.1f} Mbit/s")
    if download.mbps < settings.min_download_mbps:
        low.append(f"download {download.mbps:.1f} < {settings.min_download_mbps:.1f} Mbit/s")

    if low:
        return CycleResult(
            Status.DEGRADED,
            "; ".join(low),
            upload.mbps,
            download.mbps,
            upload.port,
            download.port,
        )
    return CycleResult(
        Status.HEALTHY,
        "throughput is above configured thresholds",
        upload.mbps,
        download.mbps,
        upload.port,
        download.port,
    )
