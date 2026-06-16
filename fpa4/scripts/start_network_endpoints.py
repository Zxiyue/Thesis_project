from __future__ import annotations

import argparse
import socketserver
import struct
import json
import hashlib
import threading
import time
from typing import Dict, Tuple

DEFAULT_ENDPOINTS: Dict[str, Tuple[str, int]] = {
    "CS": ("127.0.0.1", 9101),
    "KGC": ("127.0.0.1", 9102),
    "C1": ("127.0.0.1", 9111),
    "C2": ("127.0.0.1", 9112),
    "C3": ("127.0.0.1", 9113),
    "C4": ("127.0.0.1", 9114),
    "C5": ("127.0.0.1", 9115),
}


def _read_exact(sock, n: int) -> bytes:
    out = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(min(65536, remaining))
        if not chunk:
            raise ConnectionError("socket closed")
        out.append(chunk)
        remaining -= len(chunk)
    return b"".join(out)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server  # type: ignore
        t0 = time.perf_counter()
        size = struct.unpack("!Q", _read_exact(self.request, 8))[0]
        payload = _read_exact(self.request, size)
        read_seconds = time.perf_counter() - t0
        ack = {
            "endpoint": server.endpoint_name,
            "received_bytes": size,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "read_seconds": read_seconds,
            "client": f"{self.client_address[0]}:{self.client_address[1]}",
        }
        raw = json.dumps(ack, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.request.sendall(struct.pack("!I", len(raw)))
        self.request.sendall(raw)
        if server.verbose:
            print(f"[{server.endpoint_name}] {size} bytes in {read_seconds:.6f}s")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    def __init__(self, address, endpoint_name: str, verbose: bool = False):
        super().__init__(address, Handler)
        self.endpoint_name = endpoint_name
        self.verbose = verbose


def main():
    ap = argparse.ArgumentParser(description="Start local TCP endpoints for CS, KGC and clients.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--clients", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    endpoints = {
        "CS": (args.host, 9101),
        "KGC": (args.host, 9102),
    }
    for i in range(1, args.clients + 1):
        endpoints[f"C{i}"] = (args.host, 9110 + i)

    servers = []
    for name, addr in endpoints.items():
        srv = Server(addr, endpoint_name=name, verbose=args.verbose)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        servers.append(srv)
        print(f"[network-endpoint] {name} listening on {addr[0]}:{addr[1]}")

    print("All network endpoints are running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("Stopping endpoints...")
        for srv in servers:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    main()
