from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from math import ceil
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fl_audit.distributed.state_store import ensure_runtime_dirs, load_config, logs_dir, output_dir


def wait_health(url: str, timeout: float = 60.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"worker did not become healthy: {url}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, required=True)
    ap.add_argument("--clients-per-worker", type=int, default=20)
    ap.add_argument("--base-port", type=int, default=9500)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--config", required=True)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    ensure_runtime_dirs(cfg)
    py = sys.executable
    workers = []
    count = ceil(args.clients / args.clients_per_worker)
    for wid in range(1, count + 1):
        cid_start = (wid - 1) * args.clients_per_worker + 1
        cid_end = min(args.clients, wid * args.clients_per_worker)
        port = args.base_port + wid
        log_path = logs_dir(cfg) / f"worker_{wid}.stdout.log"
        env = os.environ.copy()
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("OPENBLAS_NUM_THREADS", "1")
        env.setdefault("MKL_NUM_THREADS", "1")
        env.setdefault("NUMEXPR_NUM_THREADS", "1")
        env.setdefault("TORCH_NUM_THREADS", "1")
        with log_path.open("w", encoding="utf-8") as f:
            proc = subprocess.Popen(
                [
                    py,
                    "-B",
                    "scripts/run_client_worker.py",
                    "--worker-id",
                    str(wid),
                    "--cid-start",
                    str(cid_start),
                    "--cid-end",
                    str(cid_end),
                    "--host",
                    args.host,
                    "--port",
                    str(port),
                    "--config",
                    args.config,
                ],
                cwd=ROOT,
                stdout=f,
                stderr=subprocess.STDOUT,
                env=env,
            )
        workers.append({
            "worker_id": wid,
            "pid": proc.pid,
            "host": args.host,
            "port": port,
            "cid_start": cid_start,
            "cid_end": cid_end,
            "endpoint": f"http://{args.host}:{port}",
            "log": str(log_path),
        })
    for w in workers:
        wait_health(f"{w['endpoint']}/health")
    out_path = output_dir(cfg) / "client_workers.json"
    out_path.write_text(json.dumps({"workers": workers}, indent=2), encoding="utf-8")
    print(f"Started {len(workers)} client workers. Config saved to {out_path}")


if __name__ == "__main__":
    main()

