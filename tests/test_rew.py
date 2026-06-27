"""Unit tests for the REW HTTP API import (optimimo.core.rew).

No live REW instance is required: the HTTP layer is mocked.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from optimimo.core import rew
from optimimo.core.io import load_wav_ir


class _FakeResponse:
    """Minimal stand-in for the object urllib.request.urlopen yields."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _mock_urlopen(monkeypatch, payload: bytes) -> None:
    def fake(request, timeout=None):  # noqa: ANN001
        return _FakeResponse(payload)

    monkeypatch.setattr(rew.urllib.request, "urlopen", fake)


# ---------------------------------------------------------------------------
# Pure helpers


def test_decode_ir_data_validation_vector():
    """REW's documented Base64 big-endian float32 sample (must byte-swap)."""
    samples = rew._decode_ir_data("PgAAAD6AAAA+wAAAPwAAAA==")
    np.testing.assert_allclose(samples, [0.125, 0.25, 0.375, 0.5])


def test_decode_ir_data_rejects_empty():
    with pytest.raises(rew.RewError):
        rew._decode_ir_data("")


def test_placement_plan_trims_lead_in_and_preserves_timing():
    fs = 48000
    pre_roll = round(0.020 * fs)  # 960 samples
    # REW returns each IR with its peak ~1 s into the data; arrivals 0 and 2 ms.
    peak_times = [0.000, 0.002]
    peak_indices = [fs, fs]
    plan = rew._placement_plan(peak_times, peak_indices, fs, pre_roll)

    # Earliest arrival: peak target = pre_roll, so ~1 s of lead-in is trimmed.
    assert plan[0] == pre_roll - fs  # negative => trim
    # 2 ms-later arrival shifts the target by exactly 2 ms; relative timing held.
    assert plan[1] - plan[0] == round(0.002 * fs)


# ---------------------------------------------------------------------------
# Client


def test_fetch_measurements_parses(monkeypatch):
    payload = json.dumps(
        {
            "1": {
                "uuid": "u1",
                "title": "Sub L",
                "sampleRate": 48000,
                "timeOfIRStartSeconds": -6.25e-5,
                "timeOfIRPeakSeconds": 0.0123,
            },
            "2": {"uuid": "u2", "title": "No IR", "sampleRate": 48000},
        }
    ).encode("utf-8")
    _mock_urlopen(monkeypatch, payload)

    result = rew.fetch_measurements("127.0.0.1", 4735)
    assert [m["uuid"] for m in result] == ["u1", "u2"]
    assert result[0]["title"] == "Sub L"
    assert result[0]["sample_rate"] == 48000
    assert result[0]["has_ir"] is True
    assert result[0]["peak_time"] == pytest.approx(0.0123)  # REW's robust IR peak
    assert result[0]["start_time"] == pytest.approx(-6.25e-5)
    assert result[1]["has_ir"] is False  # no timing fields -> no IR
    assert result[1]["peak_time"] is None


def test_fetch_impulse_response_parses(monkeypatch):
    payload = json.dumps(
        {"startTime": -6.25e-5, "sampleRate": 48000, "data": "PgAAAD6AAAA+wAAAPwAAAA=="}
    ).encode("utf-8")
    _mock_urlopen(monkeypatch, payload)

    samples, fs, start = rew.fetch_impulse_response("127.0.0.1", 4735, "u1")
    assert fs == 48000
    assert start == pytest.approx(-6.25e-5)
    np.testing.assert_allclose(samples, [0.125, 0.25, 0.375, 0.5])


def test_connection_refused_raises_rewerror(monkeypatch):
    def boom(request, timeout=None):  # noqa: ANN001
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(rew.urllib.request, "urlopen", boom)
    with pytest.raises(rew.RewError):
        rew.fetch_measurements("127.0.0.1", 4735)


def test_non_json_response_raises_rewerror(monkeypatch):
    _mock_urlopen(monkeypatch, b"<html>not json</html>")
    with pytest.raises(rew.RewError):
        rew.fetch_measurements("127.0.0.1", 4735)


# ---------------------------------------------------------------------------
# Timing-aware WAV writer


def _fake_fetch(table):
    def fetch(host, port, meas_id, *, normalised=False, timeout=30.0):  # noqa: ANN001
        return table[meas_id]

    return fetch


def test_download_preserves_relative_timing(monkeypatch, tmp_path):
    fs = 48000
    delta = np.array([1.0, 0.0, 0.0, 0.0])
    table = {
        "u0": (delta.copy(), fs, 0.0),
        "u1": (delta.copy(), fs, 0.001),  # arrives 1 ms later
    }
    monkeypatch.setattr(rew, "fetch_impulse_response", _fake_fetch(table))

    assignments = [
        {"mic": 0, "speaker": 0, "meas_id": "u0", "title": "a", "sample_rate": fs},
        {"mic": 0, "speaker": 1, "meas_id": "u1", "title": "b", "sample_rate": fs},
    ]
    written = rew.download_measurements_to_wavs("h", 1, assignments, tmp_path)

    assert {Path(w["path"]).name for w in written} == {
        "spk00_mic00.wav",
        "spk01_mic00.wav",
    }

    by_speaker = {w["speaker"]: w["path"] for w in written}
    _, s0 = load_wav_ir(Path(by_speaker[0]))
    _, s1 = load_wav_ir(Path(by_speaker[1]))
    peak0 = int(np.argmax(np.abs(s0)))
    peak1 = int(np.argmax(np.abs(s1)))
    # The 1 ms inter-measurement delay survives the round-trip.
    assert peak1 - peak0 == round(0.001 * fs)


def test_download_rejects_sample_rate_mismatch(monkeypatch, tmp_path):
    delta = np.array([1.0, 0.0, 0.0, 0.0])
    table = {
        "u0": (delta.copy(), 48000, 0.0),
        "u1": (delta.copy(), 96000, 0.0),
    }
    monkeypatch.setattr(rew, "fetch_impulse_response", _fake_fetch(table))

    assignments = [
        {"mic": 0, "speaker": 0, "meas_id": "u0", "title": "a", "sample_rate": 48000},
        {"mic": 0, "speaker": 1, "meas_id": "u1", "title": "b", "sample_rate": 96000},
    ]
    with pytest.raises(rew.RewError):
        rew.download_measurements_to_wavs("h", 1, assignments, tmp_path)


def test_download_trims_rew_lead_in(monkeypatch, tmp_path):
    """REW returns ~1 s of pre-peak lead-in; the import must trim it while
    preserving the relative time-of-flight (regression for all-peaks-at-1000 ms)."""
    fs = 48000
    length = fs + 200

    def make():
        s = np.zeros(length)
        s[fs] = 1.0  # the IR peak sits 1.0 s into the returned data
        return s

    # start_time = peak_time - 1.0 s (REW's lead-in); arrivals 0 and 2 ms.
    table = {"u0": (make(), fs, -1.0), "u1": (make(), fs, -0.998)}
    monkeypatch.setattr(rew, "fetch_impulse_response", _fake_fetch(table))

    assignments = [
        {"mic": 0, "speaker": 0, "meas_id": "u0", "title": "a", "sample_rate": fs, "peak_time": 0.0},
        {"mic": 0, "speaker": 1, "meas_id": "u1", "title": "b", "sample_rate": fs, "peak_time": 0.002},
    ]
    written = rew.download_measurements_to_wavs(
        "h", 1, assignments, tmp_path, pre_roll_ms=20.0
    )
    by_speaker = {w["speaker"]: w for w in written}
    _, s0 = load_wav_ir(Path(by_speaker[0]["path"]))
    _, s1 = load_wav_ir(Path(by_speaker[1]["path"]))
    p0 = int(np.argmax(np.abs(s0)))
    p1 = int(np.argmax(np.abs(s1)))

    # Lead-in trimmed: earliest peak near the 20 ms pre-roll, NOT at 1 s.
    assert p0 == round(0.020 * fs)
    # The 2 ms inter-speaker time-of-flight survives.
    assert p1 - p0 == round(0.002 * fs)
    # Recorded arrival_ms points at the WAV peak.
    assert round(by_speaker[0]["arrival_ms"] * fs / 1000.0) == p0


def test_download_records_rew_peak_arrival(monkeypatch, tmp_path):
    fs = 48000
    samples = np.zeros(8)
    samples[3] = 1.0  # the IR's own peak is sample 3 of the returned data
    start_time = 0.001
    peak_time = start_time + 3 / fs  # REW reports the peak consistent with data
    monkeypatch.setattr(rew, "fetch_impulse_response", _fake_fetch({"u0": (samples, fs, start_time)}))

    assignments = [
        {
            "mic": 0,
            "speaker": 0,
            "meas_id": "u0",
            "title": "a",
            "sample_rate": fs,
            "peak_time": peak_time,
        }
    ]
    written = rew.download_measurements_to_wavs("h", 1, assignments, tmp_path)
    entry = written[0]
    assert "arrival_ms" in entry

    # The recorded arrival must point exactly at the IR peak in the written WAV.
    _, data = load_wav_ir(Path(entry["path"]))
    assert round(entry["arrival_ms"] * fs / 1000.0) == int(np.argmax(np.abs(data)))
