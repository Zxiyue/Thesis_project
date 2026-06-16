from __future__ import annotations

import json
import hashlib
from typing import Any


def normalize(obj: Any) -> Any:
    if isinstance(obj, bytes):
        return {"__bytes__": obj.hex()}
    if isinstance(obj, bytearray):
        return {"__bytes__": bytes(obj).hex()}
    if isinstance(obj, tuple):
        return [normalize(x) for x in obj]
    if isinstance(obj, list):
        return [normalize(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): normalize(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if hasattr(obj, "tolist"):
        return normalize(obj.tolist())
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    return str(obj)


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha256_hex(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def bytes32_hex(obj: Any) -> str:
    return "0x" + sha256_hex(obj)


def hex_to_bytes32(value: str) -> bytes:
    v = value[2:] if value.startswith("0x") else value
    return bytes.fromhex(v.rjust(64, "0")[-64:])


def short(value: Any, n: int = 12) -> str:
    s = str(value)
    return s if len(s) <= n else s[:n] + "..." + s[-6:]


def sha256_hex_raw(data: bytes) -> str:
    return "0x" + hashlib.sha256(data).hexdigest()


def uint256_bytes(x: int) -> bytes:
    return int(x).to_bytes(32, byteorder="big", signed=False)


def audit_init_root(sys_para_hash: str, u_root: str, model_hash0: str) -> str:
    return sha256_hex_raw(b"AUDIT_INIT" + hex_to_bytes32(sys_para_hash) + hex_to_bytes32(u_root) + hex_to_bytes32(model_hash0))


def audit_round_root(prev_root: str, r: int, alpha: str, root_up: str, com_agg_hash: str, model_hash_r: str, model_hash_next: str) -> str:
    return sha256_hex_raw(
        hex_to_bytes32(prev_root)
        + uint256_bytes(r)
        + hex_to_bytes32(alpha)
        + hex_to_bytes32(root_up)
        + hex_to_bytes32(com_agg_hash)
        + hex_to_bytes32(model_hash_r)
        + hex_to_bytes32(model_hash_next)
    )
