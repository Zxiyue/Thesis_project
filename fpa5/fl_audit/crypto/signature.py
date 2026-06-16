from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.exceptions import InvalidSignature

from fl_audit.utils.codec import canonical_bytes


@dataclass
class ECDSAKeyPair:
    private_key: Any
    public_pem: str


def generate_keypair() -> ECDSAKeyPair:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    pem = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return ECDSAKeyPair(private_key=private_key, public_pem=pem)


def sign_obj(private_key: Any, obj: Any) -> str:
    sig = private_key.sign(canonical_bytes(obj), ec.ECDSA(hashes.SHA256()))
    return sig.hex()


def verify_obj(public_pem: str, signature_hex: str, obj: Any) -> bool:
    public_key = serialization.load_pem_public_key(public_pem.encode("utf-8"))
    try:
        public_key.verify(bytes.fromhex(signature_hex), canonical_bytes(obj), ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
