from __future__ import annotations

import hashlib
from typing import List, Tuple


def h(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def leaf_hash(value_hex_or_bytes) -> bytes:
    if isinstance(value_hex_or_bytes, str):
        v = value_hex_or_bytes[2:] if value_hex_or_bytes.startswith("0x") else value_hex_or_bytes
        data = bytes.fromhex(v) if all(c in "0123456789abcdefABCDEF" for c in v) and len(v) % 2 == 0 else v.encode()
    else:
        data = bytes(value_hex_or_bytes)
    return h(b"leaf:" + data)


def node_hash(left: bytes, right: bytes) -> bytes:
    return h(b"node:" + left + right)


class MerkleTree:
    def __init__(self, leaf_values: List[str | bytes]):
        self.leaves = [leaf_hash(x) for x in leaf_values]
        if not self.leaves:
            self.levels = [[h(b"EMPTY")]]
        else:
            self.levels = [self.leaves]
            level = self.leaves
            while len(level) > 1:
                if len(level) % 2 == 1:
                    level = level + [level[-1]]
                nxt = [node_hash(level[i], level[i+1]) for i in range(0, len(level), 2)]
                self.levels.append(nxt)
                level = nxt

    @property
    def root(self) -> str:
        return "0x" + self.levels[-1][0].hex()

    def proof(self, index: int) -> List[Tuple[str, str]]:
        proof = []
        idx = index
        for level in self.levels[:-1]:
            work = level
            if len(work) % 2 == 1:
                work = work + [work[-1]]
            sibling = idx ^ 1
            side = "left" if sibling < idx else "right"
            proof.append((side, "0x" + work[sibling].hex()))
            idx //= 2
        return proof


def verify_proof(leaf_value: str | bytes, proof: List[Tuple[str, str]], root_hex: str) -> bool:
    cur = leaf_hash(leaf_value)
    for side, sib_hex in proof:
        sib = bytes.fromhex(sib_hex[2:] if sib_hex.startswith("0x") else sib_hex)
        if side == "left":
            cur = node_hash(sib, cur)
        else:
            cur = node_hash(cur, sib)
    return "0x" + cur.hex() == root_hex
