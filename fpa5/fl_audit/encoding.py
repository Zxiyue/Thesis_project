from __future__ import annotations

import numpy as np
from typing import List


def quantize(vec: np.ndarray, scale: int) -> np.ndarray:
    return np.rint(vec.astype(np.float64) * scale).astype(np.int64)


def dequantize(x: np.ndarray, scale: int) -> np.ndarray:
    return x.astype(np.float64) / float(scale)


def center_mod(x: np.ndarray, q: int) -> np.ndarray:
    y = np.mod(x, q).astype(np.int64)
    y = np.where(y > q // 2, y - q, y)
    return y.astype(np.int64)


def to_list(x: np.ndarray) -> List[int]:
    return [int(v) for v in x.reshape(-1)]
