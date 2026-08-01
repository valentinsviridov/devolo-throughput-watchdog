"""Configuration settings and port parsing utilities."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    iperf_server: str
    iperf_ports: tuple[int, ...]
    remote_probe: str
    devolo_ip: str
    min_upload_mbps: float
    min_download_mbps: float
    test_bytes: str = "64M"
    iperf_tries: int = 5
    iperf_timeout_seconds: int = 30
    interval_seconds: int = 600
    parallel_streams: int = 1
    fail_limit: int = 3
    cooldown_seconds: int = 600
    initial_delay_seconds: int = 30
    ping_count: int = 2
    ping_timeout_seconds: int = 2
    password_file: str | None = None
    action: str = "log"

    @classmethod
    def from_env(cls) -> Settings:
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"Required environment variable {name} is missing")
            return value

        settings = cls(
            iperf_server=os.getenv("DW_IPERF_SERVER", "iperf.example.com").strip(),
            iperf_ports=parse_ports(os.getenv("DW_IPERF_PORTS", "5201-5205")),
            remote_probe=required("DW_REMOTE_PROBE"),
            devolo_ip=required("DW_DEVOLO_IP"),
            min_upload_mbps=float(required("DW_MIN_UPLOAD_MBPS")),
            min_download_mbps=float(required("DW_MIN_DOWNLOAD_MBPS")),
            test_bytes=os.getenv("DW_TEST_BYTES", "64M").strip().upper(),
            iperf_tries=int(os.getenv("DW_IPERF_TRIES", "5")),
            iperf_timeout_seconds=int(os.getenv("DW_IPERF_TIMEOUT_SECONDS", "30")),
            interval_seconds=int(os.getenv("DW_INTERVAL_SECONDS", "600")),
            parallel_streams=int(os.getenv("DW_PARALLEL_STREAMS", "1")),
            fail_limit=int(os.getenv("DW_FAIL_LIMIT", "3")),
            cooldown_seconds=int(os.getenv("DW_COOLDOWN_SECONDS", "600")),
            initial_delay_seconds=int(os.getenv("DW_INITIAL_DELAY_SECONDS", "30")),
            ping_count=int(os.getenv("DW_PING_COUNT", "2")),
            ping_timeout_seconds=int(os.getenv("DW_PING_TIMEOUT_SECONDS", "2")),
            password_file=os.getenv("DW_PASSWORD_FILE") or None,
            action=os.getenv("DW_ACTION", "log").strip().lower(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.iperf_server:
            raise ValueError("DW_IPERF_SERVER cannot be empty")
        if self.action not in {"log", "reboot"}:
            raise ValueError("DW_ACTION must be 'log' or 'reboot'")
        if not re.fullmatch(r"[1-9][0-9]*[KMGT]?", self.test_bytes):
            raise ValueError("DW_TEST_BYTES must look like 64M, 512K or 1G")
        positive_ints = {
            "DW_IPERF_TRIES": self.iperf_tries,
            "DW_IPERF_TIMEOUT_SECONDS": self.iperf_timeout_seconds,
            "DW_INTERVAL_SECONDS": self.interval_seconds,
            "DW_PARALLEL_STREAMS": self.parallel_streams,
            "DW_FAIL_LIMIT": self.fail_limit,
            "DW_COOLDOWN_SECONDS": self.cooldown_seconds,
            "DW_PING_COUNT": self.ping_count,
            "DW_PING_TIMEOUT_SECONDS": self.ping_timeout_seconds,
        }
        for name, value in positive_ints.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.iperf_tries > len(self.iperf_ports):
            raise ValueError("DW_IPERF_TRIES cannot exceed the number of configured ports")
        if self.initial_delay_seconds < 0:
            raise ValueError("DW_INITIAL_DELAY_SECONDS cannot be negative")
        if self.min_upload_mbps <= 0 or self.min_download_mbps <= 0:
            raise ValueError("Throughput thresholds must be greater than zero")


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


def candidate_ports(
    settings: Settings, reverse: bool, now: float | None = None
) -> tuple[int, ...]:
    """Rotate public ports between cycles and use different starts per direction."""
    timestamp = time.time() if now is None else now
    slot = int(timestamp // settings.interval_seconds)
    direction_offset = settings.iperf_tries if reverse else 0
    start = (slot + direction_offset) % len(settings.iperf_ports)
    return tuple(
        settings.iperf_ports[(start + offset) % len(settings.iperf_ports)]
        for offset in range(settings.iperf_tries)
    )
