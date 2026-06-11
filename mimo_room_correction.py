#!/usr/bin/env python3
"""
MIMO room-correction FIR matrix solver for REW measurements and CamillaDSP.

This script builds a frequency-domain room transfer matrix from individual
speaker-to-mic impulse responses, solves a regularized MIMO inverse at every
FFT bin, transforms the result to time-domain FIR filters, and writes a
CamillaDSP branch/filter/sum YAML snippet.

Mathematical model at one frequency bin f:

    H_f: M x N room transfer matrix, microphones x speakers
    X_f: N x K FIR matrix to solve, speakers x input channels
    Y_f: M x K desired target matrix, microphones x input channels

The optimizer is:

    minimize_X || Wm^(1/2) (H_f X_f - Y_f) ||_F^2
             + || Gamma_f^(1/2) X_f ||_F^2

with the closed-form solution:

    X_f = (H_f^H Wm H_f + Gamma_f)^-1 H_f^H Wm Y_f

where Gamma_f is diagonal and frequency-dependent. Each speaker's diagonal
regularization term is increased outside its configured safe operating band
and when its measured acoustic authority is weak. A hard frequency-domain
boost cap is applied after solving as a final protection layer.

The default K is N, producing N x N FIR filters suitable for a matrix pipeline.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from collections.abc import Mapping as AbcMapping
from collections.abc import Sequence as AbcSequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import numpy as np
    from scipy import signal
    from scipy.io import wavfile
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only on missing runtime deps
    missing = exc.name or "numpy/scipy"
    raise SystemExit(
        f"Missing Python dependency '{missing}'. Install dependencies with: "
        "python3 -m pip install -r requirements.txt"
    ) from exc


EPS = 1.0e-15


@dataclass(frozen=True)
class SpeakerProfile:
    """Safe operating range and optional effort penalty for one speaker."""

    name: str
    min_hz: float
    max_hz: float
    transition_hz: float = 0.0
    effort_penalty_db: float = 0.0


@dataclass(frozen=True)
class SolveDiagnostics:
    sample_rate: int
    fft_size: int
    filter_taps: int
    reference_power: float
    max_filter_gain_db: float
    max_row_sum_gain_db: float
    warnings: tuple[str, ...]


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


def resolve_path(path: str | Path, base_dir: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve()


def read_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("Configuration root must be a JSON object.")
    return config


def write_example_config(path: Path) -> None:
    example = {
        "num_speakers": 4,
        "num_mic_positions": 4,
        "sample_rate": 48000,
        "measurement_pattern": "measurements/spk_{speaker:02d}_mic_{mic:02d}.wav",
        "output_dir": "output_firs",
        "output_format": "both",
        "filter_taps": 8192,
        "target_delay_ms": 40.0,
        "max_boost_db": 9.0,
        "max_cut_db": 18.0,
        "mic_weights": [1.0, 1.0, 0.6, 0.6],
        "speaker_profiles": {
            "0": {"name": "Sub L", "min_hz": 10.0, "max_hz": 120.0, "transition_hz": 12.0},
            "1": {"name": "Sub R", "min_hz": 10.0, "max_hz": 120.0, "transition_hz": 12.0},
            "2": {"name": "Main L", "min_hz": 80.0, "max_hz": 20000.0, "transition_hz": 24.0},
            "3": {"name": "Main R", "min_hz": 80.0, "max_hz": 20000.0, "transition_hz": 24.0},
        },
        "target_curve_points_db": [[20.0, -3.0], [80.0, 0.0], [20000.0, 0.0]],
        "target_mic_matrix": [
            [1.0, 1.0, 1.0, 1.0],
            [1.0, 1.0, 1.0, 1.0],
            [0.75, 0.75, 0.75, 0.75],
            [0.75, 0.75, 0.75, 0.75],
        ],
        "auto_target_level": True,
        "reference_band_hz": [20.0, 200.0],
        "authority_floor_db": -30.0,
        "profile_disable_threshold": 1.0e-4,
        "enforce_row_sum_gain_cap": True,
        "enforce_diagonal_cut_floor": False,
        "fade_out_samples": 512,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(example, handle, indent=2)
        handle.write("\n")


def load_wav_ir(path: Path, wav_channel: int = 0) -> tuple[int, np.ndarray]:
    sample_rate, data = wavfile.read(path)
    array = np.asarray(data)
    if array.ndim == 2:
        if wav_channel < 0 or wav_channel >= array.shape[1]:
            raise ValueError(f"WAV channel {wav_channel} is invalid for {path}.")
        array = array[:, wav_channel]
    elif array.ndim != 1:
        raise ValueError(f"Unsupported WAV shape {array.shape} in {path}.")

    if np.issubdtype(array.dtype, np.integer):
        info = np.iinfo(array.dtype)
        if info.min < 0:
            scale = max(abs(info.min), abs(info.max))
            array = array.astype(np.float64) / float(scale)
        else:
            midpoint = (info.max + 1) / 2.0
            array = (array.astype(np.float64) - midpoint) / midpoint
    else:
        array = array.astype(np.float64)

    return int(sample_rate), array.astype(np.float64, copy=False)


def parse_numeric_text(path: Path) -> np.ndarray:
    rows: list[list[float]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped[0] in {"#", ";", "*"}:
                continue
            stripped = stripped.replace(",", " ")
            values: list[float] = []
            for token in stripped.split():
                try:
                    values.append(float(token))
                except ValueError:
                    continue
            if values:
                rows.append(values)

    if not rows:
        raise ValueError(f"No numeric impulse data found in {path}.")

    if all(len(row) >= 2 for row in rows):
        return np.asarray([[row[0], row[1]] for row in rows], dtype=np.float64)
    return np.asarray([row[0] for row in rows if row], dtype=np.float64)


def load_text_ir(path: Path, sample_rate: int | None = None) -> tuple[int, np.ndarray]:
    data = parse_numeric_text(path)
    if data.ndim == 2:
        time_or_index = data[:, 0]
        impulse = data[:, 1]
        if sample_rate is None:
            diffs = np.diff(time_or_index)
            positive_diffs = diffs[diffs > 0]
            if positive_diffs.size == 0:
                raise ValueError(f"Cannot infer sample rate from {path}; set sample_rate in config.")
            median_dt = float(np.median(positive_diffs))
            if not (0.0 < median_dt < 1.0):
                raise ValueError(
                    f"First text column in {path} does not look like seconds; "
                    "set sample_rate in config."
                )
            sample_rate = int(round(1.0 / median_dt))
    else:
        impulse = data
        if sample_rate is None:
            raise ValueError(f"One-column text impulse {path} requires sample_rate in config.")

    return int(sample_rate), np.ascontiguousarray(impulse, dtype=np.float64)


def load_impulse_response(path: Path, sample_rate: int | None, wav_channel: int) -> tuple[int, np.ndarray]:
    if not path.exists():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return load_wav_ir(path, wav_channel=wav_channel)
    return load_text_ir(path, sample_rate=sample_rate)


def measurement_path_grid(config: Mapping[str, Any], base_dir: Path) -> list[list[Path]]:
    num_speakers = int(config["num_speakers"])
    num_mics = int(config["num_mic_positions"])
    grid: list[list[Path | None]] = [[None for _ in range(num_speakers)] for _ in range(num_mics)]

    if "measurements" in config:
        measurements = config["measurements"]
        if not isinstance(measurements, list):
            raise ValueError("measurements must be a list of {speaker, mic, path} objects.")
        for entry in measurements:
            speaker = int(entry["speaker"])
            mic = int(entry["mic"])
            if speaker < 0 or speaker >= num_speakers or mic < 0 or mic >= num_mics:
                raise ValueError(f"Measurement index out of range: {entry}")
            grid[mic][speaker] = resolve_path(entry["path"], base_dir)
    elif "measurement_pattern" in config:
        pattern = str(config["measurement_pattern"])
        for mic in range(num_mics):
            for speaker in range(num_speakers):
                formatted = pattern.format(
                    speaker=speaker,
                    mic=mic,
                    speaker1=speaker + 1,
                    mic1=mic + 1,
                )
                grid[mic][speaker] = resolve_path(formatted, base_dir)
    else:
        raise ValueError("Config must provide either measurements or measurement_pattern.")

    missing = [f"mic {m}, speaker {s}" for m in range(num_mics) for s in range(num_speakers) if grid[m][s] is None]
    if missing:
        raise ValueError("Missing measurements: " + ", ".join(missing))

    return [[path for path in row if path is not None] for row in grid]


def crop_or_pad_ir(impulse: np.ndarray, start_sample: int, length: int) -> np.ndarray:
    if start_sample < 0:
        raise ValueError("IR crop start must be non-negative.")
    segment = impulse[start_sample : start_sample + length]
    result = np.zeros(length, dtype=np.float64)
    copy_len = min(length, segment.size)
    if copy_len > 0:
        result[:copy_len] = segment[:copy_len]
    return result


def load_measurement_matrix(config: Mapping[str, Any], base_dir: Path) -> tuple[int, np.ndarray]:
    num_speakers = int(config["num_speakers"])
    num_mics = int(config["num_mic_positions"])
    configured_sample_rate = config.get("sample_rate")
    configured_sample_rate = int(configured_sample_rate) if configured_sample_rate is not None else None
    wav_channel = int(config.get("wav_channel", 0))
    grid = measurement_path_grid(config, base_dir)

    loaded: list[list[np.ndarray]] = [[np.empty(0) for _ in range(num_speakers)] for _ in range(num_mics)]
    sample_rate: int | None = configured_sample_rate
    max_len = 0

    for mic in range(num_mics):
        for speaker in range(num_speakers):
            fs, impulse = load_impulse_response(grid[mic][speaker], configured_sample_rate, wav_channel)
            if sample_rate is None:
                sample_rate = fs
            if fs != sample_rate:
                raise ValueError(
                    f"Sample rate mismatch in {grid[mic][speaker]}: got {fs}, expected {sample_rate}."
                )
            if not np.all(np.isfinite(impulse)):
                raise ValueError(f"Non-finite samples found in {grid[mic][speaker]}.")
            loaded[mic][speaker] = impulse
            max_len = max(max_len, int(impulse.size))

    if sample_rate is None:
        raise ValueError("No sample rate available.")

    start_sample = int(config.get("ir_crop_start_sample", 0))
    if "ir_crop_start_ms" in config:
        start_sample += int(round(float(config["ir_crop_start_ms"]) * sample_rate / 1000.0))
    ir_length = int(config.get("ir_length_samples", max_len - start_sample))
    if ir_length <= 0:
        raise ValueError("IR length must be positive.")

    room_irs = np.zeros((num_mics, num_speakers, ir_length), dtype=np.float64)
    for mic in range(num_mics):
        for speaker in range(num_speakers):
            room_irs[mic, speaker, :] = crop_or_pad_ir(loaded[mic][speaker], start_sample, ir_length)

    return sample_rate, room_irs


def _build_log_smoothing_grid(
    freqs: np.ndarray, fraction: float
) -> tuple[np.ndarray, np.ndarray, int, float]:
    """Build the log-frequency grid and Gaussian kernel for complex smoothing."""
    f_low = max(float(freqs[1]), 0.5)
    f_high = float(freqs[-1])
    points_per_octave = max(int(round(32.0 * fraction)), 192)
    num_octaves = math.log2(f_high / f_low)
    num_points = int(math.ceil(num_octaves * points_per_octave)) + 1
    log_f = f_low * 2.0 ** (np.arange(num_points) / points_per_octave)

    # Gaussian kernel whose FWHM spans 1/fraction octave on the log grid.
    sigma = points_per_octave / (fraction * 2.354820045)
    radius = max(int(math.ceil(4.0 * sigma)), 1)
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    return log_f, kernel, radius, f_low


def _smooth_complex_spectrum(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    log_f: np.ndarray,
    kernel: np.ndarray,
    radius: int,
    f_low: float,
    delay_s: float,
) -> np.ndarray:
    """Smooth one complex spectrum after de-rotating a known bulk delay.

    Smoothing complex bins directly would attenuate any response whose group
    delay exceeds the inverse smoothing bandwidth, so the spectrum is first
    de-rotated by `delay_s`, smoothed on the log-frequency grid, and rotated
    back. The rotation is undone exactly, so absolute timing is preserved.
    """
    rotation = np.exp(2j * np.pi * freqs * delay_s)
    rotated = spectrum * rotation

    resampled = np.interp(log_f, freqs, rotated.real) + 1j * np.interp(
        log_f, freqs, rotated.imag
    )
    padded = np.pad(resampled, radius, mode="edge")
    filtered = np.convolve(padded, kernel, mode="valid")

    result = np.interp(freqs, log_f, filtered.real) + 1j * np.interp(
        freqs, log_f, filtered.imag
    )
    # Keep DC and any bins below the log grid unsmoothed.
    below = freqs < f_low
    result[below] = rotated[below]
    return result * np.conj(rotation)


def fractional_octave_complex_smooth(
    h_freq: np.ndarray,
    freqs: np.ndarray,
    room_irs: np.ndarray,
    sample_rate: int,
    fraction: float,
) -> np.ndarray:
    """Apply fractional-octave complex smoothing to the room transfer matrix.

    Each measurement is de-rotated by its own direct-sound arrival time
    (estimated from the impulse peak) before smoothing, then rotated back, so
    relative phase between speakers and microphone positions is preserved.
    This is the frequency-domain equivalent of a frequency-dependent window
    of roughly `fraction` cycles centered on each IR's arrival.
    """
    if fraction <= 0.0:
        return h_freq

    num_freqs, num_mics, num_speakers = h_freq.shape
    if num_freqs < 4:
        return h_freq

    log_f, kernel, radius, f_low = _build_log_smoothing_grid(freqs, fraction)
    smoothed = np.array(h_freq, copy=True)
    for mic in range(num_mics):
        for speaker in range(num_speakers):
            impulse = room_irs[mic, speaker]
            arrival_s = int(np.argmax(np.abs(impulse))) / float(sample_rate)
            smoothed[:, mic, speaker] = _smooth_complex_spectrum(
                h_freq[:, mic, speaker], freqs, log_f, kernel, radius, f_low, arrival_s
            )

    smoothed[0, :, :] = np.real(smoothed[0, :, :])
    return smoothed


def smooth_solution_matrix(
    x_freq: np.ndarray,
    freqs: np.ndarray,
    fraction: float,
    delay_s: float,
    fft_size: int,
) -> np.ndarray:
    """Apply fractional-octave complex smoothing to the solved FIR matrix.

    The solver introduces sharp spectral transitions at speaker band edges and
    regularization boundaries; those features ring for seconds in the time
    domain. Smoothing the solution (de-rotated by the bulk target delay)
    bounds the Q of every filter feature and forces a fast, Gaussian-shaped
    time decay, making the FIRs fit within the configured tap count.
    """
    if fraction <= 0.0:
        return x_freq

    num_freqs = x_freq.shape[0]
    if num_freqs < 4:
        return x_freq

    log_f, kernel, radius, f_low = _build_log_smoothing_grid(freqs, fraction)
    smoothed = np.array(x_freq, copy=True)
    for out_ch in range(x_freq.shape[1]):
        for in_ch in range(x_freq.shape[2]):
            smoothed[:, out_ch, in_ch] = _smooth_complex_spectrum(
                x_freq[:, out_ch, in_ch], freqs, log_f, kernel, radius, f_low, delay_s
            )

    smoothed[0, :, :] = np.real(smoothed[0, :, :])
    if (num_freqs - 1) * 2 == fft_size:
        smoothed[-1, :, :] = np.real(smoothed[-1, :, :])
    return smoothed


def parse_speaker_profiles(config: Mapping[str, Any], sample_rate: int) -> list[SpeakerProfile]:
    num_speakers = int(config["num_speakers"])
    raw = config.get("speaker_profiles")
    if raw is None:
        raise ValueError("speaker_profiles is required.")

    profiles: list[SpeakerProfile] = []
    for index in range(num_speakers):
        if isinstance(raw, AbcMapping):
            entry = raw.get(str(index), raw.get(index))
        elif isinstance(raw, AbcSequence) and not isinstance(raw, (str, bytes)):
            entry = raw[index] if index < len(raw) else None
        else:
            entry = None
        if entry is None:
            raise ValueError(f"Missing speaker profile for speaker {index}.")
        if not isinstance(entry, AbcMapping):
            raise ValueError(f"Speaker profile {index} must be an object.")
        min_hz = float(entry.get("min_hz", entry.get("low_hz", 0.0)))
        max_hz = float(entry.get("max_hz", entry.get("high_hz", sample_rate / 2.0)))
        transition_hz = float(entry.get("transition_hz", 0.0))
        effort_penalty_db = float(entry.get("effort_penalty_db", 0.0))
        if min_hz < 0.0 or max_hz <= min_hz:
            raise ValueError(f"Invalid frequency range for speaker {index}: {entry}")
        profiles.append(
            SpeakerProfile(
                name=str(entry.get("name", f"speaker_{index:02d}")),
                min_hz=min_hz,
                max_hz=max_hz,
                transition_hz=max(0.0, transition_hz),
                effort_penalty_db=effort_penalty_db,
            )
        )
    return profiles


def raised_sine_band_weight(freqs: np.ndarray, low_hz: float, high_hz: float, transition_hz: float) -> np.ndarray:
    """Return 0..1 band weight that is strictly zero below low_hz and above high_hz."""
    weights = np.zeros_like(freqs, dtype=np.float64)
    if transition_hz <= 0.0:
        weights[(freqs >= low_hz) & (freqs <= high_hz)] = 1.0
        return weights

    width = high_hz - low_hz
    if width <= 0.0:
        return weights

    transition = min(transition_hz, width / 2.0)
    full_low = low_hz + transition
    full_high = high_hz - transition

    if full_low >= full_high:
        in_band = (freqs >= low_hz) & (freqs <= high_hz)
        x = (freqs[in_band] - low_hz) / max(width, EPS)
        weights[in_band] = np.sin(np.pi * x)
        return np.clip(weights, 0.0, 1.0)

    ramp_up = (freqs >= low_hz) & (freqs < full_low)
    if np.any(ramp_up):
        x = (freqs[ramp_up] - low_hz) / max(transition, EPS)
        weights[ramp_up] = np.sin(0.5 * np.pi * x) ** 2

    plateau = (freqs >= full_low) & (freqs <= full_high)
    weights[plateau] = 1.0

    ramp_down = (freqs > full_high) & (freqs <= high_hz)
    if np.any(ramp_down):
        x = (freqs[ramp_down] - full_high) / max(transition, EPS)
        weights[ramp_down] = np.cos(0.5 * np.pi * x) ** 2

    return np.clip(weights, 0.0, 1.0)


def build_profile_weights(freqs: np.ndarray, profiles: Sequence[SpeakerProfile]) -> tuple[np.ndarray, np.ndarray]:
    weights = np.zeros((freqs.size, len(profiles)), dtype=np.float64)
    effort_power = np.ones(len(profiles), dtype=np.float64)
    for index, profile in enumerate(profiles):
        weights[:, index] = raised_sine_band_weight(
            freqs,
            profile.min_hz,
            profile.max_hz,
            profile.transition_hz,
        )
        effort_power[index] = db_to_power(max(0.0, profile.effort_penalty_db))
    return weights, effort_power


def target_curve_amplitude(freqs: np.ndarray, config: Mapping[str, Any]) -> np.ndarray:
    points = config.get("target_curve_points_db", [[20.0, 0.0], [20000.0, 0.0]])
    if not isinstance(points, AbcSequence) or isinstance(points, (str, bytes)) or len(points) == 0:
        raise ValueError("target_curve_points_db must be a non-empty list of [freq_hz, db] points.")
    parsed = sorted((float(point[0]), float(point[1])) for point in points)
    if len(parsed) == 1:
        return np.full(freqs.size, db_to_amplitude(parsed[0][1]), dtype=np.float64)

    point_freqs = np.asarray([p[0] for p in parsed], dtype=np.float64)
    point_db = np.asarray([p[1] for p in parsed], dtype=np.float64)
    if np.any(point_freqs <= 0.0):
        raise ValueError("Target-curve frequencies must be positive.")
    log_points = np.log10(point_freqs)
    log_freqs = np.log10(np.maximum(freqs, point_freqs[0]))
    interpolated_db = np.interp(log_freqs, log_points, point_db, left=point_db[0], right=point_db[-1])
    return np.asarray(10.0 ** (interpolated_db / 20.0), dtype=np.float64)


def parse_target_mic_matrix(config: Mapping[str, Any], num_mics: int, num_inputs: int) -> np.ndarray:
    raw = config.get("target_mic_matrix")
    if raw is None:
        mic_gains = config.get("target_mic_gains")
        if mic_gains is not None:
            gains = np.asarray(mic_gains, dtype=np.float64)
            if gains.shape != (num_mics,):
                raise ValueError(f"target_mic_gains must have shape ({num_mics},).")
            return np.tile(gains[:, None], (1, num_inputs))
        return np.ones((num_mics, num_inputs), dtype=np.float64)

    matrix = np.asarray(raw, dtype=np.float64)
    if matrix.shape != (num_mics, num_inputs):
        raise ValueError(f"target_mic_matrix must have shape ({num_mics}, {num_inputs}), got {matrix.shape}.")
    return matrix


def estimate_reference_power(
    h_freq: np.ndarray,
    freqs: np.ndarray,
    profile_weights: np.ndarray,
    mic_weights: np.ndarray,
    config: Mapping[str, Any],
) -> float:
    reference_band = config.get("reference_band_hz", [20.0, min(200.0, float(freqs[-1]))])
    if len(reference_band) != 2:
        raise ValueError("reference_band_hz must be [low_hz, high_hz].")
    low_hz = float(reference_band[0])
    high_hz = float(reference_band[1])
    mask = (freqs >= low_hz) & (freqs <= high_hz)
    if not np.any(mask):
        mask = freqs > 0.0

    sqrt_w = np.sqrt(mic_weights.astype(np.float64))
    h_weighted_all = h_freq * sqrt_w[None, :, None]
    powers_all = np.sum(np.abs(h_weighted_all) ** 2, axis=1)
    active_mask = (profile_weights > 0.5) & mask[:, None]
    selected = powers_all[active_mask]
    if selected.size == 0:
        return 1.0
    return max(float(np.median(selected)), EPS)


def resolve_target_level(
    freqs: np.ndarray,
    h_freq: np.ndarray,
    profile_weights: np.ndarray,
    mic_weights: np.ndarray,
    config: Mapping[str, Any],
) -> float:
    explicit_level = config.get("target_level_linear")
    if explicit_level is not None:
        return float(explicit_level)
    if bool(config.get("auto_target_level", True)):
        reference_power = estimate_reference_power(h_freq, freqs, profile_weights, mic_weights, config)
        return math.sqrt(reference_power / max(float(np.sum(mic_weights)), EPS))
    return 1.0


def build_target_matrix(
    freqs: np.ndarray,
    h_freq: np.ndarray,
    profile_weights: np.ndarray,
    mic_weights: np.ndarray,
    config: Mapping[str, Any],
) -> tuple[np.ndarray, float]:
    num_mics = int(config["num_mic_positions"])
    num_inputs = int(config.get("num_inputs", config["num_speakers"]))
    target_gains = parse_target_mic_matrix(config, num_mics, num_inputs)
    target_amp = target_curve_amplitude(freqs, config)
    target_level = resolve_target_level(freqs, h_freq, profile_weights, mic_weights, config)

    delay_s = float(config.get("target_delay_ms", 40.0)) / 1000.0
    phase = np.exp(-2j * np.pi * freqs * delay_s)
    y_freq = (
        target_level
        * target_amp[:, None, None]
        * phase[:, None, None]
        * target_gains[None, :, :]
    )
    return np.asarray(y_freq, dtype=np.complex128), target_level


def parse_input_primary_speakers(
    config: Mapping[str, Any], num_speakers: int, num_inputs: int
) -> list[int]:
    """Return the primary speaker index for each input channel.

    Configured via `input_primary_speaker`, for example:

        "input_primary_speaker": {"0": 3, "1": 4}
    """
    raw = config.get("input_primary_speaker")
    if raw is None:
        raise ValueError("target_mode 'anchored' requires input_primary_speaker in the config.")

    primaries: list[int] = []
    for input_channel in range(num_inputs):
        if isinstance(raw, AbcMapping):
            entry = raw.get(str(input_channel), raw.get(input_channel))
        elif isinstance(raw, AbcSequence) and not isinstance(raw, (str, bytes)):
            entry = raw[input_channel] if input_channel < len(raw) else None
        else:
            raise ValueError("input_primary_speaker must be a mapping or list of speaker indices.")
        if entry is None:
            raise ValueError(f"input_primary_speaker is missing an entry for input {input_channel}.")
        speaker = int(entry)
        if speaker < 0 or speaker >= num_speakers:
            raise ValueError(
                f"input_primary_speaker for input {input_channel} references "
                f"invalid speaker index {speaker}."
            )
        primaries.append(speaker)
    return primaries


def build_anchored_target_matrix(
    freqs: np.ndarray,
    h_freq: np.ndarray,
    room_irs: np.ndarray,
    sample_rate: int,
    profile_weights: np.ndarray,
    mic_weights: np.ndarray,
    config: Mapping[str, Any],
) -> tuple[np.ndarray, float]:
    """Build an anchored target derived from each input's primary speaker.

    Instead of demanding identical flat magnitude and pure-delay phase at every
    microphone (which is physically impossible for spaced mics and forces the
    solver to fight propagation geometry), the target for input k at mic m is:

        Y[f, m, k] = level * house_curve(f) * g[m] * P[f, m, p(k)] * exp(-2j pi f delay)

    where p(k) is the primary speaker for input k and, per mic:

      - P is the unit-magnitude phase of the primary's heavily smoothed measured
        response, preserving each position's natural arrival time (ITD) and
        broad phase behavior. Where the primary's smoothed response falls below
        `anchor_level_floor_db` relative to its in-band level, |P| shrinks toward
        zero so the target stops demanding output the primary cannot deliver.
      - g[m] is the primary's broadband level at mic m relative to the
        mic-weighted average, preserving natural level differences (ILD).

    The solver then only corrects deviations (modes, resonances, SBIR) and
    steers support speakers to cancel the difference between what the primary
    does and what it should do at each position.
    """
    num_mics = h_freq.shape[1]
    num_speakers = h_freq.shape[2]
    num_inputs = int(config.get("num_inputs", num_speakers))
    primaries = parse_input_primary_speakers(config, num_speakers, num_inputs)

    target_amp = target_curve_amplitude(freqs, config)
    target_level = resolve_target_level(freqs, h_freq, profile_weights, mic_weights, config)
    delay_s = float(config.get("target_delay_ms", 40.0)) / 1000.0
    bulk_phase = np.exp(-2j * np.pi * freqs * delay_s)

    fraction = float(config.get("anchor_phase_smoothing_fraction", 1.0))
    if fraction <= 0.0:
        raise ValueError("anchor_phase_smoothing_fraction must be positive.")
    floor = db_to_amplitude(-abs(float(config.get("anchor_level_floor_db", -30.0))))

    reference_band = config.get("reference_band_hz", [20.0, min(200.0, float(freqs[-1]))])
    band_mask = (freqs >= float(reference_band[0])) & (freqs <= float(reference_band[1]))
    if not np.any(band_mask):
        band_mask = freqs > 0.0

    log_f, kernel, radius, f_low = _build_log_smoothing_grid(freqs, fraction)

    y_freq = np.zeros((freqs.size, num_mics, num_inputs), dtype=np.complex128)
    smoothed_cache: dict[tuple[int, int], np.ndarray] = {}
    for input_channel, primary in enumerate(primaries):
        levels = np.zeros(num_mics, dtype=np.float64)
        references = []
        for mic in range(num_mics):
            key = (mic, primary)
            if key not in smoothed_cache:
                impulse = room_irs[mic, primary]
                arrival_s = int(np.argmax(np.abs(impulse))) / float(sample_rate)
                smoothed_cache[key] = _smooth_complex_spectrum(
                    h_freq[:, mic, primary], freqs, log_f, kernel, radius, f_low, arrival_s
                )
            reference = smoothed_cache[key]
            references.append(reference)
            levels[mic] = math.sqrt(
                float(np.mean(np.abs(reference[band_mask]) ** 2)) + EPS
            )

        weighted_mean_level = math.sqrt(
            float(np.sum(mic_weights * levels**2) / max(np.sum(mic_weights), EPS))
        )
        gains = levels / max(weighted_mean_level, EPS)

        for mic in range(num_mics):
            reference = references[mic]
            magnitude = np.abs(reference)
            phase_ref = reference / (magnitude + floor * levels[mic] + EPS)
            y_freq[:, mic, input_channel] = (
                target_level * target_amp * gains[mic] * phase_ref * bulk_phase
            )

    y_freq[0, :, :] = np.real(y_freq[0, :, :])
    return y_freq, target_level


def parse_input_speaker_mask(
    config: Mapping[str, Any], num_speakers: int, num_inputs: int
) -> np.ndarray:
    """Return a boolean (speakers x inputs) mask of allowed speaker/input pairs.

    Configured via `input_speakers`, mapping each input channel to the list of
    speaker indices allowed to reproduce it, for example:

        "input_speakers": {"0": [0, 1, 2, 3], "1": [0, 1, 2, 4]}

    Absent config allows every speaker for every input (full matrix).
    """
    raw = config.get("input_speakers")
    if raw is None:
        return np.ones((num_speakers, num_inputs), dtype=bool)

    mask = np.zeros((num_speakers, num_inputs), dtype=bool)
    for input_channel in range(num_inputs):
        if isinstance(raw, AbcMapping):
            entry = raw.get(str(input_channel), raw.get(input_channel))
        elif isinstance(raw, AbcSequence) and not isinstance(raw, (str, bytes)):
            entry = raw[input_channel] if input_channel < len(raw) else None
        else:
            raise ValueError("input_speakers must be a mapping or list of speaker-index lists.")
        if entry is None:
            raise ValueError(f"input_speakers is missing an entry for input {input_channel}.")
        speakers = [int(value) for value in entry]
        if not speakers:
            raise ValueError(f"input_speakers entry for input {input_channel} is empty.")
        for speaker in speakers:
            if speaker < 0 or speaker >= num_speakers:
                raise ValueError(
                    f"input_speakers entry for input {input_channel} references "
                    f"invalid speaker index {speaker}."
                )
            mask[speaker, input_channel] = True
    return mask


def cap_complex_magnitude(values: np.ndarray, max_magnitude: float) -> np.ndarray:
    if max_magnitude <= 0.0:
        raise ValueError("max_magnitude must be positive.")
    magnitudes = np.abs(values)
    over = magnitudes > max_magnitude
    if np.any(over):
        capped = values.copy()
        capped[over] *= max_magnitude / np.maximum(magnitudes[over], EPS)
        return capped
    return values


def apply_row_sum_cap(values: np.ndarray, max_row_sum: float) -> np.ndarray:
    row_sums = np.sum(np.abs(values), axis=-1, keepdims=True)
    scale = np.ones_like(row_sums)
    over = row_sums > max_row_sum
    scale[over] = max_row_sum / np.maximum(row_sums[over], EPS)
    return values * scale


def solve_frequency_domain_filters(
    h_freq: np.ndarray,
    y_freq: np.ndarray,
    freqs: np.ndarray,
    profile_weights: np.ndarray,
    profile_effort_power: np.ndarray,
    mic_weights: np.ndarray,
    reference_power: float,
    config: Mapping[str, Any],
) -> np.ndarray:
    num_freqs, num_mics, num_speakers = h_freq.shape
    if y_freq.shape[0] != num_freqs or y_freq.shape[1] != num_mics:
        raise ValueError("Y target matrix dimensions do not match H matrix.")
    num_inputs = y_freq.shape[2]

    max_boost_db = float(config.get("max_boost_db", 9.0))
    max_boost = db_to_amplitude(abs(max_boost_db))
    max_cut_db = abs(float(config.get("max_cut_db", 120.0)))
    min_direct_gain = db_to_amplitude(-max_cut_db)
    enforce_diagonal_cut_floor = bool(config.get("enforce_diagonal_cut_floor", False))
    enforce_row_sum_gain_cap = bool(config.get("enforce_row_sum_gain_cap", True))
    profile_disable_threshold = float(config.get("profile_disable_threshold", 1.0e-4))
    authority_floor_db = float(config.get("authority_floor_db", -30.0))
    authority_floor = db_to_power(authority_floor_db)
    null_reg_strength = float(config.get("null_regularization_strength", 1.0))
    profile_transition_penalty = float(config.get("profile_transition_penalty", 10.0))
    profile_disable_penalty = float(config.get("profile_disable_penalty", 1.0e12))

    if "base_regularization" in config:
        base_beta = float(config["base_regularization"])
    else:
        # Scalar inverse h*/(|h|^2 + beta) peaks near 1/(2*sqrt(beta)).
        # Scaling beta from the median measured column power ties the cap to the
        # REW calibration level instead of assuming absolute acoustic units.
        base_beta = reference_power / max(4.0 * max_boost * max_boost, EPS)
    base_beta = max(base_beta, EPS)

    sqrt_mic_weights = np.sqrt(mic_weights.astype(np.float64))

    profile = np.clip(profile_weights, 0.0, 1.0)
    h_eff = h_freq * profile[:, None, :]
    h_weighted = h_eff * sqrt_mic_weights[None, :, None]
    y_weighted = y_freq * sqrt_mic_weights[None, :, None]

    column_power = np.sum(np.abs(h_weighted) ** 2, axis=1)
    relative_power = column_power / max(reference_power, EPS)

    beta = np.full((num_freqs, num_speakers), base_beta, dtype=np.float64)
    beta *= profile_effort_power[None, :]
    beta += base_beta * null_reg_strength * np.maximum(0.0, authority_floor / np.maximum(relative_power, EPS) - 1.0)
    beta += base_beta * profile_transition_penalty * ((1.0 - profile) ** 2) / np.maximum(profile**2, EPS)
    disabled = profile <= profile_disable_threshold
    beta[disabled] = np.maximum(beta[disabled], reference_power * profile_disable_penalty)

    gram = np.matmul(h_weighted.conj().transpose(0, 2, 1), h_weighted)
    rhs = np.matmul(h_weighted.conj().transpose(0, 2, 1), y_weighted)
    identity = np.eye(num_speakers)[None, ...]

    input_mask = parse_input_speaker_mask(config, num_speakers, num_inputs)

    def regularized_solve(system: np.ndarray, rhs_block: np.ndarray) -> np.ndarray:
        try:
            return np.linalg.solve(system, rhs_block)
        except np.linalg.LinAlgError:
            return np.linalg.lstsq(system, rhs_block, rcond=None)[0]

    if np.all(input_mask):
        system = gram + identity * beta[..., None]
        z_freq = regularized_solve(system, rhs)
    else:
        # Each input channel may only use its allowed speakers, so the
        # regularization (and hence the system matrix) differs per input.
        z_freq = np.empty((num_freqs, num_speakers, num_inputs), dtype=np.complex128)
        for input_channel in range(num_inputs):
            blocked = ~input_mask[:, input_channel]
            beta_input = beta.copy()
            beta_input[:, blocked] = np.maximum(
                beta_input[:, blocked], reference_power * profile_disable_penalty
            )
            system = gram + identity * beta_input[..., None]
            solved_column = regularized_solve(system, rhs[:, :, input_channel : input_channel + 1])
            solved_column[:, blocked, :] = 0.0
            z_freq[:, :, input_channel] = solved_column[..., 0]

    solved = profile[..., None] * z_freq
    solved = cap_complex_magnitude(solved, max_boost)
    if enforce_row_sum_gain_cap:
        solved = apply_row_sum_cap(solved, max_boost)

    if enforce_diagonal_cut_floor:
        diagonal_count = min(num_speakers, num_inputs)
        for diag in range(diagonal_count):
            active = profile[:, diag] > profile_disable_threshold
            diag_vals = solved[:, diag, diag]
            magnitudes = np.abs(diag_vals)

            needs_boost = active & (magnitudes > 0.0) & (magnitudes < min_direct_gain)
            diag_vals[needs_boost] *= min_direct_gain / np.maximum(magnitudes[needs_boost], EPS)

            is_zero = active & (magnitudes == 0.0)
            diag_vals[is_zero] = min_direct_gain
            solved[:, diag, diag] = diag_vals

    solved[0, :, :] = np.real(solved[0, :, :])
    if (num_freqs - 1) * 2 == int(config["fft_size"]):
        solved[-1, :, :] = np.real(solved[-1, :, :])
    return solved


def frequency_matrix_to_firs(x_freq: np.ndarray, config: Mapping[str, Any]) -> tuple[np.ndarray, list[str]]:
    fft_size = int(config["fft_size"])
    filter_taps = int(config["filter_taps"])
    if filter_taps <= 0 or filter_taps > fft_size:
        raise ValueError("filter_taps must be positive and no larger than fft_size.")

    full = np.fft.irfft(x_freq, n=fft_size, axis=0)
    tail_check = min(filter_taps, fft_size // 8)
    warnings: list[str] = []
    if tail_check > 0:
        total_energy = np.sum(full**2, axis=0) + EPS
        wrap_energy = np.sum(full[-tail_check:, :, :] ** 2, axis=0) / total_energy
        if float(np.max(wrap_energy)) > float(config.get("wrap_energy_warning_ratio", 1.0e-3)):
            warnings.append(
                "Significant inverse-filter energy appears at the circular FFT wrap point; "
                "increase target_delay_ms, fft_size, or filter_taps for a more causal FIR."
            )

    firs = np.array(full[:filter_taps, :, :], dtype=np.float64, copy=True)
    fade_out_samples = int(config.get("fade_out_samples", 0))
    if fade_out_samples > 0:
        fade_len = min(fade_out_samples, filter_taps)
        tail = signal.windows.hann(2 * fade_len, sym=True)[fade_len:]
        if tail.size != fade_len:
            tail = np.linspace(1.0, 0.0, fade_len, dtype=np.float64)
        window = np.ones(filter_taps, dtype=np.float64)
        window[-fade_len:] *= tail
        firs *= window[:, None, None]

    if bool(config.get("remove_denormals", True)):
        firs[np.abs(firs) < 1.0e-24] = 0.0

    if bool(config.get("enforce_final_gain_cap", True)):
        firs = enforce_final_fir_gain_cap(firs, fft_size, float(config.get("max_boost_db", 9.0)), config)

    return firs, warnings


def enforce_final_fir_gain_cap(
    firs: np.ndarray,
    fft_size: int,
    max_boost_db: float,
    config: Mapping[str, Any],
) -> np.ndarray:
    max_boost = db_to_amplitude(abs(max_boost_db))
    response = np.fft.rfft(firs, n=fft_size, axis=0)
    result = np.array(firs, copy=True)

    if bool(config.get("enforce_row_sum_gain_cap", True)):
        row_sum_peak = np.max(np.sum(np.abs(response), axis=2), axis=0)
        for speaker, peak in enumerate(row_sum_peak):
            if peak > max_boost:
                result[:, speaker, :] *= max_boost / max(float(peak), EPS)
    else:
        peak = np.max(np.abs(response), axis=0)
        for speaker in range(result.shape[1]):
            for input_channel in range(result.shape[2]):
                if peak[speaker, input_channel] > max_boost:
                    result[:, speaker, input_channel] *= max_boost / max(float(peak[speaker, input_channel]), EPS)

    return result


def export_firs(
    firs: np.ndarray,
    sample_rate: int,
    output_dir: Path,
    output_format: str,
) -> dict[tuple[int, int], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_format = output_format.lower()
    if output_format not in {"wav", "txt", "both"}:
        raise ValueError("output_format must be wav, txt, or both.")

    paths: dict[tuple[int, int], dict[str, Path]] = {}
    num_outputs = firs.shape[1]
    num_inputs = firs.shape[2]
    for output_channel in range(num_outputs):
        for input_channel in range(num_inputs):
            stem = f"fir_o{output_channel:02d}_i{input_channel:02d}"
            coeffs = np.asarray(firs[:, output_channel, input_channel], dtype=np.float64)
            item: dict[str, Path] = {}
            if output_format in {"wav", "both"}:
                wav_path = output_dir / f"{stem}.wav"
                wavfile.write(wav_path, sample_rate, coeffs.astype(np.float32))
                item["wav"] = wav_path
            if output_format in {"txt", "both"}:
                txt_path = output_dir / f"{stem}.txt"
                np.savetxt(txt_path, coeffs, fmt="%.12e")
                item["txt"] = txt_path
            paths[(output_channel, input_channel)] = item
    return paths


def yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def generate_camilladsp_yaml(
    num_outputs: int,
    num_inputs: int,
    filter_paths: Mapping[tuple[int, int], Mapping[str, Path]],
    output_dir: Path,
    config: Mapping[str, Any],
) -> str:
    prefix = str(config.get("camilladsp_filter_path_prefix", ""))
    use_absolute = bool(config.get("camilladsp_absolute_paths", False))
    conv_type = str(config.get("camilladsp_conv_type", "wav")).lower()
    if conv_type not in {"wav", "raw"}:
        raise ValueError("camilladsp_conv_type must be 'wav' or 'raw'.")
    path_key = "wav" if conv_type == "wav" else "txt"
    branch_count = num_outputs * num_inputs

    def filter_filename(output_channel: int, input_channel: int) -> str:
        item = filter_paths[(output_channel, input_channel)]
        path = item.get(path_key)
        if path is None:
            needed = "'wav'" if path_key == "wav" else "'txt'"
            raise ValueError(
                f"CamillaDSP YAML generation with camilladsp_conv_type={conv_type!r} "
                f"requires {needed} FIR files. Set output_format to {needed} or 'both'."
            )
        if use_absolute:
            return str(path.resolve())
        if prefix:
            return str(Path(prefix) / path.name)
        try:
            return str(path.relative_to(output_dir))
        except ValueError:
            return path.name

    lines: list[str] = []
    lines.append("# CamillaDSP FIR matrix snippet generated by mimo_room_correction.py")
    lines.append("# Pipeline topology: input channels -> N*N FIR branches -> summed speaker outputs")
    lines.append("filters:")
    for output_channel in range(num_outputs):
        for input_channel in range(num_inputs):
            name = f"fir_o{output_channel:02d}_i{input_channel:02d}"
            lines.append(f"  {name}:")
            lines.append("    type: Conv")
            lines.append("    parameters:")
            if conv_type == "wav":
                lines.append("      type: Wav")
                lines.append(f"      filename: {yaml_quote(filter_filename(output_channel, input_channel))}")
            else:
                lines.append("      type: Raw")
                lines.append(f"      filename: {yaml_quote(filter_filename(output_channel, input_channel))}")
                lines.append("      format: TEXT")

    lines.append("mixers:")
    lines.append("  fir_matrix_expand:")
    lines.append("    channels:")
    lines.append(f"      in: {num_inputs}")
    lines.append(f"      out: {branch_count}")
    lines.append("    mapping:")
    for output_channel in range(num_outputs):
        for input_channel in range(num_inputs):
            branch = output_channel * num_inputs + input_channel
            lines.append(f"      - dest: {branch}")
            lines.append("        sources:")
            lines.append(f"          - channel: {input_channel}")
            lines.append("            gain: 0.0")
            lines.append("            inverted: false")

    lines.append("  fir_matrix_sum:")
    lines.append("    channels:")
    lines.append(f"      in: {branch_count}")
    lines.append(f"      out: {num_outputs}")
    lines.append("    mapping:")
    for output_channel in range(num_outputs):
        lines.append(f"      - dest: {output_channel}")
        lines.append("        sources:")
        for input_channel in range(num_inputs):
            branch = output_channel * num_inputs + input_channel
            lines.append(f"          - channel: {branch}")
            lines.append("            gain: 0.0")
            lines.append("            inverted: false")

    lines.append("pipeline:")
    lines.append("  - type: Mixer")
    lines.append("    name: fir_matrix_expand")
    for output_channel in range(num_outputs):
        for input_channel in range(num_inputs):
            branch = output_channel * num_inputs + input_channel
            name = f"fir_o{output_channel:02d}_i{input_channel:02d}"
            lines.append("  - type: Filter")
            lines.append(f"    channels: [{branch}]")
            lines.append("    names:")
            lines.append(f"      - {name}")
    lines.append("  - type: Mixer")
    lines.append("    name: fir_matrix_sum")
    lines.append("")
    return "\n".join(lines)


def compute_gain_diagnostics(firs: np.ndarray, fft_size: int) -> tuple[float, float]:
    response = np.fft.rfft(firs, n=fft_size, axis=0)
    max_filter_gain = float(np.max(np.abs(response)))
    max_row_sum_gain = float(np.max(np.sum(np.abs(response), axis=2)))
    return amplitude_to_db(max_filter_gain), amplitude_to_db(max_row_sum_gain)


def solve_from_room_irs(
    room_irs: np.ndarray,
    sample_rate: int,
    config: dict[str, Any],
    base_dir: Path,
) -> tuple[np.ndarray, SolveDiagnostics, dict[tuple[int, int], dict[str, Path]], Path]:
    if room_irs.ndim != 3:
        raise ValueError("room_irs must have shape M x N x samples.")
    num_mics, num_speakers, ir_length = room_irs.shape
    config["num_mic_positions"] = int(config.get("num_mic_positions", num_mics))
    config["num_speakers"] = int(config.get("num_speakers", num_speakers))
    if config["num_mic_positions"] != num_mics or config["num_speakers"] != num_speakers:
        raise ValueError("Config dimensions do not match room_irs dimensions.")

    profiles = parse_speaker_profiles(config, sample_rate)
    filter_taps = int(config.get("filter_taps", 8192))
    fft_size = int(config.get("fft_size", next_power_of_two(ir_length + filter_taps - 1)))
    if fft_size < ir_length + filter_taps - 1:
        raise ValueError("fft_size must be at least ir_length + filter_taps - 1.")
    config["fft_size"] = fft_size
    config["filter_taps"] = filter_taps

    mic_weights = np.asarray(config.get("mic_weights", np.ones(num_mics)), dtype=np.float64)
    if mic_weights.shape != (num_mics,):
        raise ValueError(f"mic_weights must have shape ({num_mics},).")
    if np.any(mic_weights < 0.0) or not np.any(mic_weights > 0.0):
        raise ValueError("mic_weights must be non-negative with at least one positive weight.")

    h_freq = np.fft.rfft(room_irs, n=fft_size, axis=2)
    h_freq = np.moveaxis(h_freq, 2, 0)
    h_freq = np.ascontiguousarray(h_freq)
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / float(sample_rate))
    smoothing_fraction = float(config.get("h_smoothing_fraction", 0.0))
    if smoothing_fraction > 0.0:
        h_freq = fractional_octave_complex_smooth(
            h_freq, freqs, room_irs, sample_rate, smoothing_fraction
        )
    profile_weights, profile_effort_power = build_profile_weights(freqs, profiles)
    reference_power = estimate_reference_power(h_freq, freqs, profile_weights, mic_weights, config)
    target_mode = str(config.get("target_mode", "flat")).lower()
    if target_mode == "anchored":
        y_freq, _target_level = build_anchored_target_matrix(
            freqs, h_freq, room_irs, sample_rate, profile_weights, mic_weights, config
        )
    elif target_mode == "flat":
        y_freq, _target_level = build_target_matrix(freqs, h_freq, profile_weights, mic_weights, config)
    else:
        raise ValueError("target_mode must be 'flat' or 'anchored'.")
    x_freq = solve_frequency_domain_filters(
        h_freq=h_freq,
        y_freq=y_freq,
        freqs=freqs,
        profile_weights=profile_weights,
        profile_effort_power=profile_effort_power,
        mic_weights=mic_weights,
        reference_power=reference_power,
        config=config,
    )
    x_smoothing_fraction = float(config.get("x_smoothing_fraction", 0.0))
    if x_smoothing_fraction > 0.0:
        x_freq = smooth_solution_matrix(
            x_freq,
            freqs,
            x_smoothing_fraction,
            float(config.get("target_delay_ms", 40.0)) / 1000.0,
            fft_size,
        )
    firs, fir_warnings = frequency_matrix_to_firs(x_freq, config)

    output_dir = resolve_path(config.get("output_dir", "mimo_fir_output"), base_dir)
    output_format = str(config.get("output_format", "wav"))
    filter_paths = export_firs(firs, sample_rate, output_dir, output_format)
    yaml_text = generate_camilladsp_yaml(num_speakers, y_freq.shape[2], filter_paths, output_dir, config)
    yaml_path = output_dir / "camilladsp_fir_matrix.yml"
    yaml_path.write_text(yaml_text, encoding="utf-8")

    max_filter_gain_db, max_row_sum_gain_db = compute_gain_diagnostics(firs, fft_size)
    diagnostics = SolveDiagnostics(
        sample_rate=sample_rate,
        fft_size=fft_size,
        filter_taps=filter_taps,
        reference_power=reference_power,
        max_filter_gain_db=max_filter_gain_db,
        max_row_sum_gain_db=max_row_sum_gain_db,
        warnings=tuple(fir_warnings),
    )
    diagnostics_path = output_dir / "diagnostics.json"
    diagnostics_path.write_text(
        json.dumps(
            {
                "sample_rate": diagnostics.sample_rate,
                "fft_size": diagnostics.fft_size,
                "filter_taps": diagnostics.filter_taps,
                "reference_power": diagnostics.reference_power,
                "max_filter_gain_db": diagnostics.max_filter_gain_db,
                "max_row_sum_gain_db": diagnostics.max_row_sum_gain_db,
                "warnings": list(diagnostics.warnings),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return firs, diagnostics, filter_paths, yaml_path


def run_pipeline(config_path: Path) -> None:
    config = read_config(config_path)
    base_dir = config_path.resolve().parent
    sample_rate, room_irs = load_measurement_matrix(config, base_dir)
    _firs, diagnostics, filter_paths, yaml_path = solve_from_room_irs(room_irs, sample_rate, config, base_dir)

    output_dir = yaml_path.parent
    print(f"Wrote {len(filter_paths)} FIR paths to {output_dir}")
    print(f"Wrote CamillaDSP snippet: {yaml_path}")
    print(f"FFT size: {diagnostics.fft_size}, taps: {diagnostics.filter_taps}, sample rate: {diagnostics.sample_rate}")
    print(f"Peak individual FIR gain: {diagnostics.max_filter_gain_db:.2f} dB")
    print(f"Peak speaker row-sum gain: {diagnostics.max_row_sum_gain_db:.2f} dB")
    for warning in diagnostics.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)


def synthetic_room_irs(sample_rate: int, num_mics: int, num_speakers: int, length: int) -> np.ndarray:
    rng = np.random.default_rng(1234)
    room = np.zeros((num_mics, num_speakers, length), dtype=np.float64)
    for mic in range(num_mics):
        for speaker in range(num_speakers):
            direct_delay = 48 + 9 * mic + 13 * speaker
            if direct_delay < length:
                room[mic, speaker, direct_delay] += 0.8 + 0.2 * rng.random()
            for reflection in range(1, 8):
                delay = direct_delay + reflection * (80 + 11 * mic + 5 * speaker)
                if delay >= length:
                    break
                sign = -1.0 if reflection % 2 else 1.0
                room[mic, speaker, delay] += sign * (0.25 / reflection) * rng.uniform(0.7, 1.2)

            # Add a low-frequency modal tail with speaker/mic-dependent polarity.
            t = np.arange(length, dtype=np.float64) / sample_rate
            mode_hz = 37.0 + 4.0 * mic + 3.0 * speaker
            envelope = np.exp(-t / 0.18)
            polarity = -1.0 if (mic + speaker) % 2 else 1.0
            room[mic, speaker, :] += polarity * 0.04 * np.sin(2.0 * np.pi * mode_hz * t) * envelope
    return room


def run_smoke_test(output_dir: Path) -> None:
    sample_rate = 48000
    num_speakers = 3
    num_mics = 3
    room_irs = synthetic_room_irs(sample_rate, num_mics, num_speakers, length=2048)
    config: dict[str, Any] = {
        "num_speakers": num_speakers,
        "num_mic_positions": num_mics,
        "output_dir": str(output_dir),
        "output_format": "both",
        "filter_taps": 2048,
        "target_delay_ms": 25.0,
        "max_boost_db": 9.0,
        "max_cut_db": 18.0,
        "mic_weights": [1.0, 0.8, 0.6],
        "speaker_profiles": {
            "0": {"name": "Sub", "min_hz": 10.0, "max_hz": 120.0, "transition_hz": 12.0},
            "1": {"name": "Small L", "min_hz": 80.0, "max_hz": 20000.0, "transition_hz": 24.0},
            "2": {"name": "Small R", "min_hz": 80.0, "max_hz": 20000.0, "transition_hz": 24.0},
        },
        "target_curve_points_db": [[20.0, -3.0], [80.0, 0.0], [20000.0, 0.0]],
        "auto_target_level": True,
        "reference_band_hz": [20.0, 200.0],
        "authority_floor_db": -30.0,
        "enforce_row_sum_gain_cap": True,
        "fade_out_samples": 256,
    }
    _firs, diagnostics, filter_paths, yaml_path = solve_from_room_irs(room_irs, sample_rate, config, Path.cwd())
    print(f"Smoke test wrote {len(filter_paths)} filter entries to {output_dir}")
    print(f"CamillaDSP snippet: {yaml_path}")
    print(f"Peak row-sum gain: {diagnostics.max_row_sum_gain_db:.2f} dB")
    if diagnostics.warnings:
        for warning in diagnostics.warnings:
            print(f"WARNING: {warning}", file=sys.stderr)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Solve a MIMO room-correction FIR matrix from REW impulse responses.",
    )
    parser.add_argument("--config", type=Path, help="Path to JSON configuration file.")
    parser.add_argument("--write-example-config", type=Path, help="Write an example JSON config and exit.")
    parser.add_argument("--smoke-test", action="store_true", help="Run a synthetic end-to-end smoke test.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "mimo_room_correction_smoke",
        help="Output directory for --smoke-test.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.write_example_config is not None:
        write_example_config(args.write_example_config)
        print(f"Wrote example config: {args.write_example_config}")
        return 0

    if args.smoke_test:
        run_smoke_test(args.output_dir)
        return 0

    if args.config is None:
        parser.error("--config is required unless --smoke-test or --write-example-config is used.")
    run_pipeline(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
