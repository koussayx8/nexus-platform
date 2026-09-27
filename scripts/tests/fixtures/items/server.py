"""Stand-in for sample-api behind a port-forward, for scripts/tests/verify-items.sh.

Usage: server.py <port> <mode>. Serves /health 200 and one canned /items answer per mode.
Standard library only.
"""

import http.server
import json
import sys

PORT = int(sys.argv[1])
MODE = sys.argv[2]

ITEMS = {
    "ok": (200, {"items": [{"id": i, "name": f"item-{i}"} for i in range(1, 21)]}),
    "empty": (200, {"items": []}),
    "db": (503, {"error": "db_unavailable"}),
    "junk": (503, {"error": "x; SHOULD-NOT-APPEAR=1"}),
    "notfound": (404, {"detail": "Not Found"}),
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "healthy"})
        elif self.path == "/items":
            self.reply(*ITEMS[MODE])
        else:
            self.reply(404, {"detail": "Not Found"})


http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
