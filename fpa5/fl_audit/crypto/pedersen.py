from __future__ import annotations

from dataclasses import dataclass
from typing import List
import hashlib

from fl_audit.crypto.number import gen_prime


@dataclass
class PedersenParams:
    p: int
    q: int
    g_vec: List[int]
    h: int


def _hash_to_base(p: int, domain: str, index: int) -> int:
    digest = hashlib.sha256(f"{domain}|{index}".encode("utf-8")).digest()
    return 2 + (int.from_bytes(digest, "big") % (p - 3))


def make_params(dim: int, bits: int = 521) -> PedersenParams:
    # Deterministic hash-derived bases avoid keeping a large set of random bases
    # and prevent MemoryError for high-dimensional CNN/linear models.
    p = gen_prime(bits)
    q = p - 1
    g_vec = [_hash_to_base(p, "pedersen-g", j) for j in range(dim)]
    h = _hash_to_base(p, "pedersen-h", 0)
    return PedersenParams(p=p, q=q, g_vec=g_vec, h=h)


def commit(params: PedersenParams, x: List[int], rho: int) -> int:
    if len(x) != len(params.g_vec):
        raise ValueError(f"dimension mismatch: {len(x)} != {len(params.g_vec)}")
    acc = 1
    for gj, xj in zip(params.g_vec, x):
        acc = (acc * pow(gj, int(xj) % params.q, params.p)) % params.p
    acc = (acc * pow(params.h, int(rho) % params.q, params.p)) % params.p
    return acc


def mul(params: PedersenParams, values: List[int]) -> int:
    acc = 1
    for v in values:
        acc = (acc * int(v)) % params.p
    return acc
