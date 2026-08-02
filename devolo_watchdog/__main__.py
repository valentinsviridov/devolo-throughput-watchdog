"""Command-line interface entrypoint for devolo-throughput-watchdog."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from typing import Any

from devolo_watchdog.actions import read_password
from devolo_watchdog.config import Settings
from devolo_watchdog.probes import probe_gateway
from devolo_watchdog.runner import run_daemon
from devolo_watchdog.state import check_heartbeat

LOG = logging.getLogger("devolo-throughput-watchdog")


class WatchdogArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser ensuring top-level flags like --json persist across subparsers."""

    def parse_args(
        self,
        args: Sequence[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        args_list = list(sys.argv[1:] if args is None else args)
        parsed = super().parse_args(args, namespace)
        if "--json" in args_list:
            parsed.json = True
        return parsed


def build_parser() -> argparse.ArgumentParser:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--json", action="store_true", help="output results in JSON format")

    parser = WatchdogArgumentParser(
        prog="devolo-watchdog",
        description="Watchdog for devolo Magic 2 LAN adapters via iperf3 throughput probing.",
        parents=[common_parser],
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="available subcommands")

    # run / default
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
        "doctor", parents=[common_parser], help="diagnose environment, binaries, DNS, and API"
    )

    # discover
    subparsers.add_parser(
        "discover", parents=[common_parser], help="discover devolo PLC network topology"
    )

    # calibrate
    cal_parser = subparsers.add_parser(
        "calibrate", parents=[common_parser], help="recommend thresholds from baseline probes"
    )
    cal_parser.add_argument(
        "--samples", type=int, default=3, help="number of test cycles to run for calibration"
    )

    # healthcheck
    hb_parser = subparsers.add_parser(
        "healthcheck", parents=[common_parser], help="check heartbeat file freshness"
    )
    hb_parser.add_argument("--heartbeat-file", help="path to heartbeat file")

    # Top-level backwards compatibility flags
    parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--allow-action", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--check-config", action="store_true", help=argparse.SUPPRESS)

    return parser


def run_doctor(settings: Settings | None, json_output: bool) -> int:
    """Perform diagnostic checks on runtime environment and devolo device."""
    results: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: str) -> None:
        results.append({"check": name, "passed": passed, "detail": detail})

    # Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 11)
    record("python_version", py_ok, f"Python {py_ver} (requires >= 3.11)")

    # System binaries
    import shutil

    iperf_bin = shutil.which("iperf3")
    record("iperf3_binary", bool(iperf_bin), iperf_bin or "not found on PATH")

    ping_bin = shutil.which("ping")
    record("ping_binary", bool(ping_bin), ping_bin or "not found on PATH")

    # Config
    if settings is None:
        record("configuration", False, "Failed to parse settings from environment")
    else:
        record(
            "configuration",
            True,
            f"Gateway={settings.remote_probe}, Devolo={settings.devolo_ip}, "
            f"Action={settings.action}",
        )

        # Password file check
        if settings.password_file:
            try:
                read_password(settings.password_file)
                record("password_file", True, f"Readable: {settings.password_file}")
            except ValueError as exc:
                record("password_file", False, str(exc))

        # State directory check
        if settings.state_file:
            from pathlib import Path

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
        try:
            from devolo_watchdog.probes import probe_plc_phy

            plc = probe_plc_phy(settings.devolo_ip, settings.password_file)
            record("devolo_plc_api", plc.reachable, plc.error or "PLC API reachable")
        except Exception as exc:
            record("devolo_plc_api", False, str(exc))

    all_passed = all(r["passed"] for r in results)
    exit_code = 0 if all_passed else 1

    if json_output:
        print(json.dumps({"status": "ok" if all_passed else "error", "checks": results}, indent=2))
    else:
        print("=== devolo-watchdog doctor ===")
        for r in results:
            mark = "✓" if r["passed"] else "✗"
            print(f"[{mark}] {r['check']}: {r['detail']}")

    return exit_code


def run_discover(settings: Settings, json_output: bool) -> int:
    """Discover devolo devices, firmware, and PHY link topology."""
    import asyncio

    try:
        from devolo_plc_api import Device
    except ImportError:
        print("Error: devolo_plc_api library is not installed", file=sys.stderr)
        return 3

    async def _discover() -> dict[str, Any]:
        from devolo_watchdog.probes import patch_devolo_device_interfaces

        patch_devolo_device_interfaces()

        device = Device(ip=settings.devolo_ip)
        if password := read_password(settings.password_file):
            device.password = password
        async with device:
            info = {
                "ip": settings.devolo_ip,
                "serial_number": getattr(device, "serial_number", "unknown"),
                "mac": getattr(device, "mac", "unknown"),
            }

            plc_api = getattr(device, "plcnet", getattr(device, "plc", None))
            if plc_api:
                try:
                    overview = await plc_api.async_get_network_overview()
                    info["devices"] = [
                        {
                            "mac": getattr(d, "mac_address", getattr(d, "mac", "")),
                            "user_device_name": getattr(d, "user_device_name", ""),
                            "product_name": getattr(d, "product_name", ""),
                            "attached_to_router": getattr(d, "attached_to_router", False),
                        }
                        for d in getattr(overview, "devices", [])
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

    try:
        data = asyncio.run(_discover())
        if json_output:
            print(json.dumps(data, indent=2))
        else:
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
                    rx = rate["rx_rate"]
                    tx = rate["tx_rate"]
                    print(
                        f"  - {rate['mac_address_from']} -> {rate['mac_address_to']}: "
                        f"RX: {rx:.1f} Mbps, TX: {tx:.1f} Mbps"
                    )
        return 0
    except Exception as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 2


def run_calibrate(settings: Settings, samples_count: int, json_output: bool) -> int:
    """Perform no-action throughput probing and recommend upload/download thresholds."""
    print(f"Running {samples_count} calibration probes (no actions will be taken)...")
    from devolo_watchdog.config import candidate_ports
    from devolo_watchdog.probes import probe_wan_iperf

    up_samples: list[float] = []
    down_samples: list[float] = []

    for i in range(samples_count):
        now = time.time()
        ports_up = candidate_ports(settings, reverse=False, now=now)
        ports_down = candidate_ports(settings, reverse=True, now=now)
        res = probe_wan_iperf(settings, ports_up, ports_down)

        if res.upload_mbps is not None:
            up_samples.append(res.upload_mbps)
        if res.download_mbps is not None:
            down_samples.append(res.download_mbps)

        err_detail = f" (error: {res.error})" if res.error else ""
        print(
            f"Sample {i + 1}/{samples_count}: upload={res.upload_mbps} Mbps, "
            f"download={res.download_mbps} Mbps{err_detail}"
        )
        if i < samples_count - 1:
            time.sleep(2)

    if not up_samples:
        print("Calibration failed: insufficient valid samples", file=sys.stderr)
        return 1

    result_data: dict[str, Any] = {"samples": samples_count}
    rec_thresholds: dict[str, float] = {}

    if up_samples and down_samples:
        up_sorted = sorted(up_samples)
        down_sorted = sorted(down_samples)
        idx = max(0, int(len(up_sorted) * 0.10))
        rec_up = round(up_sorted[idx] * 0.70, 1)
        rec_down = round(down_sorted[idx] * 0.70, 1)

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
        if up_samples and down_samples:
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


def _load_env_file_if_present() -> None:
    from pathlib import Path

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
            except Exception:
                pass
            break


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _load_env_file_if_present()
    args = build_parser().parse_args()

    sub = args.subcommand
    once = getattr(args, "once", False)
    allow_action = getattr(args, "allow_action", False)
    check_config = getattr(args, "check_config", False)
    json_output = getattr(args, "json", False)

    if sub == "healthcheck":
        hb_path = (
            getattr(args, "heartbeat_file", None)
            or os.getenv("DW_HEARTBEAT_FILE")
            or "/tmp/watchdog_heartbeat"
        )
        healthy = check_heartbeat(hb_path, max_age_seconds=90.0)
        if healthy:
            print("OK - Heartbeat fresh")
            return 0
        else:
            print("CRITICAL - Heartbeat missing or stale", file=sys.stderr)
            return 1

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        if sub == "doctor":
            return run_doctor(None, json_output)
        LOG.error("configuration error: %s", exc)
        return 3

    if check_config:
        if settings.password_file:
            try:
                read_password(settings.password_file)
                LOG.info("password file valid: %s", settings.password_file)
            except ValueError as exc:
                LOG.error("password file check failed: %s", exc)
                return 3

        p_cnt = settings.ping_count
        p_to = settings.ping_timeout_seconds
        probe_res = probe_gateway(settings.remote_probe, p_cnt, p_to)
        devolo_res = probe_gateway(settings.devolo_ip, p_cnt, p_to)

        if not probe_res.reachable or not devolo_res.reachable:
            LOG.error(
                "configuration check failed: remote_probe reachable=%s (%s), "
                "devolo_ip reachable=%s (%s)",
                probe_res.reachable,
                probe_res.error,
                devolo_res.reachable,
                devolo_res.error,
            )
            return 2

        LOG.info(
            "configuration valid: iperf_server=%s ports=%d-%d test_bytes=%s "
            "remote_probe=%s (reachable=True) devolo_ip=%s (reachable=True) min_upload=%.1fMbps "
            "min_download=%.1fMbps action=%s",
            settings.iperf_server,
            min(settings.iperf_ports),
            max(settings.iperf_ports),
            settings.test_bytes,
            settings.remote_probe,
            settings.devolo_ip,
            settings.min_upload_mbps,
            settings.min_download_mbps,
            settings.action,
        )
        return 0

    if sub == "doctor":
        return run_doctor(settings, json_output)
    elif sub == "discover":
        return run_discover(settings, json_output)
    elif sub == "calibrate":
        samples = getattr(args, "samples", 3)
        return run_calibrate(settings, samples, json_output)

    return run_daemon(settings, once=once, allow_action=allow_action)


if __name__ == "__main__":
    sys.exit(main())
