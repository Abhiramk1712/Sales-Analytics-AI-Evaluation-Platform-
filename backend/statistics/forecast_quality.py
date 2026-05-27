from __future__ import annotations

import math
from typing import Sequence


def _pairs(actual: Sequence[float], predicted: Sequence[float]) -> list[tuple[float, float]]:
    size = min(len(actual), len(predicted))
    return [(float(actual[i]), float(predicted[i])) for i in range(size)]


def mae(actual: Sequence[float], predicted: Sequence[float]) -> float:
    pairs = _pairs(actual, predicted)
    if not pairs:
        return 0.0
    return sum(abs(a - p) for a, p in pairs) / len(pairs)


def rmse(actual: Sequence[float], predicted: Sequence[float]) -> float:
    pairs = _pairs(actual, predicted)
    if not pairs:
        return 0.0
    return math.sqrt(sum((a - p) ** 2 for a, p in pairs) / len(pairs))


def mape(actual: Sequence[float], predicted: Sequence[float]) -> float:
    pairs = [(a, p) for a, p in _pairs(actual, predicted) if a != 0]
    if not pairs:
        return 0.0
    return sum(abs((a - p) / a) for a, p in pairs) * 100 / len(pairs)


def bias(actual: Sequence[float], predicted: Sequence[float]) -> float:
    pairs = _pairs(actual, predicted)
    if not pairs:
        return 0.0
    return sum(p - a for a, p in pairs) / len(pairs)
