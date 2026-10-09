"""Real servers for the tool tests: the mock company (uvicorn) and tiny throwaway HTTP servers."""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import uvicorn

from apps.company.app import create_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_company(data_dir, faults=(), admin_token="t"):
    """Start the mock company on a free port. Returns (url, stop)."""
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(data_dir, faults=faults, admin_token=admin_token),
                                           host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "mock company did not start"

    def stop():
        server.should_exit = True
        thread.join(timeout=10)

    return f"http://127.0.0.1:{port}", stop


def html(body: str, status: int = 200):
    return status, {"Content-Type": "text/html"}, f"<html><head><title>t</title></head><body>{body}</body></html>".encode()


def redirect(location: str, status: int = 302):
    return status, {"Location": location}, b""


def start_tiny(routes: dict):
    """A tiny server. routes: 'path' or 'POST path' -> (status, headers, body) or a callable returning it.
    Returns (url, hits, stop); hits lists 'METHOD path' for every request that arrived."""
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def _serve(self, method):
            if method == "POST":
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
            hits.append(f"{method} {self.path}")
            path = self.path.split("?")[0]
            entry = routes.get(f"{method} {path}") or (routes.get(path) if method == "GET" else None)
            status, headers, body = entry() if callable(entry) else entry or (404, {}, b"not found")
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

        def log_message(self, *args):
            pass

    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop():
        server.shutdown()
        server.server_close()

    return f"http://127.0.0.1:{port}", hits, stop
