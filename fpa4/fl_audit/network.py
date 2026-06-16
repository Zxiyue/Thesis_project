from __future__ import annotations

import hashlib
import json
import socket
import struct
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


def canonical_json_bytes(obj: Any) -> bytes:
    """Return canonical JSON bytes used for network-size and socket tests."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def json_size_bytes(obj: Any) -> int:
    """Return canonical JSON serialized size in bytes."""
    return len(canonical_json_bytes(obj))


def _parse_link_destination(link: str) -> str:
    """Parse links like 'C1->CS' and return the destination role name."""
    if "->" not in link:
        return link.strip()
    return link.split("->", 1)[1].strip()


def _endpoint_tuple(raw: Dict[str, Any]) -> Tuple[str, int]:
    return str(raw.get("host", "127.0.0.1")), int(raw.get("port"))


def _read_exact(sock: socket.socket, n: int) -> bytes:
    chunks: List[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(min(65536, remaining))
        if not chunk:
            raise ConnectionError(f"socket closed while reading {n} bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@dataclass
class CommunicationProfiler:
    """Record logical and real socket communication cost.

    Modes
    -----
    estimate:
        Only compute payload size and estimate transmission time by a simple
        bandwidth/latency model.

    real_socket:
        Actually send the payload bytes to the destination endpoint through a
        TCP socket and record measured wall-clock time. This is still a protocol
        experiment, not a full distributed FL deployment: computation may remain
        in one Python process, but every logical message is transmitted through
        the configured network endpoint to obtain real communication time.
    """

    cfg: Dict[str, Any]

    def __post_init__(self) -> None:
        c = self.cfg.get("communication", {}) or {}
        self.enabled = bool(c.get("enabled", True))
        self.mode = str(c.get("mode", "estimate")).lower()
        self.bandwidth_mbps = float(c.get("bandwidth_mbps", 100.0))
        self.latency_ms = float(c.get("latency_ms", 5.0))
        self.apply_delay = bool(c.get("apply_delay", False))
        self.timeout_seconds = float(c.get("timeout_seconds", 120.0))
        self.fallback_to_estimate = bool(c.get("fallback_to_estimate", False))
        self.endpoints: Dict[str, Dict[str, Any]] = dict(c.get("endpoints", {}) or {})
        self.rows: List[Dict[str, Any]] = []

    def estimate_seconds(self, payload_bytes: int, messages: int = 1) -> float:
        if not self.enabled:
            return 0.0
        latency = (self.latency_ms / 1000.0) * max(messages, 1)
        transfer = (float(payload_bytes) * 8.0) / max(self.bandwidth_mbps * 1_000_000.0, 1.0)
        return latency + transfer

    def _payload_to_bytes(self, payload: Any = None, bytes_count: int | None = None) -> bytes:
        if payload is not None:
            if isinstance(payload, bytes):
                return payload
            if isinstance(payload, bytearray):
                return bytes(payload)
            return canonical_json_bytes(payload)
        if bytes_count is None:
            return b""
        # When only size is known, send a deterministic dummy byte stream with
        # exactly the same size. This measures real link transfer cost while
        # avoiding unnecessary reconstruction of huge model vectors.
        return b"0" * int(bytes_count)

    def _send_socket(self, destination: str, payload_bytes: bytes) -> Dict[str, Any]:
        if destination not in self.endpoints:
            raise KeyError(f"No endpoint configured for destination '{destination}'.")
        host, port = _endpoint_tuple(self.endpoints[destination])
        digest = hashlib.sha256(payload_bytes).hexdigest()
        t0 = time.perf_counter()
        with socket.create_connection((host, port), timeout=self.timeout_seconds) as sock:
            sock.settimeout(self.timeout_seconds)
            sock.sendall(struct.pack("!Q", len(payload_bytes)))
            if payload_bytes:
                sock.sendall(payload_bytes)
            ack_len = struct.unpack("!I", _read_exact(sock, 4))[0]
            ack_raw = _read_exact(sock, ack_len)
        elapsed = time.perf_counter() - t0
        ack = json.loads(ack_raw.decode("utf-8"))
        if int(ack.get("received_bytes", -1)) != len(payload_bytes):
            raise RuntimeError("network endpoint acknowledged a different byte count")
        if str(ack.get("sha256", "")) != digest:
            raise RuntimeError("network endpoint acknowledged a different payload hash")
        return {
            "actual_seconds": elapsed,
            "receiver_host": host,
            "receiver_port": port,
            "ack_bytes": int(ack.get("received_bytes", 0)),
            "ack_sha256": ack.get("sha256", ""),
            "endpoint_name": ack.get("endpoint", destination),
        }

    def record(self, round_id: int, link: str, payload_type: str, payload: Any = None,
               bytes_count: int | None = None, messages: int = 1, note: str = "") -> float:
        if not self.enabled:
            return 0.0

        payload_bytes_blob = self._payload_to_bytes(payload=payload, bytes_count=bytes_count)
        payload_bytes = len(payload_bytes_blob)
        estimated = self.estimate_seconds(payload_bytes, messages=messages)
        destination = _parse_link_destination(link)

        actual_seconds: float | None = None
        receiver_host = ""
        receiver_port: int | str = ""
        status = "estimated"
        error = ""
        ack_sha256 = ""
        endpoint_name = ""

        if self.mode in {"real", "real_socket", "socket", "tcp"}:
            try:
                net = self._send_socket(destination, payload_bytes_blob)
                actual_seconds = float(net["actual_seconds"])
                receiver_host = net["receiver_host"]
                receiver_port = net["receiver_port"]
                ack_sha256 = str(net["ack_sha256"])
                endpoint_name = str(net["endpoint_name"])
                status = "real_socket"
            except Exception as exc:
                if not self.fallback_to_estimate:
                    raise
                status = "fallback_estimate"
                error = repr(exc)

        seconds_for_delay = actual_seconds if actual_seconds is not None else estimated
        self.rows.append({
            "round": round_id,
            "link": link,
            "receiver": destination,
            "payload_type": payload_type,
            "messages": int(messages),
            "bytes": int(payload_bytes),
            "network_mode": self.mode,
            "bandwidth_mbps": self.bandwidth_mbps,
            "latency_ms": self.latency_ms,
            "estimated_seconds": float(estimated),
            "actual_seconds": "" if actual_seconds is None else float(actual_seconds),
            "receiver_host": receiver_host,
            "receiver_port": receiver_port,
            "endpoint_name": endpoint_name,
            "ack_sha256": ack_sha256,
            "status": status,
            "error": error,
            "note": note,
        })
        if self.apply_delay and seconds_for_delay > 0 and actual_seconds is None:
            time.sleep(seconds_for_delay)
        return float(seconds_for_delay)

    def record_many(self, round_id: int, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            self.record(round_id=round_id, **row)
