from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from web3 import Web3

from fl_audit.utils.codec import hex_to_bytes32, bytes32_hex


class AuditBoardClient:
    def __init__(self, rpc_url: str, contract_json: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.w3.is_connected():
            raise RuntimeError(f"Cannot connect to blockchain RPC: {rpc_url}")
        meta = json.loads(Path(contract_json).read_text(encoding="utf-8"))
        self.address = Web3.to_checksum_address(meta["address"])
        self.abi = meta["abi"]
        self.contract = self.w3.eth.contract(address=self.address, abi=self.abi)
        self.account = self.w3.eth.accounts[0]

    def _send(self, fn) -> Dict[str, Any]:
        t0 = time.perf_counter()
        tx_hash = fn.transact({"from": self.account})
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        t1 = time.perf_counter()
        return {
            "tx_hash": tx_hash.hex(),
            "block_number": receipt.blockNumber,
            "gas_used": receipt.gasUsed,
            "latency_sec": t1 - t0,
            "status": receipt.status,
        }

    def submit_init(self, tx) -> Dict[str, Any]:
        return self._send(self.contract.functions.submitInit(
            hex_to_bytes32(tx.sysParaHash),
            hex_to_bytes32(tx.Uroot),
            hex_to_bytes32(tx.modelHash0),
            hex_to_bytes32(tx.auditRoot0),
            bytes.fromhex(tx.sigInit),
        ))

    def submit_final(self, tx) -> Dict[str, Any]:
        return self._send(self.contract.functions.submitFinal(
            int(tx.r),
            hex_to_bytes32(tx.alpha),
            hex_to_bytes32(tx.rootUp),
            hex_to_bytes32(tx.ComAggHash),
            hex_to_bytes32(tx.modelHashR),
            hex_to_bytes32(tx.modelHashNext),
            hex_to_bytes32(tx.auditRoot),
            bytes.fromhex(tx.sigFinal),
        ))

    def submit_fraud_proof(self, fraud_tx) -> Dict[str, Any]:
        client_hash = bytes32_hex({"client_id": fraud_tx.client_id})
        receipt_hash = bytes32_hex(fraud_tx.receipt)
        root_up = fraud_tx.receipt.get("rootUp", "0x" + "00" * 32)
        return self._send(self.contract.functions.submitFraudProof(
            int(fraud_tx.r),
            hex_to_bytes32(client_hash),
            hex_to_bytes32(receipt_hash),
            hex_to_bytes32(root_up),
            fraud_tx.claimType,
            json.dumps(fraud_tx.path).encode("utf-8"),
            bytes.fromhex(fraud_tx.sigClient),
        ))
