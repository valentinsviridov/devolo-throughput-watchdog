"""Typed network probe adapters for ping, iperf3, and devolo PLC API."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import subprocess
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from devolo_plc_api import Device

from devolo_watchdog.models import (
    GatewayProbeResult,
    IperfSample,
    PlcPhyResult,
    WanIperfResult,
)

if TYPE_CHECKING:
    from devolo_watchdog.config import Settings

LOG = logging.getLogger("devolo-throughput-watchdog")
DeviceFactory = Callable[..., Any]


class IperfError(RuntimeError):
    """Iperf3 execution error."""


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
    bits_per_second = next(
        (v for v in candidates if isinstance(v, (int, float)) and not isinstance(v, bool)), None
    )
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
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return GatewayProbeResult(reachable=True)

        if result.returncode == 2:
            # Fallback for ping implementations that do not support -W
            # (or reject format/permissions)
            fallback_command = ["ping", "-n", "-c", str(count), host]
            fallback_result = subprocess.run(
                fallback_command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if fallback_result.returncode == 0:
                return GatewayProbeResult(reachable=True)

            err_msg = (fallback_result.stderr or "").strip() or (result.stderr or "").strip()
            detail = f": {err_msg}" if err_msg else ""
            return GatewayProbeResult(
                reachable=False, error=f"ping exit code {fallback_result.returncode}{detail}"
            )

        err_msg = (result.stderr or "").strip()
        detail = f": {err_msg}" if err_msg else ""
        return GatewayProbeResult(
            reachable=False, error=f"ping exit code {result.returncode}{detail}"
        )
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
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        stdout_err = ""
        if stdout.strip():
            try:
                data = json.loads(stdout)
                if isinstance(data, dict) and data.get("error"):
                    stdout_err = str(data["error"])
                elif not isinstance(data, dict):
                    stdout_err = stdout.strip()
            except json.JSONDecodeError:
                stdout_err = stdout.strip()
        detail = stderr.strip() or stdout_err or f"exit {result.returncode}"
        raise IperfError(detail)

    return parse_iperf_mbps(result.stdout or "")


def probe_iperf_direction(
    settings: Settings, ports: tuple[int, ...], reverse: bool
) -> tuple[IperfSample | None, str | None]:
    """Try candidate ports for one iperf direction and aggregate failures."""
    errors: list[str] = []
    for p in ports:
        try:
            rate = run_single_iperf(
                server=settings.iperf_server,
                port=p,
                test_bytes=settings.test_bytes,
                parallel=settings.parallel_streams,
                timeout_seconds=settings.iperf_timeout_seconds,
                reverse=reverse,
                connect_timeout_ms=settings.iperf_connect_timeout_ms,
            )
            return IperfSample(rate, p), None
        except IperfError as exc:
            errors.append(f"port {p}: {exc}")
    return None, "; ".join(errors) if errors else "no candidate ports"


def probe_wan_iperf(
    settings: Settings, ports_up: tuple[int, ...], ports_down: tuple[int, ...]
) -> WanIperfResult:
    """Run WAN throughput probes for upload and download across port lists."""
    up_sample, up_err = probe_iperf_direction(settings, ports_up, reverse=False)
    if not up_sample:
        return WanIperfResult(error=f"WAN upload test failed: {up_err or 'unknown error'}")

    down_sample, down_err = probe_iperf_direction(settings, ports_down, reverse=True)
    if not down_sample:
        return WanIperfResult(
            upload_mbps=up_sample.mbps,
            upload_port=up_sample.port,
            error=f"WAN download test failed: {down_err or 'unknown error'}",
        )

    return WanIperfResult(
        upload_mbps=up_sample.mbps,
        download_mbps=down_sample.mbps,
        upload_port=up_sample.port,
        download_port=down_sample.port,
    )


_DEVICE_INTERFACES_PATCHED = False


def patch_devolo_device_interfaces(device_class: Any) -> None:
    """Fallback devolo_plc_api to non-loopback IPv4 interfaces if subnet matching is empty."""
    global _DEVICE_INTERFACES_PATCHED
    if _DEVICE_INTERFACES_PATCHED:
        return
    try:
        from ifaddr import get_adapters

        interface_selector_name = "_get_relevant_interfaces"
        orig_get_relevant = getattr(device_class, interface_selector_name, None)
        if not callable(orig_get_relevant):
            LOG.debug("devolo Device has no interface-selection hook to patch")
            return
        get_relevant_interfaces = cast(
            Callable[[object], Awaitable[list[str]]],
            orig_get_relevant,
        )

        async def _patched_get_relevant_interfaces(self: object) -> list[str]:
            interfaces = await get_relevant_interfaces(self)
            if not interfaces:
                fallback: list[str] = []
                for adapter in get_adapters():
                    for ip in adapter.ips:
                        if ip.is_IPv4 and str(ip.ip) not in {"127.0.0.1", "0.0.0.0"}:
                            fallback.append(str(ip.ip))
                return fallback
            return interfaces

        setattr(device_class, interface_selector_name, _patched_get_relevant_interfaces)
        _DEVICE_INTERFACES_PATCHED = True
    except Exception as exc:
        LOG.debug("Unable to install devolo interface fallback: %s", exc)


def _normalize_mac(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value.lower() if character.isalnum())


def _coerce_positive_rate(value: object) -> float | None:
    """Convert supported PLC rate values while rejecting invalid or non-finite telemetry."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, bytes)):
        return None
    try:
        rate = float(value)
    except (OverflowError, ValueError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def _rates_for_device(data_rates: list[object], device_mac: object) -> list[object]:
    """Prefer PHY rates connected to the configured device when endpoints are available."""
    normalized_device_mac = _normalize_mac(device_mac)
    if not normalized_device_mac:
        return data_rates

    matching_rates = []
    for rate in data_rates:
        endpoints = (
            getattr(rate, "mac_address_from", ""),
            getattr(rate, "mac_address_to", ""),
        )
        if any(_normalize_mac(endpoint) == normalized_device_mac for endpoint in endpoints):
            matching_rates.append(rate)
    return matching_rates or data_rates


async def async_probe_plc_phy(
    devolo_ip: str,
    password: str | None = None,
    *,
    device_class: DeviceFactory | None = None,
) -> PlcPhyResult:
    """Query devolo device PLC network overview for PHY transmission rates."""
    if device_class is None:
        device_factory: DeviceFactory = Device
        patch_devolo_device_interfaces(device_factory)
    else:
        device_factory = device_class

    try:
        device = device_factory(ip=devolo_ip)
        if password:
            device.password = password
        async with device:
            plc_api = device.plcnet
            if plc_api is None:
                return PlcPhyResult(reachable=True, error="PLC API not supported by device")

            overview = await plc_api.async_get_network_overview()
            rx_rates: list[float] = []
            tx_rates: list[float] = []

            data_rates = list(getattr(overview, "data_rates", []) or [])
            data_items = (
                _rates_for_device(data_rates, getattr(device, "mac", ""))
                if data_rates
                else list(getattr(overview, "devices", []))
            )
            for item in data_items:
                if (rx := _coerce_positive_rate(getattr(item, "rx_rate", None))) is not None:
                    rx_rates.append(rx)
                if (tx := _coerce_positive_rate(getattr(item, "tx_rate", None))) is not None:
                    tx_rates.append(tx)

            min_rx = min(rx_rates) if rx_rates else None
            min_tx = min(tx_rates) if tx_rates else None
            return PlcPhyResult(rx_rate_mbps=min_rx, tx_rate_mbps=min_tx, reachable=True)
    except Exception as exc:
        return PlcPhyResult(reachable=False, error=str(exc))


def probe_plc_phy(
    devolo_ip: str,
    password: str | None = None,
    *,
    device_class: DeviceFactory | None = None,
) -> PlcPhyResult:
    """Synchronous wrapper for PLC PHY probing."""
    return asyncio.run(async_probe_plc_phy(devolo_ip, password, device_class=device_class))
