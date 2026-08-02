"""Command-line interface entrypoint for devolo-throughput-watchdog."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, cast

from devolo_plc_api import Device

from devolo_watchdog.actions import read_password
from devolo_watchdog.config import Settings, heartbeat_max_age_seconds_from_env
from devolo_watchdog.probes import probe_gateway
from devolo_watchdog.runner import RestartPersistenceError, request_restart, run_daemon
from devolo_watchdog.state import StateStore, check_heartbeat

LOG = logging.getLogger("devolo-throughput-watchdog")


class _PlcOverviewApi(Protocol):
    async def async_get_network_overview(self) -> Any: ...


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return parsed


def find_executable(name: str) -> str | None:
    """Find an executable on PATH for the Linux runtime diagnostics."""
    for directory in os.get_exec_path():
        candidate = Path(directory or os.curdir) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def build_parser() -> argparse.ArgumentParser:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="output results in JSON format",
    )

    parser = argparse.ArgumentParser(
        prog="devolo-watchdog",
        description="Watchdog for devolo Magic 2 LAN adapters via iperf3 throughput probing.",
        parents=[common_parser],
    )

    subparsers = parser.add_subparsers(
        dest="subcommand",
        required=True,
        help="available subcommands",
    )

    # run
    run_parser = subparsers.add_parser(
        "run", parents=[common_parser], help="run watchdog daemon or single check"
    )
    run_parser.add_argument(
        "--once", action="store_true", help="run one measurement cycle and exit"
    )
    run_parser.add_argument(
        "--allow-action",
        action="store_true",
        help="allow hardware actions (reboot) during --once mode",
    )
    run_parser.add_argument(
        "--check-config", action="store_true", help="validate environment and exit"
    )

    # doctor
    subparsers.add_parser(
        "doctor", parents=[common_parser], help="diagnose environment, binaries, network, and API"
    )

    # discover
    subparsers.add_parser(
        "discover", parents=[common_parser], help="discover devolo PLC network topology"
    )

    # restart
    subparsers.add_parser(
        "restart",
        parents=[common_parser],
        help="immediately request a restart of the configured devolo device",
    )

    # calibrate
    cal_parser = subparsers.add_parser(
        "calibrate", parents=[common_parser], help="recommend thresholds from baseline probes"
    )
    cal_parser.add_argument(
        "--samples",
        type=_positive_int,
        default=3,
        help="number of test cycles to run for calibration",
    )

    # healthcheck
    hb_parser = subparsers.add_parser(
        "healthcheck", parents=[common_parser], help="check heartbeat file freshness"
    )
    hb_parser.add_argument("--heartbeat-file", help="path to heartbeat file")
    hb_parser.add_argument(
        "--max-age-seconds",
        type=_positive_float,
        help="maximum heartbeat age (default: twice DW_INTERVAL_SECONDS, minimum 90)",
    )

    return parser


def _render_doctor_results(results: list[dict[str, Any]], json_output: bool) -> int:
    all_passed = all(result["passed"] for result in results)
    if json_output:
        print(json.dumps({"status": "ok" if all_passed else "error", "checks": results}, indent=2))
    else:
        print("=== devolo-watchdog doctor ===")
        for result in results:
            mark = "✓" if result["passed"] else "✗"
            print(f"[{mark}] {result['check']}: {result['detail']}")
    return 0 if all_passed else 1


def run_doctor(
    settings: Settings | None,
    json_output: bool,
    configuration_error: str | None = None,
    *,
    executable_finder: Callable[[str], str | None] | None = None,
) -> int:
    """Perform diagnostic checks on runtime environment and devolo device."""
    results: list[dict[str, Any]] = []

    def record(name: str, passed: bool, message: str) -> None:
        results.append({"check": name, "passed": passed, "detail": message})

    # Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 11)
    record("python_version", py_ok, f"Python {py_ver} (requires >= 3.11)")

    # System binaries
    finder = executable_finder or find_executable
    iperf_bin = finder("iperf3")
    record("iperf3_binary", bool(iperf_bin), iperf_bin or "not found on PATH")

    ping_bin = finder("ping")
    record("ping_binary", bool(ping_bin), ping_bin or "not found on PATH")

    # Config
    if settings is None:
        detail = configuration_error or "Failed to parse settings from environment"
        record("configuration", False, detail)
    else:
        record(
            "configuration",
            True,
            f"Gateway={settings.remote_probe}, Devolo={settings.devolo_ip}, "
            f"Action={settings.action}",
        )

        # Password file check
        device_password: str | None = None
        password_valid = True
        if settings.password_file:
            try:
                device_password = read_password(settings.password_file)
                record("password_file", True, f"Readable: {settings.password_file}")
            except ValueError as exc:
                password_valid = False
                record("password_file", False, str(exc))

        # State directory check
        if settings.state_file:
            state_dir = Path(settings.state_file).parent
            try:
                state_dir.mkdir(parents=True, exist_ok=True)
                test_file = state_dir / ".doctor_write_test"
                test_file.touch()
                test_file.unlink()
                record("state_directory", True, f"Writable: {state_dir}")
            except Exception as exc:
                record(
                    "state_directory",
                    False,
                    f"Directory '{state_dir}' is not writable: {exc}",
                )

        # Gateway ping check
        gw = probe_gateway(
            settings.remote_probe, settings.ping_count, settings.ping_timeout_seconds
        )
        record("gateway_ping", gw.reachable, gw.error or f"Reachable: {settings.remote_probe}")

        # Devolo adapter ping check
        dev = probe_gateway(settings.devolo_ip, settings.ping_count, settings.ping_timeout_seconds)
        record("devolo_ping", dev.reachable, dev.error or f"Reachable: {settings.devolo_ip}")

        # Devolo API discovery check
        if password_valid:
            try:
                from devolo_watchdog.probes import probe_plc_phy

                plc = probe_plc_phy(settings.devolo_ip, device_password)
                record("devolo_plc_api", plc.reachable, plc.error or "PLC API reachable")
            except Exception as exc:
                record("devolo_plc_api", False, str(exc))
        else:
            record("devolo_plc_api", False, "Skipped because password file is invalid")

    return _render_doctor_results(results, json_output)


def _print_command_error(message: str, json_output: bool, *, prefix: str = "Error") -> None:
    if json_output:
        print(json.dumps({"status": "error", "error": message}))
    else:
        print(f"{prefix}: {message}", file=sys.stderr)


async def _discover_device(settings: Settings, device_class: Any) -> dict[str, Any]:
    device = device_class(ip=settings.devolo_ip)
    if password := read_password(settings.password_file):
        device.password = password

    async with device:
        info = {
            "ip": settings.devolo_ip,
            "serial_number": getattr(device, "serial_number", "unknown"),
            "mac": getattr(device, "mac", "unknown"),
        }
        plc_api_value = getattr(device, "plcnet", None)
        if plc_api_value is None:
            return info
        plc_api = cast(_PlcOverviewApi, plc_api_value)
        try:
            overview = await plc_api.async_get_network_overview()
            info["devices"] = [
                {
                    "mac": getattr(node, "mac_address", getattr(node, "mac", "")),
                    "user_device_name": getattr(node, "user_device_name", ""),
                    "product_name": getattr(node, "product_name", ""),
                    "attached_to_router": getattr(node, "attached_to_router", False),
                }
                for node in getattr(overview, "devices", [])
            ]
            if hasattr(overview, "data_rates"):
                info["data_rates"] = [
                    {
                        "mac_address_from": getattr(rate, "mac_address_from", ""),
                        "mac_address_to": getattr(rate, "mac_address_to", ""),
                        "rx_rate": getattr(rate, "rx_rate", 0.0),
                        "tx_rate": getattr(rate, "tx_rate", 0.0),
                    }
                    for rate in overview.data_rates
                ]
        except Exception as exc:
            info["plc_overview_error"] = str(exc)
        return info


def _render_discovery(data: dict[str, Any], json_output: bool) -> None:
    if json_output:
        print(json.dumps(data, indent=2))
        return

    print("=== Devolo Device Discovery ===")
    print(f"IP: {data['ip']}")
    print(f"MAC: {data['mac']}")
    print(f"Serial: {data['serial_number']}")
    if "devices" in data:
        print("\nConnected PLC Nodes:")
        for node in data["devices"]:
            name = node.get("product_name") or node.get("user_device_name") or ""
            detail = f" ({name})" if name else ""
            print(f"  - MAC: {node['mac']}{detail}")
    if "data_rates" in data and data["data_rates"]:
        print("\nPLC Transmission Rates:")
        for rate in data["data_rates"]:
            print(
                f"  - {rate['mac_address_from']} -> {rate['mac_address_to']}: "
                f"RX: {rate['rx_rate']:.1f} Mbps, TX: {rate['tx_rate']:.1f} Mbps"
            )


def run_discover(
    settings: Settings,
    json_output: bool,
    *,
    device_class: Any | None = None,
) -> int:
    """Discover devolo devices, firmware, and PHY link topology."""
    if device_class is None:
        device_class = Device
        from devolo_watchdog.probes import patch_devolo_device_interfaces

        patch_devolo_device_interfaces(device_class)

    try:
        data = asyncio.run(_discover_device(settings, device_class))
    except Exception as exc:
        _print_command_error(str(exc), json_output, prefix="Discovery failed")
        return 2
    _render_discovery(data, json_output)
    return 0


def _render_restart_result(
    settings: Settings,
    json_output: bool,
    *,
    status: str,
    detail: str,
) -> None:
    if json_output:
        print(
            json.dumps(
                {
                    "status": status,
                    "action": "restart",
                    "device": settings.devolo_ip,
                    "detail": detail,
                }
            )
        )
        return

    stream = sys.stdout if status == "accepted" else sys.stderr
    print(detail, file=stream)


def run_restart(settings: Settings, json_output: bool) -> int:
    """Issue an operator-requested restart through the automated action path."""
    store = StateStore(settings.state_file)
    state = store.load()
    try:
        accepted = request_restart(
            settings,
            store,
            state,
            now=time.time(),
            reason="manual restart command",
        )
    except RestartPersistenceError as exc:
        detail = f"Restart skipped for devolo device {settings.devolo_ip}: {exc}"
        _render_restart_result(settings, json_output, status="error", detail=detail)
        return 2
    except ValueError as exc:
        detail = f"Restart unavailable for devolo device {settings.devolo_ip}: {exc}"
        _render_restart_result(settings, json_output, status="error", detail=detail)
        return 3
    except Exception as exc:
        detail = f"Restart failed for devolo device {settings.devolo_ip}: {exc}"
        _render_restart_result(settings, json_output, status="error", detail=detail)
        return 2

    if accepted:
        detail = f"Restart request accepted for devolo device {settings.devolo_ip}."
        _render_restart_result(settings, json_output, status="accepted", detail=detail)
        return 0

    detail = f"Restart request rejected by devolo device {settings.devolo_ip}."
    _render_restart_result(settings, json_output, status="rejected", detail=detail)
    return 1


def run_calibrate(settings: Settings, samples_count: int, json_output: bool) -> int:
    """Perform no-action throughput probing and recommend upload/download thresholds."""
    if samples_count <= 0:
        print("Calibration failed: --samples must be greater than zero", file=sys.stderr)
        return 3

    progress_stream = sys.stderr if json_output else sys.stdout
    print(
        f"Running {samples_count} calibration probes (no actions will be taken)...",
        file=progress_stream,
    )
    from devolo_watchdog.config import candidate_ports
    from devolo_watchdog.probes import probe_wan_iperf

    up_samples: list[float] = []
    down_samples: list[float] = []

    for i in range(samples_count):
        now = time.time()
        ports_up = candidate_ports(settings, reverse=False, now=now)
        ports_down = candidate_ports(settings, reverse=True, now=now)
        res = probe_wan_iperf(settings, ports_up, ports_down)

        if res.upload_mbps is not None and res.download_mbps is not None:
            up_samples.append(res.upload_mbps)
            down_samples.append(res.download_mbps)

        upload = f"{res.upload_mbps:.1f}" if res.upload_mbps is not None else "unavailable"
        download = f"{res.download_mbps:.1f}" if res.download_mbps is not None else "unavailable"
        err_detail = f" (error: {res.error})" if res.error else ""
        print(
            f"Sample {i + 1}/{samples_count}: upload={upload} Mbps, "
            f"download={download} Mbps{err_detail}",
            file=progress_stream,
        )
        if i < samples_count - 1:
            time.sleep(2)

    if not up_samples or not down_samples:
        error_data = {
            "status": "error",
            "error": "insufficient valid bidirectional samples",
            "requested_samples": samples_count,
            "successful_upload_samples": len(up_samples),
            "successful_download_samples": len(down_samples),
        }
        if json_output:
            print(json.dumps(error_data, indent=2))
        else:
            print("Calibration failed: insufficient valid bidirectional samples", file=sys.stderr)
        return 1

    result_data: dict[str, Any] = {
        "status": "ok",
        "requested_samples": samples_count,
        "successful_upload_samples": len(up_samples),
        "successful_download_samples": len(down_samples),
    }
    rec_thresholds: dict[str, float] = {}

    up_sorted = sorted(up_samples)
    down_sorted = sorted(down_samples)
    up_idx = max(0, int(len(up_sorted) * 0.10))
    down_idx = max(0, int(len(down_sorted) * 0.10))
    rec_up = round(up_sorted[up_idx] * 0.70, 1)
    rec_down = round(down_sorted[down_idx] * 0.70, 1)

    result_data["upload_mbps"] = {
        "min": min(up_samples),
        "max": max(up_samples),
        "avg": sum(up_samples) / len(up_samples),
    }
    result_data["download_mbps"] = {
        "min": min(down_samples),
        "max": max(down_samples),
        "avg": sum(down_samples) / len(down_samples),
    }
    rec_thresholds["DW_MIN_UPLOAD_MBPS"] = rec_up
    rec_thresholds["DW_MIN_DOWNLOAD_MBPS"] = rec_down

    result_data["recommended_thresholds"] = rec_thresholds

    if json_output:
        print(json.dumps(result_data, indent=2))
    else:
        print("\n=== Calibration Complete ===")
        up_avg = sum(up_samples) / len(up_samples)
        down_avg = sum(down_samples) / len(down_samples)
        print(
            f"Upload   - Min: {min(up_samples):.1f} Mbps, "
            f"Max: {max(up_samples):.1f} Mbps, Avg: {up_avg:.1f} Mbps"
        )
        print(
            f"Download - Min: {min(down_samples):.1f} Mbps, "
            f"Max: {max(down_samples):.1f} Mbps, Avg: {down_avg:.1f} Mbps"
        )
        print("\nRecommended Environment Variables:")
        for key, val in rec_thresholds.items():
            print(f"{key}={val}")

    return 0


def load_env_file_if_present() -> None:
    """Load the first supported local environment file without overriding process values."""
    for filename in ("devolo-throughput-watchdog.env", ".env"):
        path = Path(filename)
        if path.is_file():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
            except (OSError, UnicodeError) as exc:
                LOG.warning("Unable to read environment file %s: %s", path, exc)
            break


def _run_healthcheck(args: argparse.Namespace, json_output: bool) -> int:
    heartbeat_file = (
        getattr(args, "heartbeat_file", None)
        or os.getenv("DW_HEARTBEAT_FILE")
        or "/tmp/watchdog_heartbeat"
    )
    try:
        max_age = getattr(args, "max_age_seconds", None) or heartbeat_max_age_seconds_from_env()
    except ValueError as exc:
        _print_command_error(str(exc), json_output, prefix="CRITICAL - Invalid configuration")
        return 2

    healthy = check_heartbeat(heartbeat_file, max_age_seconds=max_age)
    if json_output:
        print(
            json.dumps(
                {
                    "status": "ok" if healthy else "critical",
                    "heartbeat_file": heartbeat_file,
                    "max_age_seconds": max_age,
                }
            )
        )
    elif healthy:
        print("OK - Heartbeat fresh")
    else:
        print("CRITICAL - Heartbeat missing or stale", file=sys.stderr)
    return 0 if healthy else 1


def _run_configuration_check(settings: Settings, json_output: bool) -> int:
    if settings.password_file:
        try:
            read_password(settings.password_file)
        except ValueError as exc:
            _print_command_error(str(exc), json_output, prefix="Password file check failed")
            return 3

    probe = probe_gateway(settings.remote_probe, settings.ping_count, settings.ping_timeout_seconds)
    device = probe_gateway(settings.devolo_ip, settings.ping_count, settings.ping_timeout_seconds)
    data = {
        "status": "ok" if probe.reachable and device.reachable else "unreachable",
        "iperf_server": settings.iperf_server,
        "iperf_ports": list(settings.iperf_ports),
        "test_bytes": settings.test_bytes,
        "remote_probe": {"host": settings.remote_probe, "reachable": probe.reachable},
        "devolo": {"host": settings.devolo_ip, "reachable": device.reachable},
        "min_upload_mbps": settings.min_upload_mbps,
        "min_download_mbps": settings.min_download_mbps,
        "action": settings.action,
    }
    if json_output:
        print(json.dumps(data))
    elif data["status"] == "ok":
        LOG.info(
            "configuration valid: iperf_server=%s ports=%s test_bytes=%s "
            "remote_probe=%s (reachable=True) devolo_ip=%s (reachable=True) "
            "min_upload=%.1fMbps min_download=%.1fMbps action=%s",
            settings.iperf_server,
            ",".join(str(port) for port in settings.iperf_ports),
            settings.test_bytes,
            settings.remote_probe,
            settings.devolo_ip,
            settings.min_upload_mbps,
            settings.min_download_mbps,
            settings.action,
        )
    else:
        LOG.error(
            "configuration check failed: remote_probe reachable=%s (%s), "
            "devolo_ip reachable=%s (%s)",
            probe.reachable,
            probe.error,
            device.reachable,
            device.error,
        )
    return 0 if data["status"] == "ok" else 2


def _run_configured_command(
    args: argparse.Namespace,
    settings: Settings,
    json_output: bool,
) -> int:
    sub = args.subcommand
    if json_output and sub == "run":
        settings = replace(settings, log_format="json")

    if sub == "run":
        if args.check_config:
            return _run_configuration_check(settings, json_output)
        return run_daemon(
            settings,
            once=args.once,
            allow_action=args.allow_action,
        )
    if sub == "doctor":
        return run_doctor(settings, json_output)
    if sub == "discover":
        return run_discover(settings, json_output)
    if sub == "restart":
        return run_restart(settings, json_output)
    if sub == "calibrate":
        return run_calibrate(settings, args.samples, json_output)

    raise ValueError(f"unsupported command: {sub}")


def main() -> int:
    args = build_parser().parse_args()
    sub = args.subcommand
    json_output = getattr(args, "json", False)
    log_format = (
        "%(message)s" if json_output and sub == "run" else "%(asctime)s %(levelname)s %(message)s"
    )
    logging.basicConfig(level=logging.INFO, format=log_format)
    load_env_file_if_present()

    if sub == "healthcheck":
        return _run_healthcheck(args, json_output)

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        if sub == "doctor":
            return run_doctor(None, json_output, str(exc))
        if json_output:
            print(json.dumps({"status": "misconfigured", "error": str(exc)}))
        else:
            LOG.error("configuration error: %s", exc)
        return 3

    return _run_configured_command(args, settings, json_output)


if __name__ == "__main__":
    sys.exit(main())
