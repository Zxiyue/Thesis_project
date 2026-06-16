from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fl_audit.distributed.state_store import client_mode, ensure_runtime_dirs, entity_logs_dir, load_config, logs_dir, output_dir


def wait_health(url: str, timeout: float = 60.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"service did not become healthy: {url}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--clients", type=int, default=None)
    ap.add_argument("--host", default=None)
    ap.add_argument("--kgc-port", type=int, default=None)
    ap.add_argument("--cs-port", type=int, default=None)
    ap.add_argument("--client-base-port", type=int, default=None)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    d = cfg.setdefault("distributed", {})
    host = args.host or str(d.get("host", "127.0.0.1"))
    kgc_port = int(args.kgc_port or d.get("kgc_port", 9200))
    cs_port = int(args.cs_port or d.get("cs_port", 9300))
    base_port = int(args.client_base_port or d.get("client_base_port", 9400))
    worker_base_port = int(d.get("worker_base_port", 9500))
    clients_per_worker = int(d.get("clients_per_worker", 20))
    clients = int(args.clients or cfg["federated"]["clients"])
    ensure_runtime_dirs(cfg)
    for path in [
        out for out in [
            output_dir(cfg) / "metrics_round.csv",
            output_dir(cfg) / "runtime_cost.csv",
            output_dir(cfg) / "communication_cost.csv",
            output_dir(cfg) / "crypto_cost.csv",
            output_dir(cfg) / "blockchain_cost.csv",
            output_dir(cfg) / "audit_chain.csv",
            output_dir(cfg) / "verify_result.csv",
            output_dir(cfg) / "summary_tables.xlsx",
            output_dir(cfg) / "run_trace.json",
            output_dir(cfg) / "pids.json",
        ]
    ]:
        if path.exists():
            path.write_text("", encoding="utf-8")
    for path in list(logs_dir(cfg).glob("*.log")) + list(entity_logs_dir(cfg).glob("*.jsonl")):
        path.write_text("", encoding="utf-8")
    pids = []

    def start(name: str, cmd: list[str]):
        log_path = logs_dir(cfg) / f"{name}.stdout.log"
        f = log_path.open("w", encoding="utf-8")
        env = os.environ.copy()
        env["FPA4_HOST"] = host
        env["FPA4_KGC_PORT"] = str(kgc_port)
        env["FPA4_CS_PORT"] = str(cs_port)
        env["FPA4_CLIENT_BASE_PORT"] = str(base_port)
        env["FPA4_WORKER_BASE_PORT"] = str(worker_base_port)
        env.setdefault("OMP_NUM_THREADS", "1")
        env.setdefault("OPENBLAS_NUM_THREADS", "1")
        env.setdefault("MKL_NUM_THREADS", "1")
        env.setdefault("NUMEXPR_NUM_THREADS", "1")
        env.setdefault("TORCH_NUM_THREADS", "1")
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
        pids.append({"name": name, "pid": proc.pid, "cmd": cmd, "log": str(log_path)})
        return proc

    py = sys.executable
    start("kgc", [py, "-B", "scripts/run_kgc_server.py", "--host", host, "--port", str(kgc_port), "--config", args.config])
    start("cs", [py, "-B", "scripts/run_cs_server.py", "--host", host, "--port", str(cs_port), "--config", args.config])
    mode = client_mode(cfg)
    if mode == "worker_pool":
        from math import ceil
        worker_count = ceil(clients / clients_per_worker)
        for wid in range(1, worker_count + 1):
            cid_start = (wid - 1) * clients_per_worker + 1
            cid_end = min(clients, wid * clients_per_worker)
            port = worker_base_port + wid
            start(
                f"worker_{wid}",
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
                    host,
                    "--port",
                    str(port),
                    "--config",
                    args.config,
                ],
            )
    else:
        for cid in range(1, clients + 1):
            start(f"client_{cid}", [py, "-B", "scripts/run_client_server.py", "--cid", str(cid), "--host", host, "--port", str(base_port + cid), "--config", args.config])

    wait_health(f"http://{host}:{kgc_port}/health")
    wait_health(f"http://{host}:{cs_port}/health")
    if mode == "worker_pool":
        from math import ceil
        for wid in range(1, ceil(clients / clients_per_worker) + 1):
            wait_health(f"http://{host}:{worker_base_port + wid}/health")
    else:
        for cid in range(1, clients + 1):
            wait_health(f"http://{host}:{base_port + cid}/health")

    pid_path = output_dir(cfg) / "pids.json"
    pid_path.write_text(json.dumps({"processes": pids}, indent=2), encoding="utf-8")
    print(f"Started {len(pids)} entities. PIDs saved to {pid_path}")


if __name__ == "__main__":
    main()
