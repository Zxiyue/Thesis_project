from __future__ import annotations

from typing import List

from fl_audit.crypto.signature import verify_obj
from fl_audit.utils.codec import audit_init_root, audit_round_root


def verify_init(init_tx, kgc_pk: str, sys_para: dict) -> bool:
    payload = init_tx.payload()
    if not verify_obj(kgc_pk, init_tx.sigInit, payload):
        return False
    return init_tx.auditRoot0 == audit_init_root(init_tx.sysParaHash, init_tx.Uroot, init_tx.modelHash0)


def verify_chain(init_tx, final_txs: List, kgc_pk: str) -> bool:
    prev_root = init_tx.auditRoot0
    prev_model = init_tx.modelHash0
    prev_round = 0
    for tx in final_txs:
        if tx.r != prev_round + 1:
            return False
        payload = tx.payload()
        if not verify_obj(kgc_pk, tx.sigFinal, payload):
            return False
        if tx.modelHashR != prev_model:
            return False
        expected = audit_round_root(prev_root, tx.r, tx.alpha, tx.rootUp, tx.ComAggHash, tx.modelHashR, tx.modelHashNext)
        if tx.auditRoot != expected:
            return False
        prev_root = tx.auditRoot
        prev_model = tx.modelHashNext
        prev_round = tx.r
    return True
