"""The ONE HTTP server in this project.

Render Web Services must see an open port. GET / and GET /health return "OK".
It runs in a daemon thread, so it never blocks the Telegram bot. It is started
at most once per process, and a port conflict is reported instead of crashing.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_state = {"started": False, "server": None, "status": "not started"}
_lock = threading.Lock()


class _HealthHandler(BaseHTTPRequestHandler):
    def _reply(self, include_body):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/health"):
            code, body = 200, b"OK"
        else:
            code, body = 404, b"Not found"
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self):
        self._reply(include_body=True)

    def do_HEAD(self):
        self._reply(include_body=False)

    def log_message(self, format, *args):
        # Keep request logs quiet; health checks happen every few seconds on Render.
        return


def start_health_server(host, port):
    """Start the health endpoint once. Returns (server_or_None, status_text)."""
    with _lock:
        if _state["started"]:
            return _state["server"], _state["status"]
        _state["started"] = True
        try:
            server = ThreadingHTTPServer((host, port), _HealthHandler)
        except OSError as exc:
            _state["status"] = (
                f"FAILED on port {port} ({exc.strerror or 'port unavailable'}). "
                "The bot keeps running without the HTTP endpoint."
            )
            return None, _state["status"]
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, name="health-http", daemon=True).start()
        bound_host, bound_port = server.server_address[:2]
        _state["server"] = server
        _state["status"] = f"OK on {bound_host}:{bound_port}"
        return server, _state["status"]
