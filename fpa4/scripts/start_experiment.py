from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fl_audit.distributed.http_client import send_json
from fl_audit.distributed.state_store import cs_url, entity_logs_dir, load_config


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    resp = send_json(
        "Launcher",
        "CS",
        cs_url(cfg, "/start_experiment"),
        "StartExperiment",
        {"config": args.config},
        0,
        timeout=float(cfg.get("communication", {}).get("timeout_seconds", 300)),
        log_path=str(entity_logs_dir(cfg) / "launcher_communication.jsonl"),
    )
    print(resp)


if __name__ == "__main__":
    main()

