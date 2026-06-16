from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict


@dataclass
class InitTx:
    sysParaHash: str
    Uroot: str
    modelHash0: str
    auditRoot0: str
    sigInit: str

    def payload(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("sigInit")
        return d


@dataclass
class FinalTx:
    r: int
    alpha: str
    rootUp: str
    # ComAgg is a very large integer value.  It is serialized as a decimal
    # string in every signed/audited payload so that ECDSA verification is
    # stable across Python, JSON, Solidity/Web3 and CSV/JSON exports.
    ComAgg: str
    ComAggHash: str
    modelHashR: str
    modelHashNext: str
    auditRoot: str
    sigFinal: str

    def __post_init__(self) -> None:
        self.ComAgg = str(self.ComAgg)

    def payload(self) -> Dict[str, Any]:
        # Keep this payload bit-for-bit consistent with KGC.final_confirm().
        # In V1 KGC signed {"ComAgg": "123..."}, while tx.payload() returned
        # {"ComAgg": 123...}; canonical JSON bytes differed and ECDSA failed.
        d = asdict(self)
        d.pop("sigFinal")
        d["ComAgg"] = str(d["ComAgg"])
        return d


@dataclass
class FraudProofTx:
    r: int
    client_id: int
    receipt: Dict[str, Any]
    path: list
    claimType: str
    sigClient: str
