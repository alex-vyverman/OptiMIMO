"""Pure data-preparation helpers for the analysis plots.

All functions work on a `SolveResult` and plain numpy arrays so they can be
unit-tested without a browser. Plot traces are decimated onto a log-frequency
grid (frequency domain) or max-pooled (time domain) to keep the browser
responsive with 100k+ FFT bins.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ..core.pipeline import SolveResult
from ..util import EPS

DISPLAY_FLOOR_DB = -120.0


def log_frequency_indices(freqs: np.ndarray, f_min: float = 10.0, points: int = 400) -> np.ndarray:
    """Indices of `freqs` approximating `points` log-spaced samples."""
    if freqs.size < 3:
        return np.arange(freqs.size)
    f_low = max(f_min, float(freqs[1]))
    f_high = float(freqs[-1])
    if f_low >= f_high:
        return np.arange(freqs.size)
    targets = np.geomspace(f_low, f_high, points)
    indices = np.unique(np.searchsorted(freqs, targets))
    return indices[indices < freqs.size]


def magnitude_db(values: np.ndarray) -> np.ndarray:
    return np.maximum(20.0 * np.log10(np.abs(values) + EPS), DISPLAY_FLOOR_DB)


def phase_deg(values: np.ndarray) -> np.ndarray:
    """Wrapped phase in degrees, range [-180, 180]."""
    return np.degrees(np.angle(values))


def group_delay_ms(values: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    omega = 2.0 * np.pi * freqs
    phi = np.unwrap(np.angle(values))
    dphi = np.gradient(phi, omega)
    return -dphi * 1000.0


def achieved_response(result: SolveResult) -> np.ndarray:
    """Predicted corrected response H X at every mic: shape (F, M, K)."""
    return np.einsum("fms,fsk->fmk", result.h_freq, result.x_freq)


def mic_weights(result: SolveResult) -> np.ndarray:
    num_mics = result.h_freq.shape[1]
    weights = np.asarray(
        result.config.get("mic_weights", np.ones(num_mics)), dtype=np.float64
    )
    if weights.shape != (num_mics,):
        weights = np.ones(num_mics)
    return weights


def residual_table(
    result: SolveResult,
    achieved: Optional[np.ndarray] = None,
    bands: Optional[list[tuple[float, float]]] = None,
) -> list[dict[str, object]]:
    """Mic-weighted residual error ||HX - Y||^2 / ||Y||^2 in dB per band/input."""
    if achieved is None:
        achieved = achieved_response(result)
    if bands is None:
        bands = [(20.0, 120.0), (120.0, 500.0), (500.0, 5000.0), (5000.0, 20000.0)]
    error = achieved - result.y_freq
    weights = mic_weights(result)[None, :, None]
    freqs = result.freqs
    num_inputs = result.y_freq.shape[2]

    rows: list[dict[str, object]] = []
    for low, high in bands:
        selection = (freqs >= low) & (freqs <= high)
        if not np.any(selection):
            continue
        row: dict[str, object] = {"band": f"{low:g}-{high:g} Hz"}
        for input_channel in range(num_inputs):
            error_power = float(np.sum(weights * np.abs(error[selection, :, input_channel : input_channel + 1]) ** 2))
            target_power = float(np.sum(weights * np.abs(result.y_freq[selection, :, input_channel : input_channel + 1]) ** 2))
            if target_power <= EPS:
                row[f"input_{input_channel}"] = "—"
            else:
                row[f"input_{input_channel}"] = f"{10.0 * np.log10(error_power / target_power + EPS):.1f} dB"
        rows.append(row)
    return rows


def impulse_envelope(
    fir: np.ndarray, sample_rate: int, points: int = 1200
) -> tuple[np.ndarray, np.ndarray]:
    """Max-pooled |FIR| envelope in dB: returns (time_s, envelope_db)."""
    n = fir.size
    step = max(1, n // points)
    usable = n - (n % step)
    pooled = np.max(np.abs(fir[:usable]).reshape(-1, step), axis=1)
    envelope = np.maximum(20.0 * np.log10(pooled + EPS), DISPLAY_FLOOR_DB)
    time_s = (np.arange(pooled.size) + 0.5) * step / float(sample_rate)
    return time_s, envelope


def decimate_peak_preserving(
    signal: np.ndarray, points: int = 1500
) -> tuple[np.ndarray, np.ndarray]:
    """Decimate ``signal`` to about ``points`` samples for plotting.

    Each block keeps its largest-magnitude sample (preserving peak position and
    sign), so a sharp direct arrival survives decimation. Returns
    ``(positions, values)`` where ``positions`` are original sample indices.
    """
    n = int(signal.size)
    if points <= 0 or n <= points:
        return np.arange(n), np.asarray(signal, dtype=np.float64)
    step = int(np.ceil(n / points))
    usable = n - (n % step)
    blocks = signal[:usable].reshape(-1, step)
    local = np.argmax(np.abs(blocks), axis=1)
    rows = np.arange(blocks.shape[0])
    positions = rows * step + local
    values = blocks[rows, local]
    if usable < n:  # keep the strongest tail sample too
        tail = signal[usable:]
        tail_pos = usable + int(np.argmax(np.abs(tail)))
        positions = np.append(positions, tail_pos)
        values = np.append(values, signal[tail_pos])
    return positions, np.asarray(values, dtype=np.float64)


def stacked_ir_traces(
    room_irs: np.ndarray,
    sample_rate: int,
    *,
    points: int = 1500,
    spacing: float = 1.0,
    height: float = 0.42,
) -> list[dict[str, object]]:
    """Decimated, peak-normalized, vertically-stacked measured-IR traces.

    ``room_irs`` has shape ``(mics, speakers, samples)``. Measurements are
    ordered by ``(speaker, mic)``; each IR is normalized so its largest
    excursion spans ``height`` and shifted up by ``index * spacing`` so the
    traces stack without overlapping. Each returned dict carries ``speaker``,
    ``mic``, ``offset``, ``time_ms`` and ``amplitude`` (already offset), ready
    to drop into a plot.
    """
    num_mics, num_speakers, _ = room_irs.shape
    traces: list[dict[str, object]] = []
    index = 0
    for speaker in range(num_speakers):
        for mic in range(num_mics):
            ir = np.asarray(room_irs[mic, speaker], dtype=np.float64)
            peak = float(np.max(np.abs(ir))) if ir.size else 0.0
            scale = (height / peak) if peak > 0.0 else 0.0
            positions, values = decimate_peak_preserving(ir, points)
            offset = index * spacing
            traces.append(
                {
                    "speaker": speaker,
                    "mic": mic,
                    "offset": offset,
                    "time_ms": positions / float(sample_rate) * 1000.0,
                    "amplitude": values * scale + offset,
                }
            )
            index += 1
    return traces


def pre_delay_energy_ratio_db(fir: np.ndarray, sample_rate: int, delay_s: float) -> float:
    """Energy before 90% of the target delay relative to total, in dB."""
    split = int(0.9 * delay_s * sample_rate)
    split = min(max(split, 0), fir.size)
    total = float(np.sum(fir**2)) + EPS
    pre = float(np.sum(fir[:split] ** 2))
    return float(10.0 * np.log10(pre / total + EPS))


def filter_is_active(fir: np.ndarray) -> bool:
    return bool(np.max(np.abs(fir)) > 0.0)


def speaker_names(result: SolveResult) -> list[str]:
    return [profile.name for profile in result.profiles]
