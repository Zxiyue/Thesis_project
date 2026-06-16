from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import sys
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fl_audit.distributed.http_client import PROCESSING_TYPES, classify_time
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


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in {None, ""}:
            return default
        return int(float(value))
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except Exception:
        return default


def _normalize_comm_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Backfill communication classification fields for old and new logs."""
    payload_type = str(row.get("payload_type", ""))
    raw_includes = row.get("includes_processing", "")
    includes_processing = raw_includes in {1, "1", True, "True", "true", "yes"} or payload_type in PROCESSING_TYPES
    includes_processing_int, inferred_category = classify_time(payload_type, includes_processing)
    out = dict(row)
    out["includes_processing"] = includes_processing_int
    out["time_category"] = str(out.get("time_category") or inferred_category)
    return out


def _summarize_communication(comm_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summary = defaultdict(lambda: {
        "count": 0,
        "total_bytes": 0,
        "total_actual_seconds": 0.0,
        "total_process_seconds": 0.0,
        "network_transfer_count": 0,
        "processing_request_count": 0,
    })
    for row in comm_rows:
        key = str(row.get("payload_type", ""))
        item = summary[key]
        item["count"] += 1
        item["total_bytes"] += _as_int(row.get("bytes", 0))
        item["total_actual_seconds"] += _as_float(row.get("actual_seconds", 0.0))
        item["total_process_seconds"] += _as_float(row.get("process_seconds", 0.0))
        if str(row.get("time_category", "")) == "network_transfer":
            item["network_transfer_count"] += 1
        else:
            item["processing_request_count"] += 1

    rows: List[Dict[str, Any]] = []
    for payload_type, values in sorted(summary.items()):
        rows.append({
            "payload_type": payload_type,
            **values,
            "total_MB": values["total_bytes"] / 1024 / 1024,
        })
    return rows


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    out_dir = output_dir(cfg)
    logs_dir = entity_logs_dir(cfg)

    comm_rows = [_normalize_comm_row(r) for r in read_many_jsonl(sorted(logs_dir.glob("*communication.jsonl")))]
    trace: Dict[str, Any] = {}
    trace_path = out_dir / "run_trace.json"
    if trace_path.exists():
        import json

        trace = json.loads(trace_path.read_text(encoding="utf-8"))

    # Compatibility: older logs did not record ModelSyncResp as a separate row
    # when it was embedded in the HTTP response body. Derive a lightweight row so
    # the communication table always exposes ModelSyncReq/ModelSyncResp pairs.
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
            comm_rows.append(_normalize_comm_row({
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
                "includes_processing": 0,
                "time_category": "network_transfer",
                "note": "derived from run_trace for backward compatibility",
            }))

    comm_rows = sorted(
        comm_rows,
        key=lambda r: (
            int(r.get("round", 0)),
            float(r.get("send_start_time", 0.0) or 0.0),
            str(r.get("link", "")),
            str(r.get("payload_type", "")),
        ),
    )
    network_rows = [r for r in comm_rows if str(r.get("time_category", "")) == "network_transfer"]
    processing_rows = [
        r for r in comm_rows
        if str(r.get("time_category", "")) in {"end_to_end_with_processing", "experiment_total"}
    ]
    summary_rows = _summarize_communication(comm_rows)

    save_csv(comm_rows, out_dir / "communication_cost.csv")
    save_csv(network_rows, out_dir / "communication_network_only.csv")
    save_csv(processing_rows, out_dir / "communication_processing_requests.csv")
    save_csv(summary_rows, out_dir / "communication_summary_by_type.csv")

    metrics_rows = read_csv_rows(out_dir / "metrics_round.csv")
    runtime_rows = read_csv_rows(out_dir / "runtime_cost.csv")
    crypto_rows = read_csv_rows(out_dir / "crypto_cost.csv")
    blockchain_rows = read_csv_rows(out_dir / "blockchain_cost.csv")
    audit_rows = read_csv_rows(out_dir / "audit_chain.csv")
    verify_rows = read_csv_rows(out_dir / "verify_result.csv")

    event_paths = sorted(logs_dir.glob("*_events.jsonl"))
    trace["entity_logs"] = [str(p) for p in event_paths]
    trace["communication_outputs"] = {
        "all": str(out_dir / "communication_cost.csv"),
        "network_only": str(out_dir / "communication_network_only.csv"),
        "processing_requests": str(out_dir / "communication_processing_requests.csv"),
        "summary_by_type": str(out_dir / "communication_summary_by_type.csv"),
    }
    save_json(trace, trace_path)

    save_excel(
        {
            "metrics_round": metrics_rows,
            "runtime_cost": runtime_rows,
            "communication_cost": comm_rows,
            "comm_network_only": network_rows,
            "comm_processing": processing_rows,
            "comm_summary_type": summary_rows,
            "crypto_cost": crypto_rows,
            "blockchain_cost": blockchain_rows,
            "audit_chain": audit_rows,
            "verify_result": verify_rows,
        },
        out_dir / "summary_tables.xlsx",
    )
    print(f"Collected outputs into {out_dir}")
    print("Communication outputs:")
    print(f"  all: {out_dir / 'communication_cost.csv'}")
    print(f"  network only: {out_dir / 'communication_network_only.csv'}")
    print(f"  processing requests: {out_dir / 'communication_processing_requests.csv'}")
    print(f"  summary by type: {out_dir / 'communication_summary_by_type.csv'}")


if __name__ == "__main__":
    main()
