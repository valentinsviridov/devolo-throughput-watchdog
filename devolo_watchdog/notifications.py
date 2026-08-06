"""Best-effort mobile notification delivery through ntfy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from devolo_watchdog.config import Settings


@dataclass(frozen=True)
class Notification:
    """A user-facing watchdog event."""

    event: str
    title: str
    message: str
    priority: str
    tags: str


def _read_token(path: str) -> str:
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"DW_NTFY_TOKEN_FILE unreadable ({path}): {exc}") from None
    if not token:
        raise ValueError(f"DW_NTFY_TOKEN_FILE is empty ({path})")
    return token


def send_ntfy_notification(settings: Settings, notification: Notification) -> None:
    """Publish one notification to the configured ntfy topic."""
    if settings.ntfy_url is None:
        return

    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Title": notification.title,
        "Priority": notification.priority,
        "Tags": notification.tags,
        "User-Agent": "devolo-throughput-watchdog",
    }
    if settings.ntfy_token_file:
        headers["Authorization"] = f"Bearer {_read_token(settings.ntfy_token_file)}"

    request = Request(
        settings.ntfy_url,
        data=notification.message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=settings.ntfy_timeout_seconds) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}")


def degradation_notification(
    result_reason: str,
    window_degraded_count: int,
    fail_limit: int,
) -> Notification:
    return Notification(
        event="degradation_detected",
        title="Network degradation detected",
        message=(
            f"{result_reason}\n"
            f"Degraded checks in observation window: {window_degraded_count}/{fail_limit}."
        ),
        priority="high",
        tags="warning,signal_strength",
    )


def recovery_notification(
    result_reason: str,
) -> Notification:
    return Notification(
        event="degradation_resolved",
        title="Network degradation resolved",
        message=result_reason,
        priority="default",
        tags="white_check_mark,signal_strength",
    )


def pre_reboot_notification(
    devolo_ip: str,
    reason: str,
) -> Notification:
    return Notification(
        event="pre_reboot",
        title="Devolo adapter reboot starting",
        message=(
            f"The watchdog is about to restart the adapter at {devolo_ip}.\n"
            f"Network traffic may be interrupted. Reason: {reason}"
        ),
        priority="max",
        tags="warning,arrows_counterclockwise",
    )
