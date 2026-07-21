"""Target matrix construction: flat house-curve and anchored targets."""

from __future__ import annotations

import math
from collections.abc import Mapping as AbcMapping
from collections.abc import Sequence as AbcSequence
from typing import Any, Mapping

import numpy as np

from ..util import EPS, db_to_amplitude
from .smoothing import _arrival_seconds, _build_log_smoothing_grid, _smooth_complex_spectrum


def target_delay_seconds(config: Mapping[str, Any]) -> float:
    """Configured target (bulk) delay in seconds, with a mode-dependent default.

    Flat targets demand linear phase plus a fixed delay at every mic, which is
    physically impossible for spaced mics and needs generous delay for the
    inverse to be causal; anchored targets keep the primary's natural (mostly
    causal) phase and tolerate less. The defaults follow the README guidance:
    180 ms flat, 100 ms anchored.
    """
    if "target_delay_ms" in config:
        return float(config["target_delay_ms"]) / 1000.0
    mode = str(config.get("target_mode", "flat")).lower()
    return (100.0 if mode == "anchored" else 180.0) / 1000.0


def target_curve_amplitude(freqs: np.ndarray, config: Mapping[str, Any]) -> np.ndarray:
    resolved = config.get("_resolved_curve_points")
    if resolved is not None:
        points_source = resolved
    else:
        points_source = config.get("target_curve_points_db", [[20.0, 0.0], [20000.0, 0.0]])

    if not isinstance(points_source, AbcSequence) or isinstance(points_source, (str, bytes)) or len(points_source) == 0:
        raise ValueError("target_curve_points_db must be a non-empty list of [freq_hz, db] points.")
    parsed = sorted((float(point[0]), float(point[1])) for point in points_source)
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

    delay_s = target_delay_seconds(config)
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
    *,
    arrivals: np.ndarray | None = None,
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
    delay_s = target_delay_seconds(config)
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
                arrival_s = _arrival_seconds(arrivals, room_irs, mic, primary, sample_rate)
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
