"""Configuration settings, validation, and port parsing utilities."""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass


def _parse_bool(val: str) -> bool:
    return val.strip().lower() in {"1", "true", "yes", "on", "y"}


@dataclass(frozen=True)
class Settings:
    iperf_server: str
    iperf_ports: tuple[int, ...]
    remote_probe: str
    devolo_ip: str
    min_upload_mbps: float
    min_download_mbps: float
    local_min_upload_mbps: float | None = None
    local_min_download_mbps: float | None = None
    test_bytes: str = "64M"
    iperf_tries: int = 5
    iperf_timeout_seconds: int = 30
    iperf_connect_timeout_ms: int = 3000
    interval_seconds: int = 600
    parallel_streams: int = 1
    fail_limit: int = 3
    cooldown_seconds: int = 600
    initial_delay_seconds: int = 30
    ping_count: int = 2
    ping_timeout_seconds: int = 2
    password_file: str | None = None
    action: str = "log"
    local_iperf_server: str | None = None
    local_iperf_port: int = 5201
    max_reboot_attempts: int = 3
    post_reboot_delay_seconds: int = 45
    state_file: str | None = None
    heartbeat_file: str | None = None
    reboot_window_hours: float = 6.0
    max_reboots_in_window: int = 3
    require_plc_evidence_for_reboot: bool = True
    min_plc_phy_rate_mbps: float = 50.0
    log_format: str = "text"

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def from_env(cls) -> Settings:
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"Required environment variable {name} is missing")
            return value

        local_up_raw = os.getenv("DW_LOCAL_MIN_UPLOAD_MBPS")
        local_down_raw = os.getenv("DW_LOCAL_MIN_DOWNLOAD_MBPS")

        return cls(
            iperf_server=os.getenv("DW_IPERF_SERVER", "iperf.example.com").strip(),
            iperf_ports=parse_ports(os.getenv("DW_IPERF_PORTS", "5201-5205")),
            remote_probe=required("DW_REMOTE_PROBE"),
            devolo_ip=required("DW_DEVOLO_IP"),
            min_upload_mbps=float(required("DW_MIN_UPLOAD_MBPS")),
            min_download_mbps=float(required("DW_MIN_DOWNLOAD_MBPS")),
            local_min_upload_mbps=float(local_up_raw) if local_up_raw else None,
            local_min_download_mbps=float(local_down_raw) if local_down_raw else None,
            test_bytes=os.getenv("DW_TEST_BYTES", "64M").strip().upper(),
            iperf_tries=int(os.getenv("DW_IPERF_TRIES", "5")),
            iperf_timeout_seconds=int(os.getenv("DW_IPERF_TIMEOUT_SECONDS", "30")),
            iperf_connect_timeout_ms=int(os.getenv("DW_IPERF_CONNECT_TIMEOUT_MS", "3000")),
            interval_seconds=int(os.getenv("DW_INTERVAL_SECONDS", "600")),
            parallel_streams=int(os.getenv("DW_PARALLEL_STREAMS", "1")),
            fail_limit=int(os.getenv("DW_FAIL_LIMIT", "3")),
            cooldown_seconds=int(os.getenv("DW_COOLDOWN_SECONDS", "600")),
            initial_delay_seconds=int(os.getenv("DW_INITIAL_DELAY_SECONDS", "30")),
            ping_count=int(os.getenv("DW_PING_COUNT", "2")),
            ping_timeout_seconds=int(os.getenv("DW_PING_TIMEOUT_SECONDS", "2")),
            password_file=os.getenv("DW_PASSWORD_FILE") or None,
            action=os.getenv("DW_ACTION", "log").strip().lower(),
            local_iperf_server=os.getenv("DW_LOCAL_IPERF_SERVER") or None,
            local_iperf_port=int(os.getenv("DW_LOCAL_IPERF_PORT", "5201")),
            max_reboot_attempts=int(os.getenv("DW_MAX_REBOOT_ATTEMPTS", "3")),
            post_reboot_delay_seconds=int(os.getenv("DW_POST_REBOOT_DELAY_SECONDS", "45")),
            state_file=os.getenv("DW_STATE_FILE") or None,
            heartbeat_file=os.getenv("DW_HEARTBEAT_FILE") or None,
            reboot_window_hours=float(os.getenv("DW_REBOOT_WINDOW_HOURS", "6.0")),
            max_reboots_in_window=int(os.getenv("DW_MAX_REBOOTS_IN_WINDOW", "3")),
            require_plc_evidence_for_reboot=_parse_bool(
                os.getenv("DW_REQUIRE_PLC_EVIDENCE_FOR_REBOOT", "true")
            ),
            min_plc_phy_rate_mbps=float(os.getenv("DW_MIN_PLC_PHY_RATE_MBPS", "50.0")),
            log_format=os.getenv("DW_LOG_FORMAT", "text").strip().lower(),
        )

    def validate(self) -> None:
        if not self.iperf_server:
            raise ValueError("DW_IPERF_SERVER cannot be empty")
        if self.action not in {"log", "reboot"}:
            raise ValueError("DW_ACTION must be 'log' or 'reboot'")
        if self.log_format not in {"text", "json"}:
            raise ValueError("DW_LOG_FORMAT must be 'text' or 'json'")
        if not re.fullmatch(r"[1-9][0-9]*[KMGT]?", self.test_bytes):
            raise ValueError("DW_TEST_BYTES must look like 64M, 512K or 1G")
        if not math.isfinite(self.min_upload_mbps) or self.min_upload_mbps <= 0:
            raise ValueError("DW_MIN_UPLOAD_MBPS must be a finite number greater than zero")
        if not math.isfinite(self.min_download_mbps) or self.min_download_mbps <= 0:
            raise ValueError("DW_MIN_DOWNLOAD_MBPS must be a finite number greater than zero")

        if self.local_min_upload_mbps is not None and (
            not math.isfinite(self.local_min_upload_mbps) or self.local_min_upload_mbps <= 0
        ):
            raise ValueError("DW_LOCAL_MIN_UPLOAD_MBPS must be greater than zero")
        if self.local_min_download_mbps is not None and (
            not math.isfinite(self.local_min_download_mbps) or self.local_min_download_mbps <= 0
        ):
            raise ValueError("DW_LOCAL_MIN_DOWNLOAD_MBPS must be greater than zero")

        positive_ints = {
            "DW_IPERF_TRIES": self.iperf_tries,
            "DW_IPERF_TIMEOUT_SECONDS": self.iperf_timeout_seconds,
            "DW_IPERF_CONNECT_TIMEOUT_MS": self.iperf_connect_timeout_ms,
            "DW_INTERVAL_SECONDS": self.interval_seconds,
            "DW_PARALLEL_STREAMS": self.parallel_streams,
            "DW_FAIL_LIMIT": self.fail_limit,
            "DW_COOLDOWN_SECONDS": self.cooldown_seconds,
            "DW_PING_COUNT": self.ping_count,
            "DW_PING_TIMEOUT_SECONDS": self.ping_timeout_seconds,
            "DW_LOCAL_IPERF_PORT": self.local_iperf_port,
            "DW_MAX_REBOOT_ATTEMPTS": self.max_reboot_attempts,
            "DW_MAX_REBOOTS_IN_WINDOW": self.max_reboots_in_window,
        }
        for name, value in positive_ints.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")

        non_negative_ints = {
            "DW_INITIAL_DELAY_SECONDS": self.initial_delay_seconds,
            "DW_POST_REBOOT_DELAY_SECONDS": self.post_reboot_delay_seconds,
        }
        for name, value in non_negative_ints.items():
            if value < 0:
                raise ValueError(f"{name} cannot be negative")

        if self.reboot_window_hours <= 0:
            raise ValueError("DW_REBOOT_WINDOW_HOURS must be greater than zero")
        if self.min_plc_phy_rate_mbps <= 0:
            raise ValueError("DW_MIN_PLC_PHY_RATE_MBPS must be greater than zero")

        if self.iperf_tries > len(self.iperf_ports):
            raise ValueError("DW_IPERF_TRIES cannot exceed the number of configured ports")


def parse_ports(spec: str) -> tuple[int, ...]:
    """Parse comma-separated ports and inclusive ranges, preserving order."""
    ports: list[int] = []
    for raw_item in spec.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if "-" in item:
            parts = item.split("-", 1)
            if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
                raise ValueError(f"Invalid port range: {item}")
            start, end = (int(part.strip()) for part in parts)
            if end < start:
                raise ValueError(f"Descending port range is not allowed: {item}")
            ports.extend(range(start, end + 1))
        elif item.isdigit():
            ports.append(int(item))
        else:
            raise ValueError(f"Invalid port: {item}")

    unique = tuple(dict.fromkeys(ports))
    if not unique:
        raise ValueError("DW_IPERF_PORTS must contain at least one port")
    if any(port < 1 or port > 65535 for port in unique):
        raise ValueError("iperf3 ports must be between 1 and 65535")
    return unique


def candidate_ports(settings: Settings, reverse: bool, now: float | None = None) -> tuple[int, ...]:
    """Rotate public ports between cycles and use different starts per direction."""
    timestamp = time.time() if now is None else now
    slot = int(timestamp // settings.interval_seconds)
    port_count = len(settings.iperf_ports)
    direction_offset = (port_count // 2 if port_count > 1 else 0) if reverse else 0
    start = (slot + direction_offset) % port_count
    return tuple(
        settings.iperf_ports[(start + offset) % port_count]
        for offset in range(settings.iperf_tries)
    )
