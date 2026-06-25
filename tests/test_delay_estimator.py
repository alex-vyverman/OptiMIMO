"""Tests for target_delay_ms estimation from synthetic IRs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile

from optimimo.core.delay_estimator import suggest_target_delay_ms


SAMPLE_RATE = 48000


def _write_ir(path: Path, impulse: np.ndarray) -> None:
    wavfile.write(path, SAMPLE_RATE, impulse.astype(np.float32))


def _delta_ir(length: int, delay_samples: int, amp: float = 1.0) -> np.ndarray:
    ir = np.zeros(length, dtype=np.float32)
    if 0 <= delay_samples < length:
        ir[delay_samples] = amp
    return ir


def _base_config(measurements: list[dict], **extra) -> dict:
    cfg = {
        "num_speakers": 1,
        "num_inputs": 1,
        "num_mic_positions": 1,
        "sample_rate": SAMPLE_RATE,
        "ir_length_samples": 16384,
        "filter_taps": 8192,
        "fft_size": 65536,
        "target_delay_ms": 5.0,
        "target_mode": "flat",
        "speaker_profiles": {
            "0": {"name": "Spk", "min_hz": 20.0, "max_hz": 20000.0},
        },
        "measurements": measurements,
    }
    cfg.update(extra)
    return cfg


def test_pure_delay_recovers_known_value(tmp_path):
    """A pure-delay IR has constant group delay equal to the delay itself."""
    delay_samples = 480  # 10 ms at 48 kHz
    ir_path = tmp_path / "spk0_mic0.wav"
    _write_ir(ir_path, _delta_ir(8192, delay_samples))

    cfg = _base_config([{"speaker": 0, "mic": 0, "path": str(ir_path)}])
    result = suggest_target_delay_ms(cfg, tmp_path)

    # 10 ms ground truth, within 1 sample of resolution
    assert abs(result["max_group_delay_ms"] - 10.0) < 0.05
    # Default flat margin is 20 ms, so recommendation is ~30 ms
    assert abs(result["recommended_ms"] - 30.0) < 0.1
    assert result["target_mode"] == "flat"


def test_worst_case_across_measurements_wins(tmp_path):
    """When multiple IRs have different delays, the estimator returns the max."""
    short = tmp_path / "spk0_mic0.wav"
    long = tmp_path / "spk0_mic1.wav"
    _write_ir(short, _delta_ir(8192, 240))   # 5 ms
    _write_ir(long, _delta_ir(8192, 960))    # 20 ms

    cfg = _base_config(
        [
            {"speaker": 0, "mic": 0, "path": str(short)},
            {"speaker": 0, "mic": 1, "path": str(long)},
        ],
        num_mic_positions=2,
    )
    result = suggest_target_delay_ms(cfg, tmp_path)

    assert abs(result["max_group_delay_ms"] - 20.0) < 0.05


def test_anchored_mode_uses_smaller_margin(tmp_path):
    """Anchored mode applies a smaller margin since target phase is causal."""
    ir_path = tmp_path / "spk0_mic0.wav"
    _write_ir(ir_path, _delta_ir(8192, 480))  # 10 ms

    cfg = _base_config(
        [{"speaker": 0, "mic": 0, "path": str(ir_path)}],
        target_mode="anchored",
    )
    result = suggest_target_delay_ms(cfg, tmp_path)

    assert result["target_mode"] == "anchored"
    assert result["margin_ms"] == 10.0
    # 10 ms gd + 10 ms anchored margin ≈ 20 ms
    assert abs(result["recommended_ms"] - 20.0) < 0.1


def test_band_restriction_isolates_per_speaker(tmp_path):
    """A sub-band speaker should only see the group delay inside its band.

    Sub speaker (20-200 Hz) measures an IR that has 5 ms delay overall;
    a main speaker (200-20000 Hz) measures a different IR with 30 ms
    delay. The estimator's worst case must reflect the main's 30 ms,
    not get pulled down by the sub's band-restricted view.
    """
    sub_path = tmp_path / "spk0_mic0.wav"
    main_path = tmp_path / "spk1_mic0.wav"
    _write_ir(sub_path, _delta_ir(8192, 240))    # 5 ms
    _write_ir(main_path, _delta_ir(8192, 1440))  # 30 ms

    cfg = _base_config(
        [
            {"speaker": 0, "mic": 0, "path": str(sub_path)},
            {"speaker": 1, "mic": 0, "path": str(main_path)},
        ],
        num_speakers=2,
        speaker_profiles={
            "0": {"name": "Sub", "min_hz": 20.0, "max_hz": 200.0},
            "1": {"name": "Main", "min_hz": 200.0, "max_hz": 20000.0},
        },
    )
    result = suggest_target_delay_ms(cfg, tmp_path)

    assert abs(result["max_group_delay_ms"] - 30.0) < 0.1


def test_fft_budget_constraint_is_flagged(tmp_path):
    """When recommended_ms exceeds the fft_size headroom, constrained_by_fft fires."""
    ir_path = tmp_path / "spk0_mic0.wav"
    _write_ir(ir_path, _delta_ir(8192, 4800))  # 100 ms

    # fft_size = filter_taps + tiny headroom → 100 ms recommendation won't fit
    cfg = _base_config(
        [{"speaker": 0, "mic": 0, "path": str(ir_path)}],
        fft_size=8192 + 128,
    )
    result = suggest_target_delay_ms(cfg, tmp_path)

    assert result["constrained_by_fft"] is True
    assert result["max_delay_budget_ms"] is not None
    assert any("exceeds" in issue for issue in result["issues"])


def test_per_measurement_breakdown_includes_all_pairs(tmp_path):
    ir1 = tmp_path / "spk0_mic0.wav"
    ir2 = tmp_path / "spk0_mic1.wav"
    _write_ir(ir1, _delta_ir(8192, 240))
    _write_ir(ir2, _delta_ir(8192, 480))

    cfg = _base_config(
        [
            {"speaker": 0, "mic": 0, "path": str(ir1)},
            {"speaker": 0, "mic": 1, "path": str(ir2)},
        ],
        num_mic_positions=2,
    )
    result = suggest_target_delay_ms(cfg, tmp_path)

    by_mic = {(p["speaker"], p["mic"]): p["max_group_delay_ms"] for p in result["per_measurement"]}
    assert (0, 0) in by_mic and (0, 1) in by_mic
    assert abs(by_mic[(0, 0)] - 5.0) < 0.05
    assert abs(by_mic[(0, 1)] - 10.0) < 0.05
