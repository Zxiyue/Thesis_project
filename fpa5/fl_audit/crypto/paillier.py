from __future__ import annotations

from dataclasses import dataclass
import secrets
import math
from typing import List

from fl_audit.crypto.number import gen_prime, lcm, invmod


@dataclass
class PaillierPublicKey:
    n: int
    g: int

    @property
    def n2(self) -> int:
        return self.n * self.n


@dataclass
class PaillierPrivateKey:
    lam: int
    mu: int


@dataclass
class PaillierKeypair:
    public: PaillierPublicKey
    private: PaillierPrivateKey


def L(u: int, n: int) -> int:
    return (u - 1) // n


def keygen(bits: int = 512) -> PaillierKeypair:
    p = gen_prime(bits // 2)
    q = gen_prime(bits // 2)
    while q == p:
        q = gen_prime(bits // 2)
    n = p * q
    g = n + 1
    lam = lcm(p - 1, q - 1)
    mu = invmod(L(pow(g, lam, n * n), n), n)
    return PaillierKeypair(PaillierPublicKey(n, g), PaillierPrivateKey(lam, mu))


def encode_signed(m: int, n: int) -> int:
    return m % n


def decode_signed(x: int, n: int) -> int:
    return x - n if x > n // 2 else x


def encrypt(pub: PaillierPublicKey, m: int) -> int:
    m_enc = encode_signed(m, pub.n)
    while True:
        r = secrets.randbelow(pub.n)
        if r > 0 and math.gcd(r, pub.n) == 1:
            break
    return (pow(pub.g, m_enc, pub.n2) * pow(r, pub.n, pub.n2)) % pub.n2


def decrypt(pub: PaillierPublicKey, priv: PaillierPrivateKey, c: int) -> int:
    x = (L(pow(c, priv.lam, pub.n2), pub.n) * priv.mu) % pub.n
    return decode_signed(x, pub.n)


def add(pub: PaillierPublicKey, a: int, b: int) -> int:
    return (a * b) % pub.n2


def add_many(pub: PaillierPublicKey, values: List[int]) -> int:
    out = 1
    for v in values:
        out = (out * v) % pub.n2
    return out
