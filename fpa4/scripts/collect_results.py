from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fl_audit.distributed.state_store import entity_logs_dir, load_config, output_dir, read_many_jsonl
from fl_audit.utils.export import save_csv, save_excel, save_json


def read_csv_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    import pandas as pd

    return pd.read_csv(path).to_dict(orient="records")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    out_dir = output_dir(cfg)
    logs_dir = entity_logs_dir(cfg)

    comm_rows = read_many_jsonl(sorted(logs_dir.glob("*communication.jsonl")))
    trace = {}
    trace_path = out_dir / "run_trace.json"
    if trace_path.exists():
        import json

        trace = json.loads(trace_path.read_text(encoding="utf-8"))
    existing = {
        (int(row.get("round", 0)), str(row.get("link", "")), str(row.get("payload_type", "")))
        for row in comm_rows
    }
    req_by_sync = {
        (int(row.get("round", 0)), str(row.get("client_id", ""))): row
        for row in comm_rows
        if row.get("payload_type") == "ModelSyncReq"
    }
    for round_row in trace.get("rounds", []):
        for sync in round_row.get("modelSync", []):
            if not sync.get("sync_performed"):
                continue
            r = int(sync["round"])
            cid = str(sync["client_id"])
            key = (r, f"CS->C{cid}", "ModelSyncResp")
            if key in existing:
                continue
            req = req_by_sync.get((r, cid), {})
            comm_rows.append({
                "round": r,
                "sender": "CS",
                "receiver": f"C{cid}",
                "link": f"CS->C{cid}",
                "payload_type": "ModelSyncResp",
                "messages": 1,
                "client_id": cid,
                "worker_id": req.get("worker_id", ""),
                "bytes": "",
                "actual_seconds": req.get("actual_seconds", ""),
                "status": "http_response_derived",
                "ack_sha256": "",
                "receiver_host": "",
                "receiver_port": "",
                "send_start_time": req.get("send_start_time", ""),
                "send_end_time": req.get("send_end_time", ""),
                "server_receive_time": req.get("server_receive_time", ""),
                "server_response_time": req.get("server_response_time", ""),
                "process_seconds": req.get("process_seconds", ""),
                "error": "",
            })
    comm_rows = sorted(
        comm_rows,
        key=lambda r: (
            int(r.get("round", 0)),
            float(r.get("send_start_time", 0.0) or 0.0),
            str(r.get("link", "")),
            str(r.get("payload_type", "")),
        ),
    )
    if comm_rows:
        save_csv(comm_rows, out_dir / "communication_cost.csv")

    metrics_rows = read_csv_rows(out_dir / "metrics_round.csv")
    runtime_rows = read_csv_rows(out_dir / "runtime_cost.csv")
    crypto_rows = read_csv_rows(out_dir / "crypto_cost.csv")
    blockchain_rows = read_csv_rows(out_dir / "blockchain_cost.csv")
    audit_rows = read_csv_rows(out_dir / "audit_chain.csv")
    verify_rows = read_csv_rows(out_dir / "verify_result.csv")

    event_paths = sorted(logs_dir.glob("*_events.jsonl"))
    trace["entity_logs"] = [str(p) for p in event_paths]
    save_json(trace, trace_path)

    save_excel(
        {
            "metrics_round": metrics_rows,
            "runtime_cost": runtime_rows,
            "communication_cost": comm_rows,
            "crypto_cost": crypto_rows,
            "blockchain_cost": blockchain_rows,
            "audit_chain": audit_rows,
            "verify_result": verify_rows,
        },
        out_dir / "summary_tables.xlsx",
    )
    print(f"Collected outputs into {out_dir}")


if __name__ == "__main__":
    main()
