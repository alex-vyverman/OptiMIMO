"""Loading of configs and impulse-response measurements."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from scipy.io import wavfile

from ..util import EPS, next_power_of_two
from .smoothing import _build_log_smoothing_grid

from ..util import EPS, next_power_of_two
from .smoothing import _build_log_smoothing_grid


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


def load_target_curve_text(path: Path, base_dir: Path) -> list[list[float]]:
    """Load a target house curve from a text file with freq_hz and dB columns.

    Lines starting with ``#``, ``;``, or ``*`` are treated as comments.
    Comma- and whitespace-separated values are both accepted.  The file must
    contain at least one row with two numeric columns.
    """
    resolved = resolve_path(path, base_dir)
    if not resolved.exists():
        raise FileNotFoundError(f"Target curve file not found: {resolved}")
    data = parse_numeric_text(resolved)
    if data.ndim != 2 or data.shape[1] < 2:
        raise ValueError(
            f"Target curve file {resolved} must have at least two columns (freq_hz, dB)."
        )
    points = [[float(row[0]), float(row[1])] for row in data]
    points.sort(key=lambda p: p[0])
    if any(p[0] <= 0.0 for p in points):
        raise ValueError(f"Target curve frequencies must be positive in {resolved}.")
    return points


def load_target_curve_ir(
    path: Path,
    base_dir: Path,
    sample_rate: int,
    smoothing_fraction: float = 6.0,
) -> list[list[float]]:
    """Derive a target house curve from an impulse response file.

    The magnitude response is computed via FFT, optionally smoothed with
    fractional-octave Gaussian smoothing on a log-frequency grid, and
    normalised so the median level in the 20–200 Hz band is 0 dB.  The result
    is returned as ``[freq_hz, dB]`` pairs suitable for ``target_curve_points_db``.
    """
    resolved = resolve_path(path, base_dir)
    if not resolved.exists():
        raise FileNotFoundError(f"Target curve IR not found: {resolved}")

    suffix = resolved.suffix.lower()
    if suffix == ".wav":
        fs, impulse = load_wav_ir(resolved)
    else:
        fs, impulse = load_text_ir(resolved, sample_rate=sample_rate)

    if fs != sample_rate:
        raise ValueError(
            f"Sample rate mismatch in target curve IR {resolved}: "
            f"got {fs}, expected {sample_rate}."
        )

    n_fft = next_power_of_two(max(impulse.size, 4096))
    spectrum = np.fft.rfft(impulse, n=n_fft)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / float(sample_rate))
    magnitude = np.maximum(np.abs(spectrum), EPS)
    db = 20.0 * np.log10(magnitude)

    if smoothing_fraction > 0.0 and freqs.size > 4:
        log_f, kernel, radius, _f_low = _build_log_smoothing_grid(freqs, smoothing_fraction)
        padded = np.pad(db, radius, mode="edge")
        db = np.convolve(padded, kernel, mode="valid")

    mask = freqs > 0.0
    curve_freqs = freqs[mask]
    curve_db = db[mask]

    ref_mask = (curve_freqs >= 20.0) & (curve_freqs <= 200.0)
    if np.any(ref_mask):
        curve_db = curve_db - float(np.median(curve_db[ref_mask]))

    points = [[float(f), float(d)] for f, d in zip(curve_freqs, curve_db)]
    return points


def resolve_target_curve(
    config: dict[str, Any],
    base_dir: Path,
    sample_rate: int | None = None,
) -> None:
    """Resolve a file- or IR-based target curve and inject it into ``config``.

    If ``target_curve_file`` is set, the text file is loaded and its
    ``[freq, dB]`` pairs are stored in ``config["_resolved_curve_points"]``.
    If ``target_curve_ir_file`` is set instead, the impulse response is
    loaded, its magnitude response computed and smoothed, and the result
    stored the same way.  When neither is set the function is a no-op and the
    solver falls back to ``target_curve_points_db``.

    ``sample_rate`` is required for IR-based curves (read from the measurement
    WAVs or from the config).
    """
    config.pop("_resolved_curve_points", None)

    text_path = config.get("target_curve_file")
    if text_path:
        points = load_target_curve_text(Path(text_path), base_dir)
        config["_resolved_curve_points"] = points
        return

    ir_path = config.get("target_curve_ir_file")
    if ir_path:
        if sample_rate is None:
            configured_sr = config.get("sample_rate")
            if configured_sr is not None:
                sample_rate = int(configured_sr)
            else:
                raise ValueError(
                    "target_curve_ir_file requires sample_rate in the config "
                    "or from loaded measurements."
                )
        smoothing = float(config.get("target_curve_ir_smoothing_fraction", 6.0))
        points = load_target_curve_ir(Path(ir_path), base_dir, sample_rate, smoothing)
        config["_resolved_curve_points"] = points


def load_target_curve_text(path: Path, base_dir: Path) -> list[list[float]]:
    """Load a target house curve from a text file with freq_hz and dB columns.

    Lines starting with ``#``, ``;``, or ``*`` are treated as comments.
    Comma- and whitespace-separated values are both accepted.  The file must
    contain at least one row with two numeric columns.
    """
    resolved = resolve_path(path, base_dir)
    if not resolved.exists():
        raise FileNotFoundError(f"Target curve file not found: {resolved}")
    data = parse_numeric_text(resolved)
    if data.ndim != 2 or data.shape[1] < 2:
        raise ValueError(
            f"Target curve file {resolved} must have at least two columns (freq_hz, dB)."
        )
    points = [[float(row[0]), float(row[1])] for row in data]
    points.sort(key=lambda p: p[0])
    if any(p[0] <= 0.0 for p in points):
        raise ValueError(f"Target curve frequencies must be positive in {resolved}.")
    return points


def load_target_curve_ir(
    path: Path,
    base_dir: Path,
    sample_rate: int,
    smoothing_fraction: float = 6.0,
) -> list[list[float]]:
    """Derive a target house curve from an impulse response file.

    The magnitude response is computed via FFT, optionally smoothed with
    fractional-octave Gaussian smoothing on a log-frequency grid, and
    normalised so the median level in the 20–200 Hz band is 0 dB.  The result
    is returned as ``[freq_hz, dB]`` pairs suitable for ``target_curve_points_db``.
    """
    resolved = resolve_path(path, base_dir)
    if not resolved.exists():
        raise FileNotFoundError(f"Target curve IR not found: {resolved}")

    suffix = resolved.suffix.lower()
    if suffix == ".wav":
        fs, impulse = load_wav_ir(resolved)
    else:
        fs, impulse = load_text_ir(resolved, sample_rate=sample_rate)

    if fs != sample_rate:
        raise ValueError(
            f"Sample rate mismatch in target curve IR {resolved}: "
            f"got {fs}, expected {sample_rate}."
        )

    n_fft = next_power_of_two(max(impulse.size, 4096))
    spectrum = np.fft.rfft(impulse, n=n_fft)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / float(sample_rate))
    magnitude = np.maximum(np.abs(spectrum), EPS)
    db = 20.0 * np.log10(magnitude)

    if smoothing_fraction > 0.0 and freqs.size > 4:
        log_f, kernel, radius, _f_low = _build_log_smoothing_grid(freqs, smoothing_fraction)
        padded = np.pad(db, radius, mode="edge")
        db = np.convolve(padded, kernel, mode="valid")

    mask = freqs > 0.0
    curve_freqs = freqs[mask]
    curve_db = db[mask]

    ref_mask = (curve_freqs >= 20.0) & (curve_freqs <= 200.0)
    if np.any(ref_mask):
        curve_db = curve_db - float(np.median(curve_db[ref_mask]))

    points = [[float(f), float(d)] for f, d in zip(curve_freqs, curve_db)]
    return points


def resolve_target_curve(
    config: dict[str, Any],
    base_dir: Path,
    sample_rate: int | None = None,
) -> None:
    """Resolve a file- or IR-based target curve and inject it into ``config``.

    If ``target_curve_file`` is set, the text file is loaded and its
    ``[freq, dB]`` pairs are stored in ``config["_resolved_curve_points"]``.
    If ``target_curve_ir_file`` is set instead, the impulse response is
    loaded, its magnitude response computed and smoothed, and the result
    stored the same way.  When neither is set the function is a no-op and the
    solver falls back to ``target_curve_points_db``.

    ``sample_rate`` is required for IR-based curves (read from the measurement
    WAVs or from the config).
    """
    config.pop("_resolved_curve_points", None)

    text_path = config.get("target_curve_file")
    if text_path:
        points = load_target_curve_text(Path(text_path), base_dir)
        config["_resolved_curve_points"] = points
        return

    ir_path = config.get("target_curve_ir_file")
    if ir_path:
        if sample_rate is None:
            configured_sr = config.get("sample_rate")
            if configured_sr is not None:
                sample_rate = int(configured_sr)
            else:
                raise ValueError(
                    "target_curve_ir_file requires sample_rate in the config "
                    "or from loaded measurements."
                )
        smoothing = float(config.get("target_curve_ir_smoothing_fraction", 6.0))
        points = load_target_curve_ir(Path(ir_path), base_dir, sample_rate, smoothing)
        config["_resolved_curve_points"] = points
