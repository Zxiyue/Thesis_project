from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
import secrets

from fl_audit.crypto.number import invmod


@dataclass
class ShamirShares:
    prime: int
    threshold: int
    shares: Dict[int, int]


def split_secret(secret: int, n: int, threshold: int, prime: int) -> ShamirShares:
    if secret >= prime:
        raise ValueError("field prime must be greater than secret")
    coeffs = [secret] + [secrets.randbelow(prime) for _ in range(threshold - 1)]
    shares = {}
    for x in range(1, n + 1):
        y = 0
        xp = 1
        for c in coeffs:
            y = (y + c * xp) % prime
            xp = (xp * x) % prime
        shares[x] = y
    return ShamirShares(prime=prime, threshold=threshold, shares=shares)


def reconstruct(shares: List[Tuple[int, int]], prime: int) -> int:
    total = 0
    for i, yi in shares:
        num, den = 1, 1
        for j, _ in shares:
            if i == j:
                continue
            num = (num * (-j)) % prime
            den = (den * (i - j)) % prime
        total = (total + yi * num * invmod(den, prime)) % prime
    return total
