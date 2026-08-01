"""Legacy network probing compatibility module (delegates to probes.py)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from devolo_watchdog.config import Settings
from devolo_watchdog.models import IperfSample
from devolo_watchdog.probes import (
    IperfError,
    parse_iperf_mbps,
    probe_gateway,
    probe_wan_iperf,
    run_single_iperf,
)


def ping(host: str, settings: Settings) -> bool:
    """Legacy ping wrapper around probe_gateway."""
    res = probe_gateway(host, settings.ping_count, settings.ping_timeout_seconds)
    return res.reachable


def run_iperf(
    settings: Settings,
    reverse: bool,
    single_fn: Callable[..., float] | None = None,
    ports: Sequence[int] | None = None,
) -> IperfSample:
    """Legacy run_iperf wrapper around probe_wan_iperf."""
    if single_fn is not None:
        # Custom single_fn passed (e.g. in tests)
        from devolo_watchdog.config import candidate_ports
        from devolo_watchdog.core import IperfUnavailable

        selected_ports = tuple(ports) if ports is not None else candidate_ports(settings, reverse)
        errors: list[str] = []
        for port in selected_ports:
            try:
                rate = single_fn(settings, reverse, port)
                return IperfSample(rate, port)
            except Exception as exc:
                errors.append(f"{port}: {exc}")

        raise IperfUnavailable("all candidate ports failed: " + " | ".join(errors))

    # Standard execution
    from devolo_watchdog.config import candidate_ports
    from devolo_watchdog.core import IperfUnavailable

    ports_to_try = tuple(ports) if ports is not None else candidate_ports(settings, reverse)
    if reverse:
        res = probe_wan_iperf(settings, ports_up=(), ports_down=ports_to_try)
        if res.download_mbps is not None and res.download_port is not None:
            return IperfSample(res.download_mbps, res.download_port)
    else:
        res = probe_wan_iperf(settings, ports_up=ports_to_try, ports_down=())
        if res.upload_mbps is not None and res.upload_port is not None:
            return IperfSample(res.upload_mbps, res.upload_port)

    raise IperfUnavailable(f"public iperf test failed: {res.error}")


__all__ = ["ping", "run_iperf", "run_single_iperf", "parse_iperf_mbps", "IperfError"]
