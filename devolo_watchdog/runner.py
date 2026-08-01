"""Daemon execution loop, probe orchestration, logging, and signal handling."""

from __future__ import annotations

import json
import logging
import signal
import threading
import time

from devolo_watchdog.actions import restart_devolo
from devolo_watchdog.config import Settings, candidate_ports
from devolo_watchdog.models import (
    ActionType,
    CycleResult,
    MeasurementReport,
    Status,
)
from devolo_watchdog.policy import evaluate_report, transition
from devolo_watchdog.probes import (
    probe_gateway,
    probe_local_iperf,
    probe_plc_phy,
    probe_wan_iperf,
)
from devolo_watchdog.state import StateStore, write_heartbeat

LOG = logging.getLogger("devolo-throughput-watchdog")


def log_result(
    result: CycleResult,
    failures: int,
    fail_limit: int,
    action: ActionType,
    log_format: str = "text",
) -> None:
    """Log cycle status and throughput values in text or structured JSON format."""
    if log_format == "json":
        data = {
            "timestamp": time.time(),
            "status": result.status.value,
            "reason": result.reason,
            "consecutive_failures": failures,
            "fail_limit": fail_limit,
            "action": action.value,
            "metrics": {
                "upload_mbps": result.upload_mbps,
                "download_mbps": result.download_mbps,
                "upload_port": result.upload_port,
                "download_port": result.download_port,
                "local_upload_mbps": result.local_upload_mbps,
                "local_download_mbps": result.local_download_mbps,
                "plc_rx_rate": result.plc_rx_rate,
                "plc_tx_rate": result.plc_tx_rate,
            },
        }
        LOG.info(json.dumps(data))
        return

    values = (
        f" upload={result.upload_mbps:.1f}Mbps@{result.upload_port}"
        f" download={result.download_mbps:.1f}Mbps@{result.download_port}"
        if result.upload_mbps is not None and result.download_mbps is not None
        else ""
    )
    LOG.info(
        "status=%s failures=%d/%d%s action=%s reason=%s",
        result.status.value,
        failures,
        fail_limit,
        values,
        action.value,
        result.reason,
    )


def collect_measurement_report(settings: Settings, now: float) -> MeasurementReport:
    """Orchestrate probe adapters to gather a comprehensive MeasurementReport."""
    # 1. Gateway probe
    gw_result = probe_gateway(
        settings.remote_probe, settings.ping_count, settings.ping_timeout_seconds
    )

    # 2. Local iperf probe
    local_result = probe_local_iperf(settings) if settings.local_iperf_server else None

    # 3. PLC PHY probe (optional query)
    plc_result = None
    if settings.devolo_ip:
        try:
            plc_result = probe_plc_phy(settings.devolo_ip, settings.password_file)
        except Exception as exc:
            LOG.debug("PLC PHY probe skipped/failed: %s", exc)

    # 4. WAN iperf probe (rotated candidate ports)
    ports_up = candidate_ports(settings, reverse=False, now=now)
    ports_down = candidate_ports(settings, reverse=True, now=now)
    wan_result = probe_wan_iperf(settings, ports_up, ports_down)

    return MeasurementReport(
        gateway=gw_result,
        local_iperf=local_result,
        wan_iperf=wan_result,
        plc_phy=plc_result,
        timestamp=now,
    )


def run_daemon(
    settings: Settings,
    once: bool = False,
    allow_action: bool = False,
) -> int:
    """Run the watchdog loop or execute a single check when once=True."""
    stop = threading.Event()

    def handle_signal(*_: object) -> None:
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handle_signal)
        except (ValueError, OSError):
            pass

    store = StateStore(settings.state_file)
    state = store.load()

    if not once and settings.initial_delay_seconds:
        stop.wait(settings.initial_delay_seconds)

    exit_code = 0

    while not stop.is_set():
        cycle_start = time.monotonic()
        now = time.time()

        try:
            report = collect_measurement_report(settings, now)
            result = evaluate_report(report, settings)
        except Exception as exc:
            LOG.error("unexpected error during measurement cycle: %s", exc, exc_info=True)
            result = CycleResult(Status.UNAVAILABLE, f"unexpected measurement error: {exc}")

        state, action, action_reason = transition(state, result, settings, now)
        store.save(state)

        if settings.heartbeat_file:
            write_heartbeat(settings.heartbeat_file, now)

        log_result(
            result,
            state.consecutive_failures,
            settings.fail_limit,
            action,
            settings.log_format,
        )

        reboot_triggered = False

        if action == ActionType.REBOOT:
            reboot_triggered = True
            if once and not allow_action:
                LOG.warning(
                    "action=reboot required but skipped: "
                    "--once is dry-run by default without --allow-action"
                )
            else:
                # Count every reboot attempt BEFORE calling device
                state.record_reboot(now, accepted=False, reason=result.reason)
                store.save(state)

                try:
                    success = restart_devolo(settings)
                    if success:
                        state.reboot_history[-1].accepted = True
                        store.save(state)
                        LOG.warning(
                            "action=reboot device=%s result=accepted reason=%s",
                            settings.devolo_ip,
                            action_reason,
                        )

                        # Post-reboot delay and verification
                        if not stop.is_set() and settings.post_reboot_delay_seconds:
                            stop.wait(settings.post_reboot_delay_seconds)

                        if not stop.is_set():
                            verify_now = time.time()
                            verify_report = collect_measurement_report(settings, verify_now)
                            verify_result = evaluate_report(verify_report, settings)
                            if verify_result.status == Status.HEALTHY:
                                LOG.info(
                                    "action=reboot device=%s post_reboot_verification=success",
                                    settings.devolo_ip,
                                )
                                state.consecutive_failures = 0
                                store.save(state)
                            else:
                                v_stat = verify_result.status.value
                                v_reas = verify_result.reason
                                LOG.warning(
                                    "action=reboot device=%s "
                                    "post_reboot_verification=failed status=%s reason=%s",
                                    settings.devolo_ip,
                                    v_stat,
                                    v_reas,
                                )
                    else:
                        LOG.error("action=reboot device=%s result=rejected", settings.devolo_ip)
                except Exception:
                    LOG.exception("action=reboot device=%s result=error", settings.devolo_ip)

        if once:
            if result.status == Status.HEALTHY:
                return 0
            elif result.status == Status.DEGRADED:
                return 1
            else:
                return 2

        elapsed = time.monotonic() - cycle_start
        wait_target = settings.cooldown_seconds if reboot_triggered else settings.interval_seconds
        remaining_sleep = max(0.0, float(wait_target) - elapsed)
        stop.wait(remaining_sleep)

    return exit_code
