from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any, Dict

from fl_audit.crypto.paillier import PaillierPrivateKey, PaillierPublicKey, PaillierKeypair
from fl_audit.crypto.pedersen import PedersenParams
from fl_audit.model import model_from_serialized_state, serialize_model_state
from fl_audit.transactions import FinalTx, InitTx


def normalize(obj: Any) -> Any:
    if isinstance(obj, bytes):
        return {"__bytes_b64__": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, bytearray):
        return normalize(bytes(obj))
    if is_dataclass(obj):
        return normalize(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): normalize(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, tuple):
        return [normalize(x) for x in obj]
    if isinstance(obj, list):
        return [normalize(x) for x in obj]
    if hasattr(obj, "tolist"):
        return normalize(obj.tolist())
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(normalize(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def payload_sha256(obj: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def bytes_to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64_to_bytes(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def model_payload(model) -> Dict[str, Any]:
    data = serialize_model_state(model)
    return {
        "modelBytes": bytes_to_b64(data),
        "modelBytesLength": len(data),
        "modelBytesSha256": hashlib.sha256(data).hexdigest(),
    }


def model_bytes_from_payload(payload: Dict[str, Any]) -> bytes:
    data = b64_to_bytes(str(payload["modelBytes"]))
    expected_len = int(payload.get("modelBytesLength", len(data)))
    expected_sha = str(payload.get("modelBytesSha256", hashlib.sha256(data).hexdigest()))
    if len(data) != expected_len:
        raise ValueError(f"modelBytesLength mismatch: {len(data)} != {expected_len}")
    observed_sha = hashlib.sha256(data).hexdigest()
    if observed_sha != expected_sha:
        raise ValueError("modelBytesSha256 mismatch")
    return data


def model_from_payload(template_model, payload: Dict[str, Any]):
    return model_from_serialized_state(template_model, model_bytes_from_payload(payload))


def public_key_to_dict(pub: PaillierPublicKey) -> Dict[str, str]:
    return {"n": str(pub.n), "g": str(pub.g)}


def public_key_from_dict(raw: Dict[str, Any]) -> PaillierPublicKey:
    return PaillierPublicKey(n=int(raw["n"]), g=int(raw["g"]))


def private_key_to_dict(priv: PaillierPrivateKey) -> Dict[str, str]:
    return {"lam": str(priv.lam), "mu": str(priv.mu)}


def private_key_from_dict(raw: Dict[str, Any]) -> PaillierPrivateKey:
    return PaillierPrivateKey(lam=int(raw["lam"]), mu=int(raw["mu"]))


def keypair_to_dict(kp: PaillierKeypair) -> Dict[str, Any]:
    return {"public": public_key_to_dict(kp.public), "private": private_key_to_dict(kp.private)}


def keypair_from_dict(raw: Dict[str, Any]) -> PaillierKeypair:
    return PaillierKeypair(public_key_from_dict(raw["public"]), private_key_from_dict(raw["private"]))


def pedersen_to_dict(params: PedersenParams) -> Dict[str, Any]:
    return {"p": str(params.p), "q": str(params.q), "g_vec": [str(x) for x in params.g_vec], "h": str(params.h)}


def pedersen_from_dict(raw: Dict[str, Any]) -> PedersenParams:
    return PedersenParams(
        p=int(raw["p"]),
        q=int(raw["q"]),
        g_vec=[int(x) for x in raw["g_vec"]],
        h=int(raw["h"]),
    )


def init_tx_to_dict(tx: InitTx) -> Dict[str, Any]:
    return asdict(tx)


def init_tx_from_dict(raw: Dict[str, Any]) -> InitTx:
    return InitTx(**raw)


def final_tx_to_dict(tx: FinalTx) -> Dict[str, Any]:
    return asdict(tx)


def final_tx_from_dict(raw: Dict[str, Any]) -> FinalTx:
    return FinalTx(**raw)

