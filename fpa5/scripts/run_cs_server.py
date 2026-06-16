from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import torch

from fl_audit.crypto import pedersen
from fl_audit.crypto.signature import sign_obj, verify_obj
from fl_audit.data import make_loaders
from fl_audit.distributed.distributed_logger import JsonlLogger, setup_text_logger
from fl_audit.distributed.endpoints import serve_json
from fl_audit.distributed.http_client import get_json, send_json
from fl_audit.distributed.message_codec import (
    final_tx_from_dict,
    init_tx_from_dict,
    model_from_payload,
    model_payload,
    pedersen_from_dict,
    public_key_to_dict,
    public_key_from_dict,
)
from fl_audit.distributed.state_store import client_url, client_endpoint_info, client_mode, cs_url, ensure_runtime_dirs, entity_logs_dir, kgc_url, load_config, logs_dir, output_dir
from fl_audit.encoding import dequantize
from fl_audit.model import evaluate, get_vector, make_model, model_hash, set_vector
from fl_audit.protocol.entities import ServerCS, model_sync_signed_payload
from fl_audit.protocol.verifier import verify_chain, verify_init
from fl_audit.utils.codec import bytes32_hex
from fl_audit.utils.export import save_csv, save_excel, save_json
from fl_audit.utils.timer import TimerRecorder


def set_seed(seed: int) -> None:
    import random

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


class CSService:
    def __init__(self, cfg: Dict[str, Any], config_path: str):
        self.cfg = cfg
        self.config_path = config_path
        ensure_runtime_dirs(cfg)
        self.logger = setup_text_logger("cs", logs_dir(cfg))
        self.events = JsonlLogger(entity_logs_dir(cfg) / "cs_events.jsonl")
        self.comm_log = str(entity_logs_dir(cfg) / "cs_communication.jsonl")
        set_seed(int(cfg.get("seed", 42)))
        self.cs = ServerCS()
        self.model = make_model(cfg["model"]["name"])
        self.model_template = make_model(cfg["model"]["name"])
        self.train_loaders, self.test_loader, _ = make_loaders(cfg)
        self.total_samples = sum(len(loader.dataset) for loader in self.train_loaders.values())
        self.timer = TimerRecorder()
        self.up_msgs_by_round: Dict[int, List[Dict[str, Any]]] = {}
        self.share_msgs_by_round: Dict[int, List[Dict[str, Any]]] = {}
        self.current_model_hash = ""
        self.init_tx = None
        self.final_txs = []
        self.paillier_pub = None
        self.ped_params = None
        self.share_field = 0
        self.U: List[int] = []
        self.Uroot = ""
        self.kgc_public_key = ""
        self.client_public_keys: Dict[int, str] = {}
        self.blockchain_rows: List[Dict[str, Any]] = []
        self.rows: Dict[str, List[Dict[str, Any]]] = {
            "metrics_round": [],
            "runtime_cost": self.timer.rows,
            "crypto_cost": [],
            "blockchain_cost": self.blockchain_rows,
            "audit_chain": [],
        }
        self.trace: Dict[str, Any] = {"rounds": [], "runtime": {"distributed": True, "client_mode": client_mode(cfg)}}

    def _device(self) -> str:
        device = str(self.cfg.get("runtime", {}).get("device", "cpu"))
        if device == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if device.startswith("cuda") and not torch.cuda.is_available():
            return "cpu"
        return device

    def _timeout(self) -> float:
        return float(self.cfg.get("communication", {}).get("timeout_seconds", 300))

    def health(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {"role": "CS", "ready": True, "public_key": self.cs.public_key}

    def state(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "current_model_hash": self.current_model_hash,
            "received_upload_rounds": sorted(self.up_msgs_by_round),
            "received_share_rounds": sorted(self.share_msgs_by_round),
        }

    def upload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        msg = payload["UpMsg"]
        self.up_msgs_by_round.setdefault(int(msg["r"]), []).append(msg)
        self.events.append("upload", round=int(msg["r"]), client_id=int(msg["i"]))
        return {"stored": True}

    def share(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        msg = payload["ShareMsg"]
        self.share_msgs_by_round.setdefault(int(msg["r"]), []).append(payload)
        self.events.append("share", round=int(msg["r"]), client_id=int(msg["j"]))
        return {"stored": True}

    def model_sync(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = int(payload["r"])
        cid = int(payload["client_id"])
        mp = model_payload(self.model)
        signed_payload = model_sync_signed_payload(r, cid, self.current_model_hash, __import__("base64").b64decode(mp["modelBytes"]))
        sig = sign_obj(self.cs.keypair.private_key, signed_payload)
        self.events.append("model_sync", round=r, client_id=cid, old_hash=payload.get("local_model_hash"), target=self.current_model_hash)
        return {
            "_response_payload_type": "ModelSyncResp",
            "_response_sender": "CS",
            "_response_receiver": f"C{cid}",
            "_response_client_id": cid,
            "r": r,
            "client_id": cid,
            "modelHash": self.current_model_hash,
            **mp,
            "sigSyncCS": sig,
        }

    def _client_endpoints(self) -> Dict[int, str]:
        return {cid: client_url(self.cfg, cid) for cid in range(1, int(self.cfg["federated"]["clients"]) + 1)}

    def _client_send(self, cid: int, path: str, payload_type: str, payload: Dict[str, Any], round_id: int, *, includes_processing: bool = False, note: str = "") -> Dict[str, Any]:
        url, worker_id = client_endpoint_info(self.cfg, cid, path)
        payload = dict(payload)
        payload.setdefault("client_id", cid)
        return send_json(
            "CS",
            f"C{cid}",
            url,
            payload_type,
            payload,
            round_id,
            client_id=cid,
            worker_id=worker_id,
            timeout=self._timeout(),
            log_path=self.comm_log,
            includes_processing=includes_processing,
            note=note,
        )

    def _setup_protocol(self) -> None:
        endpoints = self._client_endpoints()
        self.client_public_keys = {}
        for cid in endpoints:
            info = self._client_send(cid, "/public_key", "PublicKeyReq", {"client_id": cid}, 0)
            self.client_public_keys[cid] = str(info["public_key"])
        setup_resp = send_json(
            "CS",
            "KGC",
            kgc_url(self.cfg, "/setup"),
            "SetupReq",
            {"client_public_keys": self.client_public_keys, "client_endpoints": endpoints, "cs_public_key": self.cs.public_key},
            0,
            timeout=self._timeout(),
            log_path=self.comm_log,
        )
        self.kgc_public_key = str(setup_resp["kgc_public_key"])
        self.init_tx = init_tx_from_dict(setup_resp["initTx"])
        self.trace["initTx"] = setup_resp["initTx"]
        self.U = [int(x) for x in setup_resp["U"]]
        self.Uroot = str(setup_resp["Uroot"])
        self.current_model_hash = str(setup_resp["modelHash0"])
        self.paillier_pub = public_key_from_dict(setup_resp.get("paillier_public", setup_resp.get("paillier", {}).get("public", setup_resp.get("paillier", {}))))
        self.ped_params = pedersen_from_dict(setup_resp["pedersen"])
        self.share_field = int(setup_resp["shareField"])
        self.model = model_from_payload(self.model_template, setup_resp)
        self.blockchain_rows[:] = list(setup_resp.get("blockchain_rows", []))
        if not verify_init(self.init_tx, self.kgc_public_key, setup_resp["sysPara"]):
            raise RuntimeError("InitTx verification failed")
        for cid, base in endpoints.items():
            share = setup_resp["shares"][str(cid)] if str(cid) in setup_resp["shares"] else setup_resp["shares"][cid]
            init_payload = {
                "modelHash": self.current_model_hash,
                "share": share["share"],
                "share_field": share["share_field"],
                "paillier_public": public_key_to_dict(self.paillier_pub),
                "pedersen": setup_resp["pedersen"],
                "total_samples": self.total_samples,
                "cs_public_key": self.cs.public_key,
                "kgc_public_key": self.kgc_public_key,
                "cs_url": cs_url(self.cfg),
                "kgc_url": kgc_url(self.cfg),
                **{k: setup_resp[k] for k in ("modelBytes", "modelBytesLength", "modelBytesSha256")},
            }
            self._client_send(cid, "/init_model", "InitModel", init_payload, 0)
        loss0, acc0 = evaluate(self.model, self.test_loader, device=self._device())
        self.rows["metrics_round"].append({"round": 0, "test_loss": loss0, "test_accuracy": acc0, "effective_clients": 0, "dropout_clients": 0})

    def start_experiment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        exp_t0 = time.perf_counter()
        self.events.append("start_experiment")
        self.final_txs = []
        self.rows["metrics_round"] = []
        self.rows["crypto_cost"] = []
        self.rows["audit_chain"] = []
        self.timer.rows.clear()
        self.trace = {"rounds": [], "runtime": {"distributed": True, "client_mode": client_mode(self.cfg)}}
        self._setup_protocol()
        rounds = int(self.cfg["federated"]["rounds"])
        dim = int(get_vector(self.model).numel())
        dropout_cfg = self.cfg["federated"].get("dropout", {}) or {}
        device = self._device()
        for r in range(1, rounds + 1):
            self.logger.info("ROUND %s", r)
            round_trace: Dict[str, Any] = {"round": r}
            W_r_vec = get_vector(self.model).clone().double()
            round_init = send_json(
                "CS",
                "KGC",
                kgc_url(self.cfg, "/round_init"),
                "RoundInitReq",
                {"r": r, "modelHash": self.current_model_hash, "Uroot": self.Uroot, "client_endpoints": self._client_endpoints()},
                r,
                timeout=self._timeout(),
                log_path=self.comm_log,
            )
            alpha = str(round_init["alpha"])
            round_trace["alpha"] = alpha
            drop_ids = set(dropout_cfg.get(r, dropout_cfg.get(str(r), [])) or [])
            active = [cid for cid in self.U if cid not in drop_ids]
            self.up_msgs_by_round[r] = []
            self.share_msgs_by_round[r] = []
            model_sync_events = []
            client_stats = []
            runtime_rows = []
            max_workers = int(self.cfg.get("runtime", {}).get("max_client_workers", len(active) or 1))

            def _request_train(cid: int) -> Dict[str, Any]:
                return self._client_send(
                    cid,
                    "/train_upload",
                    "TrainUploadReq",
                    {"r": r, "alpha": alpha, "modelHash": self.current_model_hash, "device": device, "client_id": cid},
                    r,
                    includes_processing=True,
                    note="TrainUploadReq includes client-side sync check, local training, encryption, commitment, and UpMsg sends",
                )

            upload_t0 = time.perf_counter()
            if active:
                with ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futures = {ex.submit(_request_train, cid): cid for cid in active}
                    for fut in as_completed(futures):
                        resp = fut.result()
                        client_stats.append(resp["stat"])
                        model_sync_events.append(resp["sync_event"])
                        runtime_rows.extend(resp.get("runtime_rows", []))
            upload_wall = time.perf_counter() - upload_t0
            self.timer.add(
                r,
                "client_upload_parallel_wall",
                upload_wall,
                detail="CS waits for all active clients to finish TrainUploadReq in parallel",
            )
            self.timer.rows.extend(runtime_rows)
            verify_resp = send_json(
                "CS",
                "KGC",
                kgc_url(self.cfg, "/verify_uploads"),
                "VerifyUploadsReq",
                {"r": r},
                r,
                timeout=self._timeout(),
                log_path=self.comm_log,
                includes_processing=True,
                note="KGC verifies upload signatures and builds rootUp",
            )
            valid_msgs = verify_resp["valid_up_msgs"]
            root_up = str(verify_resp["rootUp"])
            U_r1 = sorted([int(m["i"]) for m in valid_msgs])
            D_r = sorted(list(set(self.U) - set(U_r1)))
            comp_resp = send_json(
                "CS",
                "KGC",
                kgc_url(self.cfg, "/compensation"),
                "CompensationReq",
                {"r": r},
                r,
                timeout=self._timeout(),
                log_path=self.comm_log,
                includes_processing=True,
                note="KGC computes dropout compensation; includes computation time",
            )
            cdrop = [int(x) for x in comp_resp["Cdrop"]]
            com_drop = int(comp_resp["ComDrop"])
            craw, cagg, com_raw, com_agg = self.cs.aggregate(valid_msgs, cdrop, com_drop, self.paillier_pub, self.ped_params)
            cagg_hash = bytes32_hex([str(c) for c in cagg])
            agg_payload = {"r": r, "alpha": alpha, "rootUp": root_up, "CaggHash": cagg_hash, "ComAgg": str(com_agg)}
            sig_agg = sign_obj(self.cs.keypair.private_key, agg_payload)
            agg_msg = {"r": r, "alpha": alpha, "rootUp": root_up, "Cagg": [str(c) for c in cagg], "CaggHash": cagg_hash, "ComAgg": str(com_agg), "sigAgg": sig_agg}
            for cid in U_r1:
                self._client_send(cid, "/receive_agg", "AggMsg", {"client_id": cid, "AggMsg": agg_msg}, r)
            share_msgs = sorted(self.share_msgs_by_round.get(r, []), key=lambda sm: int(sm["ShareMsg"]["j"]))
            xagg, used_shares = self.cs.recover_lambda_and_decrypt(
                share_msgs,
                self.client_public_keys,
                self.share_field,
                int(self.cfg["federated"]["threshold"]),
                self.paillier_pub,
                cagg,
                r,
                alpha,
            )
            com_check = pedersen.commit(self.ped_params, xagg.tolist(), 0)
            if com_check != com_agg:
                raise RuntimeError("CS open check failed: ComAgg != PedCom(xAgg;0)")
            update = torch.tensor(dequantize(xagg, int(self.cfg["encoding"]["scale"])), dtype=torch.float64)
            W_next_vec = W_r_vec + float(self.cfg["federated"].get("server_lr", 1.0)) * update
            set_vector(self.model, W_next_vec)
            model_hash_next = model_hash(self.model)
            model_msg_payload = {
                "r": r,
                "alpha": alpha,
                "modelHashR": self.current_model_hash,
                "modelHashNext": model_hash_next,
                "CaggHash": cagg_hash,
                "ComAgg": str(com_agg),
            }
            sig_model = sign_obj(self.cs.keypair.private_key, model_msg_payload)
            if not verify_obj(self.cs.public_key, sig_model, model_msg_payload):
                raise RuntimeError("sigModel failed")
            final_payload = {
                **model_msg_payload,
                **model_payload(self.model),
                "rootUp": root_up,
                "sigModel": sig_model,
            }
            final_resp = send_json(
                "CS",
                "KGC",
                kgc_url(self.cfg, "/final_confirm"),
                "ModelMsg",
                final_payload,
                r,
                timeout=self._timeout(),
                log_path=self.comm_log,
                includes_processing=True,
                note="KGC verifies final model, builds FinalTx, and may submit blockchain transaction",
            )
            final_tx = final_tx_from_dict(final_resp["FinalTx"])
            self.final_txs.append(final_tx)
            self.blockchain_rows[:] = list(final_resp.get("blockchain_rows", self.blockchain_rows))
            for cid in U_r1:
                broadcast_payload = {
                    "r": r,
                    "client_id": cid,
                    **model_msg_payload,
                    **model_payload(self.model),
                    "model_payload": model_msg_payload,
                    "sigModel": sig_model,
                }
                self._client_send(cid, "/receive_model", "ModelBroadcast", broadcast_payload, r)
            loss, acc = evaluate(self.model, self.test_loader, device=device)
            self.rows["metrics_round"].append({
                "round": r,
                "test_loss": loss,
                "test_accuracy": acc,
                "effective_clients": len(U_r1),
                "dropout_clients": len(D_r),
            })
            self.rows["audit_chain"].append({
                "round": r,
                "alpha": alpha,
                "rootUp": root_up,
                "ComAggHash": final_tx.ComAggHash,
                "modelHashR": final_tx.modelHashR,
                "modelHashNext": final_tx.modelHashNext,
                "auditRoot": final_tx.auditRoot,
                "sigFinal_len": len(final_tx.sigFinal),
            })
            compensation_encryptions = dim if len(D_r) > 0 else 0
            compensation_commitments = 1 if len(D_r) > 0 else 0
            self.rows["crypto_cost"].extend([
                {"round": r, "metric": "model_dimension", "value": dim},
                {"round": r, "metric": "model_name", "value": self.cfg["model"]["name"]},
                {"round": r, "metric": "effective_clients", "value": len(U_r1)},
                {"round": r, "metric": "dropout_clients", "value": len(D_r)},
                {"round": r, "metric": "pedersen_commitments", "value": len(U_r1) + compensation_commitments + 2},
                {"round": r, "metric": "paillier_encryptions", "value": len(U_r1) * dim + compensation_encryptions},
                {"round": r, "metric": "paillier_ciphertexts_aggregated", "value": len(U_r1) * dim},
                {"round": r, "metric": "compensation_encryptions", "value": compensation_encryptions},
                {"round": r, "metric": "compensation_identity_optimized", "value": 1 if len(D_r) == 0 else 0},
            ])
            round_trace.update({
                "U_r1": U_r1,
                "D_r": D_r,
                "rootUp": root_up,
                "ComAggHash": final_tx.ComAggHash,
                "modelHashNext": model_hash_next,
                "auditRoot": final_tx.auditRoot,
                "clientStats": sorted(client_stats, key=lambda s: int(s["cid"])),
                "modelSync": model_sync_events,
            })
            self.trace["rounds"].append(round_trace)
            self.current_model_hash = model_hash_next
            self.events.append("round_complete", round=r, acc=acc, effective=len(U_r1), dropout=D_r)
        chain_ok = verify_chain(self.init_tx, self.final_txs, self.kgc_public_key)
        self.timer.add(
            0,
            "start_experiment_total_wall",
            time.perf_counter() - exp_t0,
            detail="total wall-clock time of the CS-driven distributed experiment",
        )
        verify_rows = [{"accepted": chain_ok, "rounds": len(self.final_txs), "latestAuditRoot": self.final_txs[-1].auditRoot if self.final_txs else self.init_tx.auditRoot0}]
        self._save_outputs(verify_rows)
        return {"completed": True, "accepted": chain_ok, "output_dir": str(output_dir(self.cfg))}

    def _save_outputs(self, verify_rows: List[Dict[str, Any]]) -> None:
        out = output_dir(self.cfg)
        save_csv(self.rows["metrics_round"], out / "metrics_round.csv")
        save_csv(self.timer.rows, out / "runtime_cost.csv")
        save_csv(self.rows["crypto_cost"], out / "crypto_cost.csv")
        save_csv(self.blockchain_rows, out / "blockchain_cost.csv")
        save_csv(self.rows["audit_chain"], out / "audit_chain.csv")
        save_csv(verify_rows, out / "verify_result.csv")
        save_json(self.trace, out / "run_trace.json")
        save_excel({
            "metrics_round": self.rows["metrics_round"],
            "runtime_cost": self.timer.rows,
            "crypto_cost": self.rows["crypto_cost"],
            "blockchain_cost": self.blockchain_rows,
            "audit_chain": self.rows["audit_chain"],
            "verify_result": verify_rows,
        }, out / "summary_tables.xlsx")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9300)
    ap.add_argument("--config", required=True)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    service = CSService(cfg, args.config)
    service.logger.info("CS listening on %s:%s", args.host, args.port)
    serve_json(args.host, args.port, {
        ("GET", "/health"): service.health,
        ("GET", "/state"): service.state,
        ("POST", "/start_experiment"): service.start_experiment,
        ("POST", "/upload"): service.upload,
        ("POST", "/share"): service.share,
        ("POST", "/model_sync"): service.model_sync,
    })


if __name__ == "__main__":
    main()
