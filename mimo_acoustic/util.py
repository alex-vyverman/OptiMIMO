"""Shared numeric utilities and dependency guard for mimo_acoustic."""

from __future__ import annotations

import math

try:
    import numpy as np  # noqa: F401  (re-exported import check)
    from scipy import signal  # noqa: F401
    from scipy.io import wavfile  # noqa: F401
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only on missing runtime deps
    missing = exc.name or "numpy/scipy"
    raise SystemExit(
        f"Missing Python dependency '{missing}'. Install dependencies with: "
        "python3 -m pip install -r requirements.txt"
    ) from exc


EPS = 1.0e-15


def db_to_amplitude(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def db_to_power(db: float) -> float:
    return float(10.0 ** (db / 10.0))


def amplitude_to_db(value: float) -> float:
    value = max(float(value), EPS)
    return float(20.0 * math.log10(value))


def next_power_of_two(value: int) -> int:
    if value <= 1:
        return 1
    return 1 << (int(value) - 1).bit_length()
