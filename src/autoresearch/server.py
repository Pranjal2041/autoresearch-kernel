"""The kernel HTTP API: the only channel between agent and kernel.

HTTP only, by design. Anything that can curl can be an agent.

    POST /submit            payload per experiment signature, returns {submit_id}
    GET  /submit/{id}       record including score when ready
    GET  /history           all records, test metrics stripped
    GET  /best              best submit so far
    GET  /experiment        objective, rules, budgets remaining
    GET  /health

The agent-visible API never leaks test metrics: every record passes through
SubmitRecord.to_public() before serialization.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .state import RunState

MAX_BODY_BYTES = 1024 * 1024  # a submit payload is metadata, not data

_SUBMIT_ID_RE = re.compile(r"^/submit/(\d+)$")


def make_handler(state: RunState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet; the event log is the record
            pass

        def _json(self, status: int, body: dict | list | None) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _agent_prefix(self, path: str) -> tuple[int, str]:
            """Parallel agents get AR_API_URL=.../a/<idx>; strip and return it."""
            m = re.match(r"^/a/(\d+)(/.*)?$", path)
            if m:
                return int(m.group(1)), (m.group(2) or "/")
            return 0, path

        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            _, path = self._agent_prefix(path)
            path = path.rstrip("/") or "/"
            if path == "/health":
                self._json(200, {"ok": True})
            elif path == "/experiment":
                self._json(200, state.experiment_info())
            elif path == "/history":
                self._json(200, state.public_history())
            elif path == "/best":
                best = state.public_best()
                self._json(200 if best else 404, best or {"error": "no scored submits yet"})
            elif m := _SUBMIT_ID_RE.match(path):
                rec = state.public_record(int(m.group(1)))
                self._json(200 if rec else 404, rec or {"error": "no such submit"})
            else:
                self._json(404, {"error": f"no route {path}"})

        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            agent_idx, path = self._agent_prefix(path)
            if path.rstrip("/") != "/submit":
                self._json(404, {"error": f"no route {path}"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > MAX_BODY_BYTES:
                    self._json(413, {"error": "payload too large"})
                    return
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "body must be valid JSON"})
                return
            body, status = state.submit(payload, agent_idx=agent_idx)
            self._json(status, body)

    return Handler


class KernelServer:
    def __init__(self, state: RunState, bind: str = "127.0.0.1", port: int = 0):
        self._server = ThreadingHTTPServer((bind, port), make_handler(state))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="kernel-api")

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
