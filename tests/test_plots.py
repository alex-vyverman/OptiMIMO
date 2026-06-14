"""Unit tests for the analysis data-preparation helpers."""

from __future__ import annotations

import numpy as np

from mimo_acoustic import solve
from mimo_acoustic.cli import synthetic_room_irs
from mimo_acoustic.gui import plots


def _small_result():
    sample_rate = 48000
    room = synthetic_room_irs(sample_rate, 2, 2, length=1024)
    config = {
        "num_speakers": 2,
        "num_mic_positions": 2,
        "filter_taps": 1024,
        "target_delay_ms": 10.0,
        "mic_weights": [1.0, 0.5],
        "speaker_profiles": {
            "0": {"name": "L", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 10.0},
            "1": {"name": "R", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 10.0},
        },
    }
    return solve(room, sample_rate, config)


def test_log_frequency_indices_monotonic():
    freqs = np.fft.rfftfreq(4096, d=1.0 / 48000.0)
    indices = plots.log_frequency_indices(freqs, points=200)
    assert indices.size > 50
    assert np.all(np.diff(indices) > 0)
    assert indices[-1] < freqs.size
    assert freqs[indices[0]] >= 10.0 or indices[0] == 1


def test_magnitude_db_floor():
    values = np.array([0.0, 1.0, 10.0])
    db = plots.magnitude_db(values)
    assert db[0] == plots.DISPLAY_FLOOR_DB
    assert abs(db[1]) < 1e-9
    assert abs(db[2] - 20.0) < 1e-9


def test_achieved_and_residual_table():
    result = _small_result()
    achieved = plots.achieved_response(result)
    assert achieved.shape == result.y_freq.shape
    rows = plots.residual_table(result, achieved=achieved)
    assert rows, "expected at least one band row"
    for row in rows:
        assert "band" in row
        assert "input_0" in row and "input_1" in row
        for key in ("input_0", "input_1"):
            value = str(row[key])
            assert value.endswith("dB")
            assert float(value.split()[0]) < 0.0


def test_residual_perfect_match_is_minus_inf_like():
    result = _small_result()
    rows = plots.residual_table(result, achieved=result.y_freq.copy())
    for row in rows:
        assert float(str(row["input_0"]).split()[0]) < -100.0


def test_impulse_envelope_and_predelay():
    result = _small_result()
    fir = result.firs[:, 0, 0]
    time_s, envelope = plots.impulse_envelope(fir, result.sample_rate, points=100)
    assert time_s.shape == envelope.shape
    assert time_s.size <= 110
    assert np.all(np.diff(time_s) > 0)

    ratio = plots.pre_delay_energy_ratio_db(fir, result.sample_rate, delay_s=0.010)
    assert ratio <= 0.0
    assert plots.pre_delay_energy_ratio_db(fir, result.sample_rate, delay_s=10.0) > -1e-6


def test_filter_activity_and_names():
    result = _small_result()
    assert plots.speaker_names(result) == ["L", "R"]
    assert plots.filter_is_active(result.firs[:, 0, 0])
    assert not plots.filter_is_active(np.zeros(16))


def test_phase_deg():
    values = np.array([1.0 + 0j, 0.0 + 1j, -1.0 + 0j, 0.0 - 1j])
    phase = plots.phase_deg(values)
    assert phase.shape == values.shape
    assert abs(phase[0]) < 1e-9
    assert abs(phase[1] - 90.0) < 1e-9
    assert abs(abs(phase[2]) - 180.0) < 1e-9


def test_group_delay_ms():
    freqs = np.linspace(0, 24000, 1025)
    delay_s = 0.010
    spectrum = np.exp(-2j * np.pi * freqs * delay_s)
    gd = plots.group_delay_ms(spectrum, freqs)
    assert gd.shape == freqs.shape
    assert abs(gd[len(gd) // 2] - delay_s * 1000.0) < 0.5
