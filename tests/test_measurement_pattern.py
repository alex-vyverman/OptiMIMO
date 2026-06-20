"""Tests for measurement_pattern name placeholders and mic_names handling."""

from __future__ import annotations

import pytest

from optimimo.core.io import measurement_path_grid
from optimimo.gui.state import AppState


def test_pattern_name_placeholders(tmp_path):
    config = {
        "num_speakers": 2,
        "num_mic_positions": 2,
        "measurement_pattern": "{speaker_name}__{mic_name}.wav",
        "speaker_profiles": {"0": {"name": "Sub L"}, "1": {"name": "Main R"}},
        "mic_names": ["MLP", "Couch"],
    }
    grid = measurement_path_grid(config, tmp_path)  # grid[mic][speaker]
    assert grid[0][0] == (tmp_path / "Sub L__MLP.wav").resolve()
    assert grid[0][1] == (tmp_path / "Main R__MLP.wav").resolve()
    assert grid[1][0] == (tmp_path / "Sub L__Couch.wav").resolve()
    assert grid[1][1] == (tmp_path / "Main R__Couch.wav").resolve()


def test_pattern_mic_name_fallback_when_empty(tmp_path):
    config = {
        "num_speakers": 1,
        "num_mic_positions": 2,
        "measurement_pattern": "{speaker_name}_{mic_name}.wav",
        "speaker_profiles": {"0": {"name": "S0"}},
        "mic_names": ["", "Seat"],  # empty first entry -> mic0
    }
    grid = measurement_path_grid(config, tmp_path)
    assert grid[0][0] == (tmp_path / "S0_mic0.wav").resolve()
    assert grid[1][0] == (tmp_path / "S0_Seat.wav").resolve()


def test_pattern_speaker_name_fallback(tmp_path):
    config = {
        "num_speakers": 1,
        "num_mic_positions": 1,
        "measurement_pattern": "{speaker_name}.wav",
        "speaker_profiles": {},  # no profile -> speaker0
    }
    grid = measurement_path_grid(config, tmp_path)
    assert grid[0][0] == (tmp_path / "speaker0.wav").resolve()


def test_pattern_unknown_placeholder_raises(tmp_path):
    config = {
        "num_speakers": 1,
        "num_mic_positions": 1,
        "measurement_pattern": "{bogus}.wav",
        "speaker_profiles": {"0": {"name": "S0"}},
    }
    with pytest.raises(ValueError):
        measurement_path_grid(config, tmp_path)


def test_normalize_config_sizes_mic_names():
    state = AppState()
    state.config = {
        "num_speakers": 1,
        "num_mic_positions": 3,
        "speaker_profiles": {},
        "mic_names": ["MLP"],  # short -> padded with empty entries
    }
    state.normalize_config()
    assert state.config["mic_names"] == ["MLP", "", ""]
    assert len(state.config["mic_weights"]) == 3
    # mic_name() helper falls back to a default label for empty entries.
    assert state.mic_name(0) == "MLP"
    assert state.mic_name(1) == "Mic 1"
