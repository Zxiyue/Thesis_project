from __future__ import annotations

import argparse
import hashlib
import json
import socketserver
import struct
import time
from typing import Tuple


def _read_exact(sock, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(min(65536, remaining))
        if not chunk:
            raise ConnectionError(f"socket closed while reading {n} bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: "NetworkEndpointServer" = self.server  # type: ignore[assignment]
        t0 = time.perf_counter()
        header = _read_exact(self.request, 8)
        size = struct.unpack("!Q", header)[0]
        payload = _read_exact(self.request, size)
        elapsed_read = time.perf_counter() - t0
        digest = hashlib.sha256(payload).hexdigest()
        ack = {
            "endpoint": server.endpoint_name,
            "received_bytes": size,
            "sha256": digest,
            "read_seconds": elapsed_read,
            "client": f"{self.client_address[0]}:{self.client_address[1]}",
        }
        raw = json.dumps(ack, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.request.sendall(struct.pack("!I", len(raw)))
        self.request.sendall(raw)
        if server.verbose:
            print(f"[{server.endpoint_name}] received {size} bytes from {ack['client']} in {elapsed_read:.6f}s")


class NetworkEndpointServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address: Tuple[str, int], handler, endpoint_name: str, verbose: bool = False):
        super().__init__(server_address, handler)
        self.endpoint_name = endpoint_name
        self.verbose = verbose


def main() -> None:
    ap = argparse.ArgumentParser(description="TCP endpoint for measuring real communication time.")
    ap.add_argument("--name", required=True, help="Endpoint name, e.g., CS, KGC, C1")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    with NetworkEndpointServer((args.host, args.port), EchoHandler, endpoint_name=args.name, verbose=args.verbose) as srv:
        print(f"[network-endpoint] {args.name} listening on {args.host}:{args.port}")
        srv.serve_forever()


if __name__ == "__main__":
    main()
