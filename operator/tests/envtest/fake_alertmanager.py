"""A fake Alertmanager for the envtest integration test (M1b-8). Test-only.

Serves GET /api/v2/alerts on 127.0.0.1 from a JSON file, re-read on every request, so the test
changes the firing alerts by rewriting the file. Writes its port to a file and logs each request
(path and query) to stderr.

Usage: fake_alertmanager.py <alerts.json> <port file>
"""

import http.server
import pathlib
import sys
import urllib.parse

ALERTS, PORT_FILE = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if urllib.parse.urlsplit(self.path).path != "/api/v2/alerts":
            self.send_error(404)
            return
        body = ALERTS.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(
            f"fake-alertmanager {self.log_date_time_string()} {fmt % args}\n"
        )


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
PORT_FILE.write_text(str(server.server_address[1]))
server.serve_forever()
