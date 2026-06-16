from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fl_audit.distributed.state_store import load_config, output_dir


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--pids", default=None)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    if args.pids:
        pid_path = Path(args.pids)
    elif args.config:
        pid_path = output_dir(load_config(args.config)) / "pids.json"
    else:
        pid_path = ROOT / "outputs" / "mnist_iid_distributed" / "pids.json"
    if not pid_path.exists():
        worker_path = pid_path.parent / "client_workers.json"
        if not worker_path.exists():
            print(f"No pid file found: {pid_path}")
            return
        data = {"processes": json.loads(worker_path.read_text(encoding="utf-8")).get("workers", [])}
    else:
        data = json.loads(pid_path.read_text(encoding="utf-8"))
    for row in data.get("processes", []):
        pid = int(row["pid"])
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(pid, signal.SIGTERM)
            print(f"stopped {row.get('name', pid)} pid={pid}")
        except Exception as exc:
            print(f"failed to stop pid={pid}: {exc}")


if __name__ == "__main__":
    main()
