"""Configuration settings, validation, and port parsing utilities."""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "n"})


def _parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean (true/false, yes/no, on/off, or 1/0)")


def _parse_int(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {value!r}") from None


def _parse_float(value: str, name: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {value!r}") from None


def heartbeat_max_age_seconds_from_env() -> float:
    """Return the explicit heartbeat limit or a safe limit derived from the cycle interval."""
    raw_interval = os.getenv("DW_INTERVAL_SECONDS", "600")
    interval = _parse_int(raw_interval, "DW_INTERVAL_SECONDS")
    if interval <= 0:
        raise ValueError("DW_INTERVAL_SECONDS must be greater than zero")
    raw_max_age = os.getenv("DW_HEARTBEAT_MAX_AGE_SECONDS")
    max_age = (
        _parse_float(raw_max_age, "DW_HEARTBEAT_MAX_AGE_SECONDS")
        if raw_max_age is not None
        else max(90.0, interval * 2.0)
    )
    if not math.isfinite(max_age) or max_age <= 0:
        raise ValueError("DW_HEARTBEAT_MAX_AGE_SECONDS must be a finite number greater than zero")
    return max_age


def _require_nonempty(name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{name} cannot be empty")


def _validate_ports(ports: tuple[int, ...]) -> None:
    if not ports:
        raise ValueError("DW_IPERF_PORTS must contain at least one port")
    if any(
        isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
        for port in ports
    ):
        raise ValueError("iperf3 ports must be integers between 1 and 65535")


def _validate_integer_values(values: dict[str, int], *, allow_zero: bool = False) -> None:
    for name, value in values.items():
        requirement = "cannot be negative" if allow_zero else "must be greater than zero"
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer and {requirement}")
        if (allow_zero and value < 0) or (not allow_zero and value <= 0):
            raise ValueError(f"{name} {requirement}")


def _validate_finite_positive_values(values: dict[str, float]) -> None:
    for name, value in values.items():
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a finite number greater than zero")


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
    post_reboot_delay_seconds: int = 45
    state_file: str | None = None
    heartbeat_file: str | None = None
    heartbeat_max_age_seconds: float | None = None
    reboot_window_hours: float = 6.0
    max_reboots_in_window: int = 3
    require_plc_evidence_for_reboot: bool = True
    min_plc_phy_rate_mbps: float = 50.0
    log_format: str = "text"

    def __post_init__(self) -> None:
        self.validate()

    @property
    def effective_heartbeat_max_age_seconds(self) -> float:
        """Maximum heartbeat age, derived from the interval unless explicitly configured."""
        if self.heartbeat_max_age_seconds is not None:
            return self.heartbeat_max_age_seconds
        return max(90.0, self.interval_seconds * 2.0)

    @classmethod
    def from_env(cls) -> Settings:
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"Required environment variable {name} is missing")
            return value

        max_reboots_raw = os.getenv("DW_MAX_REBOOTS_IN_WINDOW")
        max_reboots_name = "DW_MAX_REBOOTS_IN_WINDOW"
        if max_reboots_raw is None:
            max_reboots_raw = os.getenv("DW_MAX_REBOOT_ATTEMPTS", "3")
            max_reboots_name = "DW_MAX_REBOOT_ATTEMPTS"
        max_reboots_in_window = _parse_int(max_reboots_raw, max_reboots_name)
        heartbeat_max_age_raw = os.getenv("DW_HEARTBEAT_MAX_AGE_SECONDS")

        return cls(
            iperf_server=os.getenv("DW_IPERF_SERVER", "iperf.example.com").strip(),
            iperf_ports=parse_ports(os.getenv("DW_IPERF_PORTS", "5201-5205")),
            remote_probe=required("DW_REMOTE_PROBE"),
            devolo_ip=required("DW_DEVOLO_IP"),
            min_upload_mbps=_parse_float(required("DW_MIN_UPLOAD_MBPS"), "DW_MIN_UPLOAD_MBPS"),
            min_download_mbps=_parse_float(
                required("DW_MIN_DOWNLOAD_MBPS"), "DW_MIN_DOWNLOAD_MBPS"
            ),
            test_bytes=os.getenv("DW_TEST_BYTES", "64M").strip().upper(),
            iperf_tries=_parse_int(os.getenv("DW_IPERF_TRIES", "5"), "DW_IPERF_TRIES"),
            iperf_timeout_seconds=_parse_int(
                os.getenv("DW_IPERF_TIMEOUT_SECONDS", "30"), "DW_IPERF_TIMEOUT_SECONDS"
            ),
            iperf_connect_timeout_ms=_parse_int(
                os.getenv("DW_IPERF_CONNECT_TIMEOUT_MS", "3000"),
                "DW_IPERF_CONNECT_TIMEOUT_MS",
            ),
            interval_seconds=_parse_int(
                os.getenv("DW_INTERVAL_SECONDS", "600"), "DW_INTERVAL_SECONDS"
            ),
            parallel_streams=_parse_int(
                os.getenv("DW_PARALLEL_STREAMS", "1"), "DW_PARALLEL_STREAMS"
            ),
            fail_limit=_parse_int(os.getenv("DW_FAIL_LIMIT", "3"), "DW_FAIL_LIMIT"),
            cooldown_seconds=_parse_int(
                os.getenv("DW_COOLDOWN_SECONDS", "600"), "DW_COOLDOWN_SECONDS"
            ),
            initial_delay_seconds=_parse_int(
                os.getenv("DW_INITIAL_DELAY_SECONDS", "30"), "DW_INITIAL_DELAY_SECONDS"
            ),
            ping_count=_parse_int(os.getenv("DW_PING_COUNT", "2"), "DW_PING_COUNT"),
            ping_timeout_seconds=_parse_int(
                os.getenv("DW_PING_TIMEOUT_SECONDS", "2"), "DW_PING_TIMEOUT_SECONDS"
            ),
            password_file=os.getenv("DW_PASSWORD_FILE") or None,
            action=os.getenv("DW_ACTION", "log").strip().lower(),
            post_reboot_delay_seconds=_parse_int(
                os.getenv("DW_POST_REBOOT_DELAY_SECONDS", "45"),
                "DW_POST_REBOOT_DELAY_SECONDS",
            ),
            state_file=os.getenv("DW_STATE_FILE") or None,
            heartbeat_file=os.getenv("DW_HEARTBEAT_FILE") or None,
            heartbeat_max_age_seconds=(
                _parse_float(heartbeat_max_age_raw, "DW_HEARTBEAT_MAX_AGE_SECONDS")
                if heartbeat_max_age_raw is not None
                else None
            ),
            reboot_window_hours=_parse_float(
                os.getenv("DW_REBOOT_WINDOW_HOURS", "6.0"), "DW_REBOOT_WINDOW_HOURS"
            ),
            max_reboots_in_window=max_reboots_in_window,
            require_plc_evidence_for_reboot=_parse_bool(
                os.getenv("DW_REQUIRE_PLC_EVIDENCE_FOR_REBOOT", "true"),
                "DW_REQUIRE_PLC_EVIDENCE_FOR_REBOOT",
            ),
            min_plc_phy_rate_mbps=_parse_float(
                os.getenv("DW_MIN_PLC_PHY_RATE_MBPS", "50.0"),
                "DW_MIN_PLC_PHY_RATE_MBPS",
            ),
            log_format=os.getenv("DW_LOG_FORMAT", "text").strip().lower(),
        )

    def validate(self) -> None:
        _require_nonempty("DW_IPERF_SERVER", self.iperf_server)
        _require_nonempty("DW_REMOTE_PROBE", self.remote_probe)
        _require_nonempty("DW_DEVOLO_IP", self.devolo_ip)
        _validate_ports(self.iperf_ports)
        if self.action not in {"log", "reboot"}:
            raise ValueError("DW_ACTION must be 'log' or 'reboot'")
        if self.log_format not in {"text", "json"}:
            raise ValueError("DW_LOG_FORMAT must be 'text' or 'json'")
        if not re.fullmatch(r"[1-9][0-9]*[KMGT]?", self.test_bytes):
            raise ValueError("DW_TEST_BYTES must look like 64M, 512K or 1G")
        _validate_finite_positive_values(
            {
                "DW_MIN_UPLOAD_MBPS": self.min_upload_mbps,
                "DW_MIN_DOWNLOAD_MBPS": self.min_download_mbps,
                "DW_REBOOT_WINDOW_HOURS": self.reboot_window_hours,
                "DW_MIN_PLC_PHY_RATE_MBPS": self.min_plc_phy_rate_mbps,
            }
        )
        _validate_integer_values(
            {
                "DW_IPERF_TRIES": self.iperf_tries,
                "DW_IPERF_TIMEOUT_SECONDS": self.iperf_timeout_seconds,
                "DW_IPERF_CONNECT_TIMEOUT_MS": self.iperf_connect_timeout_ms,
                "DW_INTERVAL_SECONDS": self.interval_seconds,
                "DW_PARALLEL_STREAMS": self.parallel_streams,
                "DW_FAIL_LIMIT": self.fail_limit,
                "DW_COOLDOWN_SECONDS": self.cooldown_seconds,
                "DW_PING_COUNT": self.ping_count,
                "DW_PING_TIMEOUT_SECONDS": self.ping_timeout_seconds,
                "DW_MAX_REBOOTS_IN_WINDOW": self.max_reboots_in_window,
            }
        )
        _validate_integer_values(
            {
                "DW_INITIAL_DELAY_SECONDS": self.initial_delay_seconds,
                "DW_POST_REBOOT_DELAY_SECONDS": self.post_reboot_delay_seconds,
            },
            allow_zero=True,
        )
        if self.heartbeat_max_age_seconds is not None:
            _validate_finite_positive_values(
                {"DW_HEARTBEAT_MAX_AGE_SECONDS": self.heartbeat_max_age_seconds}
            )

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
