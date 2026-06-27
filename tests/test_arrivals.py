"""Tests for robust per-IR arrival handling (REW peak vs argmax fallback)."""

from __future__ import annotations

import numpy as np
import pytest

from optimimo.core.io import compute_measurement_arrivals
from optimimo.core.smoothing import _arrival_seconds


def test_compute_measurement_arrivals_reads_arrival_ms():
    config = {
        "num_speakers": 2,
        "num_mic_positions": 1,
        "measurements": [
            {"speaker": 0, "mic": 0, "path": "a.wav", "arrival_ms": 25.0},
            {"speaker": 1, "mic": 0, "path": "b.wav"},  # no arrival -> NaN
        ],
    }
    arrivals = compute_measurement_arrivals(config, 48000)
    assert arrivals.shape == (1, 2)
    assert arrivals[0, 0] == pytest.approx(0.025)
    assert np.isnan(arrivals[0, 1])


def test_compute_measurement_arrivals_applies_crop():
    config = {
        "num_speakers": 1,
        "num_mic_positions": 1,
        "ir_crop_start_ms": 5.0,
        "measurements": [{"speaker": 0, "mic": 0, "path": "a.wav", "arrival_ms": 25.0}],
    }
    arrivals = compute_measurement_arrivals(config, 48000)
    # 25 ms in the WAV minus the 5 ms crop the solver also applies.
    assert arrivals[0, 0] == pytest.approx(0.020)


def test_compute_measurement_arrivals_pattern_is_all_nan():
    config = {
        "num_speakers": 1,
        "num_mic_positions": 1,
        "measurement_pattern": "spk{speaker}_mic{mic}.wav",
    }
    arrivals = compute_measurement_arrivals(config, 48000)
    assert arrivals.shape == (1, 1)
    assert np.all(np.isnan(arrivals))


def test_arrival_seconds_prefers_supplied_else_argmax():
    fs = 48000
    room_irs = np.zeros((1, 2, 8))
    room_irs[0, 0, 5] = 1.0
    room_irs[0, 1, 2] = 1.0

    # No arrivals supplied -> argmax of each IR.
    assert _arrival_seconds(None, room_irs, 0, 0, fs) == pytest.approx(5 / fs)

    arrivals = np.array([[0.001, np.nan]])
    # Finite value wins over argmax...
    assert _arrival_seconds(arrivals, room_irs, 0, 0, fs) == pytest.approx(0.001)
    # ...NaN falls back to argmax.
    assert _arrival_seconds(arrivals, room_irs, 0, 1, fs) == pytest.approx(2 / fs)


def test_solve_consults_arrivals_from_config():
    """solve() derives arrivals from config['measurements'] and de-rotates with
    them; this exercises the full wiring with h smoothing enabled."""
    from optimimo import solve
    from optimimo.cli import synthetic_room_irs

    fs = 48000
    room = synthetic_room_irs(fs, 2, 2, length=1024)
    config = {
        "num_speakers": 2,
        "num_mic_positions": 2,
        "filter_taps": 1024,
        "target_delay_ms": 10.0,
        "h_smoothing_fraction": 6.0,
        "speaker_profiles": {
            "0": {"name": "L", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 10.0},
            "1": {"name": "R", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 10.0},
        },
        "measurements": [
            {"speaker": 0, "mic": 0, "path": "x", "arrival_ms": 1.0},
            {"speaker": 1, "mic": 0, "path": "x", "arrival_ms": 1.2},
            {"speaker": 0, "mic": 1, "path": "x", "arrival_ms": 1.1},
            {"speaker": 1, "mic": 1, "path": "x", "arrival_ms": 1.3},
        ],
    }
    result = solve(room, fs, config)
    assert result.firs.shape == (1024, 2, 2)
    assert np.all(np.isfinite(result.firs))
