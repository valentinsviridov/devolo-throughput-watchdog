"""Daemon execution loop, logging, and signal handling."""

from __future__ import annotations

import logging
import signal
import threading

from devolo_watchdog.actions import restart_devolo
from devolo_watchdog.config import Settings
from devolo_watchdog.core import CycleResult, Status, evaluate_cycle
from devolo_watchdog.network import ping, run_iperf

LOG = logging.getLogger("devolo-throughput-watchdog")


def log_result(result: CycleResult, failures: int, fail_limit: int) -> None:
    """Log cycle status and throughput values."""
    values = (
        f" upload={result.upload_mbps:.1f}Mbps@{result.upload_port}"
        f" download={result.download_mbps:.1f}Mbps@{result.download_port}"
        if result.upload_mbps is not None and result.download_mbps is not None
        else ""
    )
    LOG.info(
        "status=%s failures=%d/%d%s reason=%s",
        result.status.value,
        failures,
        fail_limit,
        values,
        result.reason,
    )


def run_daemon(settings: Settings, once: bool = False) -> int:
    """Run the watchdog loop or execute a single check when once=True."""
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    if not once and settings.initial_delay_seconds:
        stop.wait(settings.initial_delay_seconds)

    failures = 0
    while not stop.is_set():
        try:
            result = evaluate_cycle(settings, ping, run_iperf)
        except Exception as exc:
            LOG.error("unexpected error during measurement cycle: %s", exc, exc_info=True)
            result = CycleResult(Status.UNAVAILABLE, f"unexpected measurement error: {exc}")

        failures = failures + 1 if result.status == Status.DEGRADED else 0
        log_result(result, failures, settings.fail_limit)

        if failures >= settings.fail_limit:
            if settings.action == "reboot":
                try:
                    res = "accepted" if restart_devolo(settings) else "rejected"
                    LOG.warning("action=reboot device=%s result=%s", settings.devolo_ip, res)
                except Exception:
                    LOG.exception("action=reboot device=%s result=error", settings.devolo_ip)
            else:
                LOG.warning(
                    "action=log-only device=%s reboot_would_trigger=true",
                    settings.devolo_ip,
                )

            failures = 0
            if not once:
                stop.wait(settings.cooldown_seconds)

        if once:
            return 0 if result.status == Status.HEALTHY else 1
        stop.wait(settings.interval_seconds)

    return 0
