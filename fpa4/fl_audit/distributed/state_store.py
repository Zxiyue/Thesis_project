from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml


def load_config(path: str | Path) -> Dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def output_dir(cfg: Dict[str, Any]) -> Path:
    return Path(cfg.get("output_dir", "outputs/distributed"))


def logs_dir(cfg: Dict[str, Any]) -> Path:
    return output_dir(cfg) / "logs"


def entity_logs_dir(cfg: Dict[str, Any]) -> Path:
    return output_dir(cfg) / "entity_logs"


def ensure_runtime_dirs(cfg: Dict[str, Any]) -> None:
    output_dir(cfg).mkdir(parents=True, exist_ok=True)
    logs_dir(cfg).mkdir(parents=True, exist_ok=True)
    entity_logs_dir(cfg).mkdir(parents=True, exist_ok=True)


def endpoint_url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{int(port)}{path}"


def _dist_host(cfg: Dict[str, Any]) -> str:
    d = cfg.get("distributed", {}) or {}
    return str(os.environ.get("FPA4_HOST", d.get("host", "127.0.0.1")))


def _dist_port(cfg: Dict[str, Any], name: str, default: int) -> int:
    d = cfg.get("distributed", {}) or {}
    env_name = f"FPA4_{name.upper()}"
    return int(os.environ.get(env_name, d.get(name, default)))


def kgc_url(cfg: Dict[str, Any], path: str = "") -> str:
    return endpoint_url(_dist_host(cfg), _dist_port(cfg, "kgc_port", 9200), path)


def cs_url(cfg: Dict[str, Any], path: str = "") -> str:
    return endpoint_url(_dist_host(cfg), _dist_port(cfg, "cs_port", 9300), path)


def client_url(cfg: Dict[str, Any], cid: int, path: str = "") -> str:
    return endpoint_url(_dist_host(cfg), _dist_port(cfg, "client_base_port", 9400) + int(cid), path)


def client_urls(cfg: Dict[str, Any]) -> Dict[int, str]:
    n = int(cfg["federated"]["clients"])
    return {cid: client_url(cfg, cid) for cid in range(1, n + 1)}


def append_jsonl(path: str | Path, row: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_many_jsonl(paths: Iterable[str | Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in paths:
        rows.extend(read_jsonl(path))
    return rows
