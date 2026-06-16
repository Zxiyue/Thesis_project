from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import torch

from fl_audit.crypto.signature import verify_obj
from fl_audit.data import make_loaders
from fl_audit.distributed.distributed_logger import JsonlLogger, setup_text_logger
from fl_audit.distributed.endpoints import serve_json
from fl_audit.distributed.http_client import send_json
from fl_audit.distributed.message_codec import (
    model_bytes_from_payload,
    model_from_payload,
    model_payload,
    pedersen_from_dict,
    public_key_from_dict,
)
from fl_audit.distributed.state_store import ensure_runtime_dirs, entity_logs_dir, load_config, logs_dir
from fl_audit.model import make_model, model_hash
from fl_audit.protocol.entities import Client, model_sync_signed_payload
from fl_audit.utils.timer import TimerRecorder


def set_seed(seed: int) -> None:
    import random

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


class ClientService:
    def __init__(self, cid: int, cfg: Dict[str, Any], config_path: str):
        self.cid = int(cid)
        self.cfg = cfg
        self.config_path = config_path
        ensure_runtime_dirs(cfg)
        self.logger = setup_text_logger(f"client_{self.cid}", logs_dir(cfg))
        self.events = JsonlLogger(entity_logs_dir(cfg) / f"client_{self.cid}_events.jsonl")
        self.comm_log = str(entity_logs_dir(cfg) / f"client_{self.cid}_communication.jsonl")
        set_seed(int(cfg.get("seed", 42)) + self.cid)
        self.client = Client(cid=self.cid)
        self.model_template = make_model(cfg["model"]["name"])
        self.train_loaders, _, _ = make_loaders(cfg)
        self.masks: Dict[int, Any] = {}
        self.rhos: Dict[int, int] = {}
        self.alpha_by_round: Dict[int, str] = {}
        self.paillier_pub = None
        self.ped_params = None
        self.total_samples = 0
        self.cs_public_key = ""
        self.kgc_public_key = ""
        self.cs_url = ""
        self.kgc_url = ""
        self.timer = TimerRecorder()

    def health(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {"role": "Client", "cid": self.cid, "ready": True}

    def state(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "cid": self.cid,
            "local_model_hash": self.client.local_model_hash,
            "known_masks": sorted(self.masks),
        }

    def public_key(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {"cid": self.cid, "public_key": self.client.public_key}

    def init_model(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from fl_audit.crypto.signature import ECDSAKeyPair

        self.client.local_model = model_from_payload(self.model_template, payload)
        self.client.local_model_hash = str(payload["modelHash"])
        if model_hash(self.client.local_model) != self.client.local_model_hash:
            raise RuntimeError("initial model hash mismatch")
        self.client.share = int(payload["share"])
        self.client.share_field = int(payload["share_field"])
        self.paillier_pub = public_key_from_dict(payload["paillier_public"])
        self.ped_params = pedersen_from_dict(payload["pedersen"])
        self.total_samples = int(payload["total_samples"])
        self.cs_public_key = str(payload["cs_public_key"])
        self.kgc_public_key = str(payload["kgc_public_key"])
        self.cs_url = str(payload["cs_url"]).rstrip("/")
        self.kgc_url = str(payload["kgc_url"]).rstrip("/")
        self.events.append("init_model", client_id=self.cid, modelHash=self.client.local_model_hash)
        return {"client_id": self.cid, "local_model_hash": self.client.local_model_hash}

    def receive_mask(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = int(payload["r"])
        signed = {k: v for k, v in payload.items() if k != "sigKGC"}
        if self.kgc_public_key and not verify_obj(self.kgc_public_key, str(payload["sigKGC"]), signed):
            raise RuntimeError("invalid KGC signature on MaskMsg")
        self.masks[r] = np.array([int(x) for x in payload["m"]], dtype=np.int64)
        self.rhos[r] = int(payload["rho"])
        self.alpha_by_round[r] = str(payload["alpha"])
        self.events.append("receive_mask", round=r, client_id=self.cid)
        return {"client_id": self.cid, "round": r, "stored": True}

    def _sync_if_needed(self, r: int, model_hash_r: str) -> Dict[str, Any]:
        old_hash = self.client.local_model_hash
        if not self.client.needs_model_sync(model_hash_r):
            return {
                "round": r,
                "client_id": self.cid,
                "old_local_model_hash": old_hash,
                "target_modelHash_r": model_hash_r,
                "sync_performed": False,
                "sigSyncCS_verified": False,
            }
        req_payload = self.client.make_model_sync_request(r, model_hash_r)
        resp = send_json(
            f"C{self.cid}",
            "CS",
            self.cs_url + "/model_sync",
            "ModelSyncReq",
            req_payload,
            r,
            client_id=self.cid,
            timeout=float(self.cfg.get("communication", {}).get("timeout_seconds", 300)),
            log_path=self.comm_log,
        )
        if str(resp.get("modelHash")) != str(model_hash_r):
            raise RuntimeError(
                f"client C{self.cid} rejected ModelSyncResp: target hash {resp.get('modelHash')} != requested {model_hash_r}"
            )
        model_bytes = model_bytes_from_payload(resp)
        sync_payload = {
            "r": resp["r"],
            "client_id": resp["client_id"],
            "modelHash": resp["modelHash"],
            "modelBytesLength": resp["modelBytesLength"],
            "modelBytesSha256": resp["modelBytesSha256"],
            "modelBytes": model_bytes,
            "sigSyncCS": resp["sigSyncCS"],
        }
        self.client.sync_model_from_server(sync_payload, model_template=self.model_template, cs_public_key=self.cs_public_key)
        self.events.append("model_sync", round=r, client_id=self.cid, old_hash=old_hash, target=model_hash_r)
        return {
            "round": r,
            "client_id": self.cid,
            "old_local_model_hash": old_hash,
            "target_modelHash_r": model_hash_r,
            "sync_performed": True,
            "sigSyncCS_verified": True,
        }

    def train_upload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = int(payload["r"])
        alpha = str(payload["alpha"])
        model_hash_r = str(payload["modelHash"])
        sync_event = self._sync_if_needed(r, model_hash_r)
        if r not in self.masks:
            raise RuntimeError(f"client C{self.cid} has no mask for round {r}")
        start_rows = len(self.timer.rows)
        up_msg, stat = self.client.make_upload(
            r,
            alpha,
            model_hash_r,
            self.client.local_model,
            self.train_loaders[self.cid],
            self.total_samples,
            self.cfg,
            self.masks[r],
            self.rhos[r],
            self.paillier_pub,
            self.ped_params,
            timer=self.timer,
            device=str(payload.get("device", "cpu")),
        )
        timeout = float(self.cfg.get("communication", {}).get("timeout_seconds", 300))
        send_json(f"C{self.cid}", "CS", self.cs_url + "/upload", "UpMsg", {"UpMsg": up_msg}, r, client_id=self.cid, timeout=timeout, log_path=self.comm_log)
        send_json(f"C{self.cid}", "KGC", self.kgc_url + "/upload_copy", "UpMsg", {"UpMsg": up_msg}, r, client_id=self.cid, timeout=timeout, log_path=self.comm_log)
        self.events.append("train_upload", round=r, client_id=self.cid, sync_performed=sync_event["sync_performed"])
        return {"client_id": self.cid, "stat": stat, "sync_event": sync_event, "runtime_rows": self.timer.rows[start_rows:]}

    def receive_agg(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        agg = payload["AggMsg"]
        sig = str(agg.pop("sigAgg"))
        signed_payload = {
            "r": agg["r"],
            "alpha": agg["alpha"],
            "rootUp": agg["rootUp"],
            "CaggHash": agg["CaggHash"],
            "ComAgg": agg["ComAgg"],
        }
        if self.cs_public_key and not verify_obj(self.cs_public_key, sig, signed_payload):
            raise RuntimeError("invalid CS signature on AggMsg")
        agg["sigAgg"] = sig
        share_msg = self.client.make_decryption_share(int(agg["r"]), str(agg["alpha"]), str(agg["CaggHash"]))
        timeout = float(self.cfg.get("communication", {}).get("timeout_seconds", 300))
        send_json(f"C{self.cid}", "CS", self.cs_url + "/share", "ShareMsg", share_msg, int(agg["r"]), client_id=self.cid, timeout=timeout, log_path=self.comm_log)
        self.events.append("receive_agg", round=int(agg["r"]), client_id=self.cid)
        return {"client_id": self.cid, "share_sent": True}

    def receive_model(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from fl_audit.crypto.signature import verify_obj

        model_msg = payload["model_payload"]
        if self.cs_public_key and not verify_obj(self.cs_public_key, str(payload["sigModel"]), model_msg):
            raise RuntimeError("invalid CS signature on ModelBroadcast")
        next_model = model_from_payload(self.model_template, payload)
        self.client.receive_model_broadcast(next_model, str(payload["modelHashNext"]))
        self.events.append("receive_model", round=int(payload["r"]), client_id=self.cid, modelHashNext=payload["modelHashNext"])
        return {"client_id": self.cid, "local_model_hash": self.client.local_model_hash}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--config", required=True)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    service = ClientService(args.cid, cfg, args.config)
    service.logger.info("Client C%s listening on %s:%s", args.cid, args.host, args.port)
    serve_json(args.host, args.port, {
        ("GET", "/health"): service.health,
        ("GET", "/state"): service.state,
        ("GET", "/public_key"): service.public_key,
        ("POST", "/public_key"): service.public_key,
        ("POST", "/init_model"): service.init_model,
        ("POST", "/receive_mask"): service.receive_mask,
        ("POST", "/round_start"): service.train_upload,
        ("POST", "/train_upload"): service.train_upload,
        ("POST", "/receive_agg"): service.receive_agg,
        ("POST", "/receive_model"): service.receive_model,
    })


if __name__ == "__main__":
    main()

