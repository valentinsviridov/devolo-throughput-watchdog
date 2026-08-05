"""Domain models, typed result objects, and state representations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "measurement-unavailable"
    MISCONFIGURED = "misconfigured"


class ActionType(StrEnum):
    NONE = "none"
    LOG = "log"
    REBOOT = "reboot"


@dataclass(frozen=True)
class IperfSample:
    mbps: float
    port: int


@dataclass(frozen=True)
class GatewayProbeResult:
    reachable: bool
    latency_ms: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class WanIperfResult:
    upload_mbps: float | None = None
    download_mbps: float | None = None
    upload_port: int | None = None
    download_port: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class PlcPhyResult:
    rx_rate_mbps: float | None = None
    tx_rate_mbps: float | None = None
    reachable: bool = True
    error: str | None = None


@dataclass(frozen=True)
class MeasurementReport:
    gateway: GatewayProbeResult
    wan_iperf: WanIperfResult | None = None
    plc_phy: PlcPhyResult | None = None
    timestamp: float = 0.0


@dataclass(frozen=True)
class CycleResult:
    status: Status
    reason: str
    upload_mbps: float | None = None
    download_mbps: float | None = None
    upload_port: int | None = None
    download_port: int | None = None
    plc_rx_rate: float | None = None
    plc_tx_rate: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "upload_mbps": self.upload_mbps,
            "download_mbps": self.download_mbps,
            "upload_port": self.upload_port,
            "download_port": self.download_port,
            "plc_rx_rate": self.plc_rx_rate,
            "plc_tx_rate": self.plc_tx_rate,
        }


@dataclass
class RebootAttempt:
    timestamp: float
    accepted: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        dt = datetime.fromtimestamp(self.timestamp, tz=UTC)
        ts_str = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return {
            "timestamp": ts_str,
            "accepted": self.accepted,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RebootAttempt:
        ts_val = data["timestamp"]
        if isinstance(ts_val, str):
            try:
                if ts_val.endswith("Z"):
                    ts_val = ts_val[:-1] + "+00:00"
                ts = datetime.fromisoformat(ts_val).timestamp()
            except ValueError:
                ts = float(ts_val)
        else:
            ts = float(ts_val)
        return cls(
            timestamp=ts,
            accepted=bool(data["accepted"]),
            reason=str(data.get("reason", "")),
        )


@dataclass
class WatchdogState:
    consecutive_failures: int = 0
    degradation_notification_sent: bool = False
    reboot_history: list[RebootAttempt] = field(default_factory=list)
    last_reboot_timestamp: float | None = None
    breaker_tripped: bool = False
    last_status: Status | None = None
    last_reason: str | None = None
    last_check_timestamp: float | None = None

    def recent_reboot_count(self, now: float, window_seconds: float) -> int:
        cutoff = now - window_seconds
        return sum(1 for attempt in self.reboot_history if attempt.timestamp >= cutoff)

    def record_reboot(self, now: float, accepted: bool, reason: str) -> None:
        self.last_reboot_timestamp = now
        self.reboot_history.append(RebootAttempt(now, accepted, reason))

    def prune_history(self, now: float, max_age_seconds: float = 86400 * 7) -> None:
        cutoff = now - max_age_seconds
        self.reboot_history = [a for a in self.reboot_history if a.timestamp >= cutoff]

    def to_dict(self) -> dict[str, Any]:
        def format_ts(ts: float | None) -> str | None:
            if ts is None:
                return None
            dt = datetime.fromtimestamp(ts, tz=UTC)
            fmt = "%Y-%m-%dT%H:%M:%S.%f"
            return dt.strftime(fmt)[:-3] + "Z"

        return {
            "consecutive_failures": self.consecutive_failures,
            "degradation_notification_sent": self.degradation_notification_sent,
            "reboot_history": [a.to_dict() for a in self.reboot_history],
            "last_reboot_timestamp": format_ts(self.last_reboot_timestamp),
            "breaker_tripped": self.breaker_tripped,
            "last_status": self.last_status.value if self.last_status else None,
            "last_reason": self.last_reason,
            "last_check_timestamp": format_ts(self.last_check_timestamp),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchdogState:
        def parse_ts(val: Any) -> float | None:
            if val is None:
                return None
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                try:
                    if val.endswith("Z"):
                        val = val[:-1] + "+00:00"
                    return datetime.fromisoformat(val).timestamp()
                except ValueError:
                    pass
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        last_status_raw = data.get("last_status")
        last_status = Status(last_status_raw) if last_status_raw else None
        history_raw = data.get("reboot_history", [])
        history = [RebootAttempt.from_dict(h) for h in history_raw]
        return cls(
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            degradation_notification_sent=bool(data.get("degradation_notification_sent", False)),
            reboot_history=history,
            last_reboot_timestamp=parse_ts(data.get("last_reboot_timestamp")),
            breaker_tripped=bool(data.get("breaker_tripped", False)),
            last_status=last_status,
            last_reason=data.get("last_reason"),
            last_check_timestamp=parse_ts(data.get("last_check_timestamp")),
        )
