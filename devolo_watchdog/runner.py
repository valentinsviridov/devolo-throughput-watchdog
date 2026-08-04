"""Daemon execution loop, probe orchestration, logging, and signal handling."""

from __future__ import annotations

import json
import logging
import signal
import threading
import time

from devolo_watchdog.actions import read_password, restart_devolo
from devolo_watchdog.config import Settings, candidate_ports
from devolo_watchdog.logging_config import format_log_timestamp
from devolo_watchdog.models import (
    ActionType,
    CycleResult,
    MeasurementReport,
    Status,
    WatchdogState,
)
from devolo_watchdog.notifications import (
    Notification,
    degradation_notification,
    pre_reboot_notification,
    recovery_notification,
    send_ntfy_notification,
)
from devolo_watchdog.policy import evaluate_report, transition
from devolo_watchdog.probes import (
    probe_gateway,
    probe_plc_phy,
    probe_wan_iperf,
)
from devolo_watchdog.state import StateStore, write_heartbeat

LOG = logging.getLogger("devolo-throughput-watchdog")


class RestartPersistenceError(RuntimeError):
    """A restart was skipped because its attempt could not be persisted safely."""


def _send_notification_best_effort(settings: Settings, notification: Notification) -> bool:
    """Attempt delivery without allowing a notification outage to block recovery."""
    if settings.ntfy_url is None:
        return False
    try:
        send_ntfy_notification(settings, notification)
    except Exception as exc:  # Notification failures must not block a hardware recovery action.
        LOG.warning(
            "notification event=%s result=error error=%s",
            notification.event,
            exc,
        )
        return False
    LOG.info("notification event=%s result=sent", notification.event)
    return True


def request_restart(
    settings: Settings,
    store: StateStore,
    state: WatchdogState,
    *,
    now: float,
    reason: str,
) -> bool:
    """Record and issue one restart request through the shared hardware adapter."""
    state.record_reboot(now, accepted=False, reason=reason)
    if not store.save(state):
        raise RestartPersistenceError("restart attempt could not be persisted")

    _send_notification_best_effort(
        settings,
        pre_reboot_notification(settings.devolo_ip, reason),
    )
    accepted = restart_devolo(settings)
    if accepted:
        state.reboot_history[-1].accepted = True
        if not store.save(state):
            LOG.error(
                "action=reboot device=%s result=accepted state_update=failed",
                settings.devolo_ip,
            )
    return accepted


def log_startup(settings: Settings, *, once: bool) -> None:
    """Log an immediate startup event in the configured output format."""
    mode = "once" if once else "daemon"
    initial_delay = 0 if once else settings.initial_delay_seconds
    if settings.log_format == "json":
        LOG.info(
            json.dumps(
                {
                    "timestamp": format_log_timestamp(),
                    "event": "watchdog_started",
                    "mode": mode,
                    "action": settings.action,
                    "initial_delay_seconds": initial_delay,
                }
            )
        )
        return

    LOG.info(
        "watchdog started mode=%s action=%s initial_delay=%ds",
        mode,
        settings.action,
        initial_delay,
    )


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
            "timestamp": format_log_timestamp(),
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
                "plc_rx_rate": result.plc_rx_rate,
                "plc_tx_rate": result.plc_tx_rate,
            },
        }
        LOG.info(json.dumps(data))
        return

    upload = result.upload_mbps
    download = result.download_mbps
    upload_port = f"@{result.upload_port}" if result.upload_port is not None else ""
    download_port = f"@{result.download_port}" if result.download_port is not None else ""
    values = (
        f" upload={upload:.1f}Mbps{upload_port} download={download:.1f}Mbps{download_port}"
        if upload is not None and download is not None
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
    if not gw_result.reachable:
        return MeasurementReport(gateway=gw_result, timestamp=now)

    # 2. PLC PHY probe (optional query)
    plc_result = None
    if settings.devolo_ip:
        try:
            password = read_password(settings.password_file)
            plc_result = probe_plc_phy(settings.devolo_ip, password)
        except Exception as exc:
            LOG.debug("PLC PHY probe skipped/failed: %s", exc)

    # 3. WAN iperf probe (rotated candidate ports)
    ports_up = candidate_ports(settings, reverse=False, now=now)
    ports_down = candidate_ports(settings, reverse=True, now=now)
    wan_result = probe_wan_iperf(settings, ports_up, ports_down)

    return MeasurementReport(
        gateway=gw_result,
        wan_iperf=wan_result,
        plc_phy=plc_result,
        timestamp=now,
    )


def _execute_reboot(
    settings: Settings,
    store: StateStore,
    state: WatchdogState,
    stop: threading.Event,
    now: float,
    result: CycleResult,
    action_reason: str,
) -> None:
    """Persist, execute, and verify one reboot attempt."""
    # noinspection PyBroadException
    try:
        success = request_restart(
            settings,
            store,
            state,
            now=now,
            reason=result.reason,
        )
        if not success:
            LOG.error("action=reboot device=%s result=rejected", settings.devolo_ip)
            return

        LOG.warning(
            "action=reboot device=%s result=accepted reason=%s",
            settings.devolo_ip,
            action_reason,
        )

        if not stop.is_set() and settings.post_reboot_delay_seconds:
            stop.wait(settings.post_reboot_delay_seconds)
        if stop.is_set():
            return

        verify_now = time.time()
        verify_report = collect_measurement_report(settings, verify_now)
        verify_result = evaluate_report(verify_report, settings)
        state.last_status = verify_result.status
        state.last_reason = verify_result.reason
        state.last_check_timestamp = verify_now

        if verify_result.status == Status.HEALTHY:
            LOG.info(
                "action=reboot device=%s post_reboot_verification=success",
                settings.devolo_ip,
            )
            state.consecutive_failures = 0
            state.breaker_tripped = False
        else:
            LOG.warning(
                "action=reboot device=%s post_reboot_verification=failed status=%s reason=%s",
                settings.devolo_ip,
                verify_result.status.value,
                verify_result.reason,
            )
        store.save(state)
    except RestartPersistenceError:
        LOG.critical(
            "action=reboot device=%s result=skipped reason=state-persistence-failed",
            settings.devolo_ip,
        )
    except Exception:  # Keep unexpected device/API failures from terminating the daemon.
        LOG.exception("action=reboot device=%s result=error", settings.devolo_ip)


def _install_signal_handlers(stop: threading.Event) -> None:
    def handle_signal(*_: object) -> None:
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handle_signal)
        except (ValueError, OSError):
            pass


def _handle_action(
    action: ActionType,
    action_reason: str,
    result: CycleResult,
    settings: Settings,
    store: StateStore,
    state: WatchdogState,
    stop: threading.Event,
    now: float,
    *,
    once: bool,
    allow_action: bool,
) -> bool:
    if action != ActionType.REBOOT:
        return False
    if once and not allow_action:
        LOG.warning(
            "action=reboot required but skipped: "
            "--once is dry-run by default without --allow-action"
        )
        return True
    _execute_reboot(settings, store, state, stop, now, result, action_reason)
    return True


def _once_exit_code(result: CycleResult) -> int:
    if result.status == Status.HEALTHY:
        return 0
    if result.status == Status.DEGRADED:
        return 1
    return 2


def run_daemon(
    settings: Settings,
    once: bool = False,
    allow_action: bool = False,
) -> int:
    """Run the watchdog loop or execute a single check when once=True."""
    stop = threading.Event()
    _install_signal_handlers(stop)
    log_startup(settings, once=once)

    store = StateStore(settings.state_file)
    state = store.load()

    if not once and settings.initial_delay_seconds:
        if settings.heartbeat_file:
            write_heartbeat(settings.heartbeat_file)
        stop.wait(settings.initial_delay_seconds)

    exit_code = 0

    while not stop.is_set():
        cycle_start = time.monotonic()
        now = time.time()

        if settings.heartbeat_file:
            write_heartbeat(settings.heartbeat_file, now)

        try:
            report = collect_measurement_report(settings, now)
            result = evaluate_report(report, settings)
        except Exception as exc:
            LOG.error("unexpected error during measurement cycle: %s", exc, exc_info=True)
            result = CycleResult(Status.UNAVAILABLE, f"unexpected measurement error: {exc}")

        was_degraded_notified = state.degradation_notification_sent
        state, action, action_reason = transition(state, result, settings, now)
        store.save(state)

        log_result(
            result,
            state.consecutive_failures,
            settings.fail_limit,
            action,
            settings.log_format,
        )

        if result.status == Status.DEGRADED and not state.degradation_notification_sent:
            state.degradation_notification_sent = _send_notification_best_effort(
                settings,
                degradation_notification(
                    result.reason,
                    state.consecutive_failures,
                    settings.fail_limit,
                ),
            )
            if state.degradation_notification_sent:
                store.save(state)

        if result.status != Status.DEGRADED and was_degraded_notified:
            _send_notification_best_effort(
                settings,
                recovery_notification(result.reason),
            )

        reboot_triggered = _handle_action(
            action,
            action_reason,
            result,
            settings,
            store,
            state,
            stop,
            now,
            once=once,
            allow_action=allow_action,
        )

        if once:
            return _once_exit_code(result)

        elapsed = time.monotonic() - cycle_start
        wait_target = settings.cooldown_seconds if reboot_triggered else settings.interval_seconds
        remaining_sleep = max(0.0, float(wait_target) - elapsed)
        stop.wait(remaining_sleep)

    return exit_code
