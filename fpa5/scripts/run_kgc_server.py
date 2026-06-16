from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import torch

from fl_audit.blockchain.contract_client import AuditBoardClient
from fl_audit.crypto import pedersen
from fl_audit.crypto.signature import verify_obj
from fl_audit.distributed.distributed_logger import JsonlLogger, setup_text_logger
from fl_audit.distributed.endpoints import serve_json
from fl_audit.distributed.http_client import send_json
from fl_audit.distributed.message_codec import (
    final_tx_to_dict,
    init_tx_to_dict,
    model_from_payload,
    model_payload,
    pedersen_to_dict,
    public_key_to_dict,
)
from fl_audit.distributed.state_store import client_endpoint_info, client_url, ensure_runtime_dirs, entity_logs_dir, load_config, logs_dir, output_dir
from fl_audit.encoding import quantize
from fl_audit.model import get_vector, make_model, model_hash, set_vector
from fl_audit.protocol.entities import KGC


def set_seed(seed: int) -> None:
    import random

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


class KGCService:
    def __init__(self, cfg: Dict[str, Any], config_path: str):
        self.cfg = cfg
        self.config_path = config_path
        ensure_runtime_dirs(cfg)
        self.logger = setup_text_logger("kgc", logs_dir(cfg))
        self.events = JsonlLogger(entity_logs_dir(cfg) / "kgc_events.jsonl")
        self.comm_log = str(entity_logs_dir(cfg) / "kgc_communication.jsonl")
        self.kgc = KGC()
        self.model = None
        self.pkey = None
        self.ped_params = None
        self.U: List[int] = []
        self.Uroot = ""
        self.client_pubkeys: Dict[int, str] = {}
        self.client_endpoints: Dict[int, str] = {}
        self.cs_public_key = ""
        self.prev_audit_root = ""
        self.current_model_hash = ""
        self.final_txs = []
        self.blockchain_rows: List[Dict[str, Any]] = []
        self.board = None

    def _maybe_board(self):
        bc = self.cfg.get("blockchain", {}) or {}
        if not bool(bc.get("enabled", False)):
            return None
        if self.board is None:
            self.board = AuditBoardClient(str(bc["rpc_url"]), str(bc["contract_json"]))
        return self.board

    def health(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {"role": "KGC", "ready": True}

    def state(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "current_model_hash": self.current_model_hash,
            "rounds_finalized": len(self.final_txs),
            "clients": self.U,
        }

    def setup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        set_seed(int(self.cfg.get("seed", 42)))
        self.model = make_model(self.cfg["model"]["name"])
        client_public_keys = {int(k): v for k, v in payload["client_public_keys"].items()}
        self.client_pubkeys = client_public_keys
        self.client_endpoints = {int(k): str(v) for k, v in payload.get("client_endpoints", {}).items()}
        self.cs_public_key = str(payload.get("cs_public_key", ""))
        clients = [SimpleNamespace(cid=cid, public_key=client_public_keys[cid]) for cid in sorted(client_public_keys)]
        setup = self.kgc.setup(clients, self.model, self.cfg)
        self.pkey = setup["paillier"]
        self.ped_params = setup["pedersen"]
        self.U = setup["U"]
        self.Uroot = setup["Uroot"]
        self.prev_audit_root = setup["auditRoot0"]
        self.current_model_hash = setup["modelHash0"]
        self.blockchain_rows = []
        try:
            board = self._maybe_board()
            if board is not None:
                self.blockchain_rows.append({"round": 0, "tx_type": "InitTx", **board.submit_init(setup["initTx"])})
        except Exception as exc:
            self.blockchain_rows.append({"round": 0, "tx_type": "InitTx", "status": "error", "error": repr(exc)})
        shares = {c.cid: {"share": str(c.share), "share_field": str(c.share_field)} for c in clients}
        self.events.append("setup", modelHash0=self.current_model_hash, U=self.U)
        return {
            "kgc_public_key": self.kgc.public_key,
            "initTx": init_tx_to_dict(setup["initTx"]),
            "sysPara": setup["sysPara"],
            "U": self.U,
            "Uroot": self.Uroot,
            "modelHash0": self.current_model_hash,
            "paillier_public": public_key_to_dict(self.pkey.public),
            "pedersen": pedersen_to_dict(self.ped_params),
            "shareField": str(setup["shareField"]),
            "shares": shares,
            **model_payload(self.model),
            "blockchain_rows": self.blockchain_rows,
        }

    def round_init(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = int(payload["r"])
        model_hash_r = str(payload["modelHash"])
        self.client_endpoints.update({int(k): str(v) for k, v in payload.get("client_endpoints", {}).items()})
        alpha, masks, rhos = self.kgc.init_round(
            r,
            model_hash_r,
            self.U,
            self.Uroot,
            int(get_vector(self.model).numel()),
            self.ped_params,
            int(self.cfg.get("seed", 42)),
        )
        timeout = float(self.cfg.get("communication", {}).get("timeout_seconds", 300))
        for cid in self.U:
            msg = {"r": r, "i": cid, "client_id": cid, "alpha": alpha, "modelHash": model_hash_r, "m": masks[cid].tolist(), "rho": str(rhos[cid])}
            from fl_audit.crypto.signature import sign_obj

            msg["sigKGC"] = sign_obj(self.kgc.keypair.private_key, {k: v for k, v in msg.items() if k != "sigKGC"})
            url = self.client_endpoints.get(cid) or client_url(self.cfg, cid, "/receive_mask")
            if not url.endswith("/receive_mask"):
                url = url.rstrip("/") + "/receive_mask"
            send_json("KGC", f"C{cid}", url, "MaskMsg", msg, r, client_id=cid, worker_id=client_endpoint_info(self.cfg, cid)[1], timeout=timeout, log_path=self.comm_log)
        self.current_model_hash = model_hash_r
        self.events.append("round_init", round=r, alpha=alpha, modelHash=model_hash_r)
        return {"alpha": alpha}

    def upload_copy(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        msg = payload["UpMsg"]
        self.kgc.state_cache.setdefault(int(msg["r"]), {}).setdefault("up_msgs", []).append(msg)
        self.events.append("upload_copy", round=int(msg["r"]), client_id=int(msg["i"]))
        return {"stored": True}

    def verify_uploads(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = int(payload["r"])
        up_msgs = list(self.kgc.state_cache.get(r, {}).get("up_msgs", []))
        valid, root_up = self.kgc.verify_uploads_and_root(r, up_msgs, self.client_pubkeys)
        self.kgc.state_cache.setdefault(r, {})["valid_up_msgs"] = valid
        self.kgc.state_cache[r]["rootUp"] = root_up
        self.events.append("verify_uploads", round=r, valid_clients=[int(m["i"]) for m in valid], rootUp=root_up)
        return {"valid_up_msgs": valid, "rootUp": root_up}

    def compensation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = int(payload["r"])
        valid = self.kgc.state_cache.get(r, {}).get("valid_up_msgs", [])
        U_r1 = sorted([int(m["i"]) for m in valid])
        D_r = sorted(list(set(self.U) - set(U_r1)))
        m_drop, rho_drop, cdrop, com_drop = self.kgc.make_compensation(r, D_r, self.pkey.public, self.ped_params)
        self.events.append("compensation", round=r, D_r=D_r)
        return {
            "D_r": D_r,
            "Cdrop": [str(c) for c in cdrop],
            "ComDrop": str(com_drop),
            "identityOptimized": len(D_r) == 0,
        }

    def final_confirm(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = int(payload["r"])
        alpha = str(payload["alpha"])
        root_up = str(payload["rootUp"])
        com_agg = int(payload["ComAgg"])
        cagg_hash = str(payload["CaggHash"])
        model_hash_r = str(payload["modelHashR"])
        model_hash_next = str(payload["modelHashNext"])
        sig_model = str(payload.get("sigModel", ""))

        if model_hash_r != self.current_model_hash:
            raise RuntimeError(f"KGC modelHashR mismatch: {model_hash_r} != {self.current_model_hash}")

        model_msg_payload = {
            "r": r,
            "alpha": alpha,
            "modelHashR": model_hash_r,
            "modelHashNext": model_hash_next,
            "CaggHash": cagg_hash,
            "ComAgg": str(com_agg),
        }
        if self.cs_public_key and not verify_obj(self.cs_public_key, sig_model, model_msg_payload):
            raise RuntimeError("invalid CS signature on ModelMsg")

        next_model = model_from_payload(self.model, payload)
        observed_hash = model_hash(next_model)
        if observed_hash != model_hash_next:
            raise RuntimeError(f"KGC model hash mismatch: {observed_hash} != {model_hash_next}")

        # KGC does not receive xAgg. It derives the encoded aggregate update from
        # the published model difference and checks that it opens ComAgg.
        u_model = (get_vector(next_model).double() - get_vector(self.model).double()) / float(self.cfg["federated"].get("server_lr", 1.0))
        x_model = quantize(u_model.numpy(), int(self.cfg["encoding"]["scale"]))
        if pedersen.commit(self.ped_params, x_model.tolist(), 0) != com_agg:
            raise RuntimeError("KGC model differential commitment check failed")

        final_tx = self.kgc.final_confirm(r, alpha, root_up, com_agg, model_hash_r, model_hash_next, self.prev_audit_root)
        self.final_txs.append(final_tx)
        self.prev_audit_root = final_tx.auditRoot
        self.current_model_hash = model_hash_next
        self.model = next_model
        try:
            board = self._maybe_board()
            if board is not None:
                self.blockchain_rows.append({"round": r, "tx_type": "FinalTx", **board.submit_final(final_tx)})
        except Exception as exc:
            self.blockchain_rows.append({"round": r, "tx_type": "FinalTx", "status": "error", "error": repr(exc)})
        self.events.append("final_confirm", round=r, modelHashNext=model_hash_next, auditRoot=final_tx.auditRoot)
        return {"FinalTx": final_tx_to_dict(final_tx), "blockchain_rows": self.blockchain_rows}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9200)
    ap.add_argument("--config", required=True)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    service = KGCService(cfg, args.config)
    service.logger.info("KGC listening on %s:%s", args.host, args.port)
    serve_json(args.host, args.port, {
        ("GET", "/health"): service.health,
        ("GET", "/state"): service.state,
        ("POST", "/setup"): service.setup,
        ("POST", "/round_init"): service.round_init,
        ("POST", "/upload_copy"): service.upload_copy,
        ("POST", "/verify_uploads"): service.verify_uploads,
        ("POST", "/compensation"): service.compensation,
        ("POST", "/final_confirm"): service.final_confirm,
    })


if __name__ == "__main__":
    main()

