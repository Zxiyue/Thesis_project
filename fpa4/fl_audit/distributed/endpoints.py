from __future__ import annotations

import hashlib
import json
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict
from urllib.parse import urlparse


Route = Callable[[Dict[str, Any]], Dict[str, Any]]


def serve_json(host: str, port: int, routes: Dict[tuple[str, str], Route]) -> None:
    class Handler(BaseHTTPRequestHandler):
        server_version = "FLAuditHTTP/1.0"

        def _send(self, code: int, obj: Dict[str, Any]) -> None:
            raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            fn = routes.get(("GET", path))
            if fn is None:
                self._send(404, {"ok": False, "error": f"unknown GET {path}"})
                return
            try:
                self._send(200, {"ok": True, **fn({})})
            except Exception as exc:
                self._send(500, {"ok": False, "error": repr(exc)})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            fn = routes.get(("POST", path))
            if fn is None:
                self._send(404, {"ok": False, "error": f"unknown POST {path}"})
                return
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n)
            digest = hashlib.sha256(raw).hexdigest()
            recv_time = time.time()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                result = fn(payload)
                self._send(200, {
                    "ok": True,
                    "ack_sha256": digest,
                    "server_receive_time": recv_time,
                    "server_response_time": time.time(),
                    **result,
                })
            except Exception as exc:
                self._send(500, {
                    "ok": False,
                    "ack_sha256": digest,
                    "server_receive_time": recv_time,
                    "server_response_time": time.time(),
                    "error": repr(exc),
                    "traceback": traceback.format_exc(),
                })

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((host, int(port)), Handler)
    server.serve_forever()
