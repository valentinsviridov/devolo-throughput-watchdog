"""Command-line interface entrypoint for devolo-throughput-watchdog."""

from __future__ import annotations

import argparse
import logging

from devolo_watchdog.config import Settings
from devolo_watchdog.runner import run_daemon

LOG = logging.getLogger("devolo-throughput-watchdog")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure WAN throughput via public iperf3 server and restart a devolo adapter."
    )
    parser.add_argument("--once", action="store_true", help="run one measurement cycle and exit")
    parser.add_argument(
        "--check-config", action="store_true", help="validate environment and exit"
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        LOG.error("configuration error: %s", exc)
        return 2

    if args.check_config:
        LOG.info(
            "configuration valid: iperf_server=%s ports=%d-%d test_bytes=%s "
            "remote_probe=%s devolo_ip=%s min_upload=%.1fMbps "
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
    return run_daemon(settings, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
