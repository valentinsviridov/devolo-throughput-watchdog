"""HTTP server for exposing watchdog state."""

from __future__ import annotations

import http.server
import json
import logging
import threading
from typing import Any

from devolo_watchdog.config import Settings
from devolo_watchdog.state import StateStore

LOG = logging.getLogger("devolo-throughput-watchdog")


def start_http_server(settings: Settings, store: StateStore) -> threading.Thread | None:
    """Start an HTTP server in a background thread to expose state, if configured."""
    if not settings.http_port:
        return None

    class StateHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in ("/", "/state"):
                self.send_response(404)
                self.end_headers()
                return

            try:
                state = store.load()
                data = state.to_dict()
                response = json.dumps(data, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
            except Exception as exc:
                LOG.error("HTTP server error: %s", exc)
                self.send_response(500)
                self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Suppress default HTTP logging

    class ThreadedHTTPServer(http.server.HTTPServer):
        daemon_threads = True

    try:
        server = ThreadedHTTPServer(("", settings.http_port), StateHandler)
    except Exception as exc:
        LOG.error("Failed to start HTTP server on port %d: %s", settings.http_port, exc)
        return None

    thread = threading.Thread(
        target=server.serve_forever,
        name="StateHTTPServer",
        daemon=True,
    )
    thread.start()
    LOG.info("HTTP server started on port %d", settings.http_port)
    return thread
