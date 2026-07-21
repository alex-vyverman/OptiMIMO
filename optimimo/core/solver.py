"""Speaker profiles, regularized MIMO inverse, and FIR conversion."""

from __future__ import annotations

from collections.abc import Mapping as AbcMapping
from collections.abc import Sequence as AbcSequence
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import signal

from ..util import EPS, amplitude_to_db, db_to_amplitude, db_to_power


@dataclass(frozen=True)
class SpeakerProfile:
    """Safe operating range and optional effort penalty for one speaker."""

    name: str
    min_hz: float
    max_hz: float
    transition_hz: float = 0.0
    effort_penalty_db: float = 0.0


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
    max_boost = db_to_amplitude(max_boost_db)
    max_cut_db = abs(float(config.get("max_cut_db", 18.0)))
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


def compute_gain_diagnostics(firs: np.ndarray, fft_size: int) -> tuple[float, float]:
    response = np.fft.rfft(firs, n=fft_size, axis=0)
    max_filter_gain = float(np.max(np.abs(response)))
    max_row_sum_gain = float(np.max(np.sum(np.abs(response), axis=2)))
    return amplitude_to_db(max_filter_gain), amplitude_to_db(max_row_sum_gain)
