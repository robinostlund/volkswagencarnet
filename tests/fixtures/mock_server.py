"""Mock HTTP server."""

import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


class MockServerRequestHandler(BaseHTTPRequestHandler):
    """Mock HTTP handler."""

    mock_responses = {"/ok": {"content": json.dumps([]), "code": 200}}

    def do_GET(self):
        """Respond with something."""

        if self.path in self.mock_responses:
            self.send_response(self.mock_responses.get(self.path).get("code"))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        response_content = self.mock_responses.get(self.path).get("content")
        self.wfile.write(response_content.encode("utf-8"))
        return


def get_free_port():
    """Find a free port on localhost."""

    s = socket.socket(socket.AF_INET, type=socket.SOCK_STREAM)
    s.bind(("localhost", 0))
    address, port = s.getsockname()
    s.close()
    return port


def start_mock_server(port):
    """Start the server."""

    mock_server = HTTPServer(("localhost", port), MockServerRequestHandler)
    mock_server_thread = Thread(target=mock_server.serve_forever)
    mock_server_thread.setDaemon(True)
    mock_server_thread.start()
