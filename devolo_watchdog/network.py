"""Network probing commands (ping, iperf3 execution & output parsing)."""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable, Sequence

from devolo_watchdog.config import Settings, candidate_ports
from devolo_watchdog.core import IperfSample, IperfThroughputTimeout, IperfUnavailable

LOG = logging.getLogger("devolo-throughput-watchdog")


def parse_iperf_mbps(payload: str) -> float:
    """Return receiver throughput from iperf3 JSON output."""
    data = json.loads(payload)
    if data.get("error"):
        raise RuntimeError(str(data["error"]))

    end = data.get("end", {})
    candidates = (
        end.get("sum_received", {}).get("bits_per_second"),
        end.get("sum", {}).get("bits_per_second"),
        end.get("sum_sent", {}).get("bits_per_second"),
    )
    bits_per_second = next((v for v in candidates if isinstance(v, (int, float))), None)
    if bits_per_second is None:
        raise ValueError("iperf3 JSON does not contain an end-to-end throughput value")
    return float(bits_per_second) / 1_000_000.0


def ping(host: str, settings: Settings) -> bool:
    """Execute ping command against target host."""
    command = [
        "ping",
        "-n",
        "-c",
        str(settings.ping_count),
        "-W",
        str(settings.ping_timeout_seconds),
        host,
    ]
    timeout = settings.ping_count * settings.ping_timeout_seconds + 3
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def run_single_iperf(settings: Settings, reverse: bool, port: int) -> float:
    """Execute iperf3 client command on a specific port."""
    command = [
        "iperf3",
        "--client",
        settings.iperf_server,
        "--port",
        str(port),
        "--bytes",
        settings.test_bytes,
        "--parallel",
        str(settings.parallel_streams),
        "--json",
    ]
    if reverse:
        command.append("--reverse")

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=settings.iperf_timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(detail)
    return parse_iperf_mbps(result.stdout)


def run_iperf(
    settings: Settings,
    reverse: bool,
    single_fn: Callable[[Settings, bool, int], float] = run_single_iperf,
    ports: Sequence[int] | None = None,
) -> IperfSample:
    """Try several candidate ports because each public port serves one test at a time."""
    selected_ports = tuple(ports) if ports is not None else candidate_ports(settings, reverse)
    errors: list[str] = []

    for port in selected_ports:
        try:
            return IperfSample(single_fn(settings, reverse, port), port)
        except subprocess.TimeoutExpired as exc:
            raise IperfThroughputTimeout(
                f"port {port}: {settings.test_bytes} transfer exceeded "
                f"{settings.iperf_timeout_seconds}s"
            ) from exc
        except (FileNotFoundError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
            errors.append(f"{port}: {exc}")
            LOG.info(
                "iperf attempt failed server=%s port=%d error=%s",
                settings.iperf_server,
                port,
                exc,
            )

    raise IperfUnavailable("all candidate ports failed: " + " | ".join(errors))
