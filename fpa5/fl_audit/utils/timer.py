from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import List
import threading


@dataclass
class TimerRecorder:
    rows: List[dict] = field(default_factory=list)

    def __post_init__(self):
        self._lock = threading.Lock()

    def add(self, round_id: int, stage: str, seconds: float, detail: str = "", cid: int | str = ""):
        with self._lock:
            self.rows.append({
                "round": round_id,
                "stage": stage,
                "detail": detail,
                "cid": cid,
                "seconds": float(seconds),
            })

    @contextmanager
    def record(self, round_id: int, stage: str, detail: str = "", cid: int | str = ""):
        start = time.perf_counter()
        try:
            yield
        finally:
            end = time.perf_counter()
            self.add(round_id, stage, end - start, detail=detail, cid=cid)
