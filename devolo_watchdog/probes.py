"""Typed network probe adapters for ping, iperf3, and devolo PLC API."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import subprocess
from typing import TYPE_CHECKING

from devolo_watchdog.models import (
    GatewayProbeResult,
    IperfSample,
    LocalIperfResult,
    PlcPhyResult,
    WanIperfResult,
)

if TYPE_CHECKING:
    from devolo_watchdog.config import Settings

LOG = logging.getLogger("devolo-throughput-watchdog")


class ProbeError(Exception):
    """Base exception for probe adapter errors."""


class PingError(ProbeError):
    """Ping command execution or environment error."""


class IperfError(ProbeError):
    """Iperf3 execution error."""


class PlcApiError(ProbeError):
    """Devolo PLC API query error."""


def parse_iperf_mbps(payload: str) -> float:
    """Return receiver throughput from iperf3 JSON output."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise IperfError(f"Invalid JSON output from iperf3: {exc}") from exc

    if data.get("error"):
        raise IperfError(str(data["error"]))

    end = data.get("end", {})
    candidates = (
        end.get("sum_received", {}).get("bits_per_second"),
        end.get("sum", {}).get("bits_per_second"),
        end.get("sum_sent", {}).get("bits_per_second"),
    )
    bits_per_second = next((v for v in candidates if isinstance(v, (int, float))), None)
    if bits_per_second is None:
        raise IperfError("iperf3 JSON does not contain an end-to-end throughput value")
    mbps = float(bits_per_second) / 1_000_000.0
    if not math.isfinite(mbps):
        raise IperfError("iperf3 JSON throughput value is not a finite number")
    return mbps


def probe_gateway(host: str, count: int = 2, timeout_seconds: int = 2) -> GatewayProbeResult:
    """Execute ping command against target gateway host."""
    command = ["ping", "-n", "-c", str(count), "-W", str(timeout_seconds), host]
    timeout = count * timeout_seconds + 3
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return GatewayProbeResult(reachable=True)
        return GatewayProbeResult(reachable=False, error=f"ping exit code {result.returncode}")
    except FileNotFoundError:
        return GatewayProbeResult(reachable=False, error="ping binary missing on system")
    except subprocess.TimeoutExpired:
        return GatewayProbeResult(reachable=False, error=f"ping command timed out after {timeout}s")
    except Exception as exc:
        return GatewayProbeResult(reachable=False, error=f"ping execution failed: {exc}")


def run_single_iperf(
    server: str,
    port: int,
    test_bytes: str,
    parallel: int,
    timeout_seconds: int,
    reverse: bool,
    connect_timeout_ms: int = 5000,
) -> float:
    """Execute iperf3 client command on a specific port."""
    command = [
        "iperf3",
        "--client",
        server,
        "--port",
        str(port),
        "--bytes",
        test_bytes,
        "--parallel",
        str(parallel),
        "--connect-timeout",
        str(connect_timeout_ms),
        "--json",
    ]
    if reverse:
        command.append("--reverse")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        raise IperfError("iperf3 binary missing on system") from None
    except subprocess.TimeoutExpired:
        raise IperfError(f"iperf3 transfer exceeded timeout ({timeout_seconds}s)") from None

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise IperfError(detail)

    return parse_iperf_mbps(result.stdout)


def probe_local_iperf(settings: Settings) -> LocalIperfResult:
    """Run local iperf3 tests for upload and download if server configured."""
    if not settings.local_iperf_server:
        return LocalIperfResult(error="No local iperf server configured")

    port = settings.local_iperf_port
    try:
        up = run_single_iperf(
            server=settings.local_iperf_server,
            port=port,
            test_bytes=settings.test_bytes,
            parallel=settings.parallel_streams,
            timeout_seconds=settings.iperf_timeout_seconds,
            reverse=False,
        )
        down = run_single_iperf(
            server=settings.local_iperf_server,
            port=port,
            test_bytes=settings.test_bytes,
            parallel=settings.parallel_streams,
            timeout_seconds=settings.iperf_timeout_seconds,
            reverse=True,
        )
        return LocalIperfResult(upload_mbps=up, download_mbps=down, port=port)
    except IperfError as exc:
        return LocalIperfResult(port=port, error=str(exc))
    except Exception as exc:
        return LocalIperfResult(port=port, error=f"Local iperf failed: {exc}")


def _run_direction(
    settings: Settings, ports: tuple[int, ...], reverse: bool
) -> tuple[IperfSample | None, str | None]:
    err: str | None = None
    for p in ports:
        try:
            rate = run_single_iperf(
                server=settings.iperf_server,
                port=p,
                test_bytes=settings.test_bytes,
                parallel=settings.parallel_streams,
                timeout_seconds=settings.iperf_timeout_seconds,
                reverse=reverse,
            )
            return IperfSample(rate, p), None
        except IperfError as exc:
            err = f"port {p}: {exc}"
    return None, err


def probe_wan_iperf(
    settings: Settings, ports_up: tuple[int, ...], ports_down: tuple[int, ...]
) -> WanIperfResult:
    """Run WAN throughput probes for upload and download across port lists."""
    up_sample, up_err = _run_direction(settings, ports_up, reverse=False)
    if not up_sample:
        return WanIperfResult(error=f"WAN upload test failed: {up_err}")

    down_sample, down_err = _run_direction(settings, ports_down, reverse=True)
    if not down_sample:
        return WanIperfResult(
            upload_mbps=up_sample.mbps,
            upload_port=up_sample.port,
            error=f"WAN download test failed: {down_err}",
        )

    return WanIperfResult(
        upload_mbps=up_sample.mbps,
        download_mbps=down_sample.mbps,
        upload_port=up_sample.port,
        download_port=down_sample.port,
    )


async def async_probe_plc_phy(devolo_ip: str, password: str | None = None) -> PlcPhyResult:
    """Query devolo device PLC network overview for PHY transmission rates."""
    try:
        from devolo_plc_api import Device
    except ImportError:
        return PlcPhyResult(reachable=False, error="devolo_plc_api library not installed")

    try:
        async with Device(ip=devolo_ip) as device:
            if password:
                device.password = password
            if device.plc is None:
                return PlcPhyResult(reachable=True, error="PLC API not supported by device")

            overview = await device.plc.async_get_network_overview()
            rx_rates: list[float] = []
            tx_rates: list[float] = []
            for item in overview.devices:
                if item.rx_rate:
                    rx_rates.append(float(item.rx_rate))
                if item.tx_rate:
                    tx_rates.append(float(item.tx_rate))

            min_rx = min(rx_rates) if rx_rates else None
            min_tx = min(tx_rates) if tx_rates else None
            return PlcPhyResult(rx_rate_mbps=min_rx, tx_rate_mbps=min_tx, reachable=True)
    except Exception as exc:
        return PlcPhyResult(reachable=False, error=str(exc))


def probe_plc_phy(devolo_ip: str, password: str | None = None) -> PlcPhyResult:
    """Synchronous wrapper for PLC PHY probing."""
    return asyncio.run(async_probe_plc_phy(devolo_ip, password))
