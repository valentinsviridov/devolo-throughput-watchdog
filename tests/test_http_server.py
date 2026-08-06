import json
from unittest.mock import MagicMock, patch

from devolo_watchdog.http_server import start_http_server
from devolo_watchdog.models import WatchdogState


@patch("devolo_watchdog.http_server.http.server.HTTPServer.__init__", return_value=None)
@patch("devolo_watchdog.http_server.threading.Thread")
def test_http_server_lifecycle_and_requests(mock_thread, mock_server_init):
    settings = MagicMock()
    settings.http_port = 8080
    store = MagicMock()

    thread = start_http_server(settings, store)
    assert thread is not None
    mock_thread.assert_called_once()

    # The server initialization should be intercepted
    assert mock_server_init.called
    # mock_server_init is called with (server_address, RequestHandlerClass)
    handler_class = mock_server_init.call_args[0][-1]

    # Instantiate handler without calling BaseHTTPRequestHandler.__init__ (which requires a socket)
    handler = handler_class.__new__(handler_class)
    handler.wfile = MagicMock()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    # --- Test valid path (/) ---
    handler.path = "/"
    state = WatchdogState()
    state.breaker_tripped = True
    store.load.return_value = state

    handler.do_GET()

    handler.send_response.assert_called_with(200)
    handler.wfile.write.assert_called_once()
    response_data = json.loads(handler.wfile.write.call_args[0][0])
    assert response_data["breaker_tripped"] is True
    handler.log_message("format", "args")  # Test log_message override does not crash

    # --- Test valid path (/state) ---
    handler.wfile.reset_mock()
    handler.send_response.reset_mock()
    handler.path = "/state"

    handler.do_GET()
    handler.send_response.assert_called_with(200)
    handler.wfile.write.assert_called_once()

    # --- Test invalid path ---
    handler.wfile.reset_mock()
    handler.send_response.reset_mock()
    handler.path = "/invalid"

    handler.do_GET()

    handler.send_response.assert_called_with(404)
    handler.wfile.write.assert_not_called()

    # --- Test internal error handling ---
    handler.wfile.reset_mock()
    handler.send_response.reset_mock()
    handler.path = "/"
    store.load.side_effect = Exception("failed to load state")

    handler.do_GET()

    handler.send_response.assert_called_with(500)


def test_start_http_server_disabled():
    settings = MagicMock()
    settings.http_port = None
    store = MagicMock()

    assert start_http_server(settings, store) is None


@patch(
    "devolo_watchdog.http_server.http.server.HTTPServer.__init__",
    side_effect=OSError("bind failed"),
)
def test_start_http_server_bind_failure(mock_server_init):
    settings = MagicMock()
    settings.http_port = 8080
    store = MagicMock()

    assert start_http_server(settings, store) is None
