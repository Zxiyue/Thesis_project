from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fl_audit.distributed.distributed_logger import JsonlLogger, setup_text_logger
from fl_audit.distributed.endpoints import serve_json
from fl_audit.distributed.state_store import ensure_runtime_dirs, entity_logs_dir, load_config, logs_dir
from scripts.run_client_server import ClientService


class ClientWorkerService:
    def __init__(self, worker_id: int, cid_start: int, cid_end: int, cfg: Dict[str, Any], config_path: str):
        self.worker_id = int(worker_id)
        self.cid_start = int(cid_start)
        self.cid_end = int(cid_end)
        self.cfg = cfg
        ensure_runtime_dirs(cfg)
        self.logger = setup_text_logger(f"worker_{self.worker_id}", logs_dir(cfg))
        self.events = JsonlLogger(entity_logs_dir(cfg) / f"worker_{self.worker_id}_events.jsonl")
        self.clients = {
            cid: ClientService(cid, cfg, config_path)
            for cid in range(self.cid_start, self.cid_end + 1)
        }

    def _cid(self, payload: Dict[str, Any]) -> int:
        cid = int(payload.get("client_id", payload.get("cid", payload.get("i", 0))))
        if cid not in self.clients:
            raise RuntimeError(f"worker {self.worker_id} does not own client C{cid}")
        return cid

    def health(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {"role": "ClientWorker", "worker_id": self.worker_id, "clients": sorted(self.clients)}

    def state(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "clients": {
                cid: {
                    "local_model_hash": svc.client.local_model_hash,
                    "known_masks": sorted(svc.masks),
                }
                for cid, svc in self.clients.items()
            },
        }

    def public_key(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.clients[self._cid(payload)].public_key(payload)

    def init_model(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.clients[self._cid(payload)].init_model(payload)

    def receive_mask(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.clients[self._cid(payload)].receive_mask(payload)

    def train_upload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.clients[self._cid(payload)].train_upload(payload)

    def receive_agg(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.clients[self._cid(payload)].receive_agg(payload)

    def receive_model(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.clients[self._cid(payload)].receive_model(payload)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker-id", type=int, required=True)
    ap.add_argument("--cid-start", type=int, required=True)
    ap.add_argument("--cid-end", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--config", required=True)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    service = ClientWorkerService(args.worker_id, args.cid_start, args.cid_end, cfg, args.config)
    service.logger.info("ClientWorker %s listening on %s:%s for C%s-C%s", args.worker_id, args.host, args.port, args.cid_start, args.cid_end)
    serve_json(args.host, args.port, {
        ("GET", "/health"): service.health,
        ("GET", "/state"): service.state,
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

