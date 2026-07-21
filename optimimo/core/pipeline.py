"""Pipeline orchestration: compute-only solve() and file-writing export().

`solve()` runs the full computation and returns a `SolveResult` carrying all
intermediate artifacts (for GUI plotting and analysis). `export()` writes FIR
files, the CamillaDSP YAML snippet, and diagnostics.json.

`solve_from_room_irs()` is the backwards-compatible wrapper with the original
signature and side effects.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..export.camilladsp import generate_camilladsp_yaml
from ..export.firs import export_firs
from ..util import next_power_of_two
from .io import compute_measurement_arrivals, resolve_path, resolve_target_curve
from .smoothing import SolveCancelled, fractional_octave_complex_smooth, smooth_solution_matrix
from .solver import (
    SpeakerProfile,
    build_profile_weights,
    compute_gain_diagnostics,
    frequency_matrix_to_firs,
    parse_input_speaker_mask,
    parse_speaker_profiles,
    solve_frequency_domain_filters,
)
from .targets import (
    build_anchored_target_matrix,
    build_target_matrix,
    estimate_reference_power,
    parse_input_primary_speakers,
    target_curve_amplitude,
)

ProgressFn = Callable[[str, float], None]


@dataclass(frozen=True)
class SolveDiagnostics:
    sample_rate: int
    fft_size: int
    filter_taps: int
    reference_power: float
    max_filter_gain_db: float
    max_row_sum_gain_db: float
    warnings: tuple[str, ...]


@dataclass
class SolveResult:
    """All artifacts of one solve, for export, plotting, and analysis."""

    sample_rate: int
    freqs: np.ndarray  # (F,)
    h_freq: np.ndarray  # (F, M, N), post-smoothing
    y_freq: np.ndarray  # (F, M, K)
    x_freq: np.ndarray  # (F, N, K), post-smoothing and caps
    firs: np.ndarray  # (taps, N, K)
    profiles: list[SpeakerProfile]
    diagnostics: SolveDiagnostics
    config: dict[str, Any]  # resolved config (fft_size etc. filled in)


@dataclass(frozen=True)
class ExportPaths:
    output_dir: Path
    filter_paths: dict[tuple[int, int], dict[str, Path]]
    yaml_path: Path
    diagnostics_path: Path


def _report(progress: ProgressFn | None, stage: str, fraction: float) -> None:
    if progress is not None:
        progress(stage, fraction)


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise SolveCancelled()


def solve(
    room_irs: np.ndarray,
    sample_rate: int,
    config: dict[str, Any],
    *,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> SolveResult:
    """Solve the MIMO inverse and return all artifacts without writing files.

    `config` is not mutated; the resolved configuration (with computed
    `fft_size`, `filter_taps`, and dimensions filled in) is returned on the
    result.
    """
    if room_irs.ndim != 3:
        raise ValueError("room_irs must have shape M x N x samples.")
    config = dict(config)
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

    # Robust per-(mic, speaker) arrival times (e.g. from REW's reported IR
    # peak), used to de-rotate each IR for H smoothing and the anchored target.
    # NaN where unknown; the smoothing/target code falls back to argmax.
    arrivals = compute_measurement_arrivals(config, sample_rate)

    _report(progress, "fft", 0.0)
    _check_cancel(cancel)
    h_freq = np.fft.rfft(room_irs, n=fft_size, axis=2)
    h_freq = np.moveaxis(h_freq, 2, 0)
    h_freq = np.ascontiguousarray(h_freq)
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / float(sample_rate))

    _report(progress, "smooth_h", 0.1)
    smoothing_fraction = float(config.get("h_smoothing_fraction", 0.0))
    if smoothing_fraction > 0.0:
        h_freq = fractional_octave_complex_smooth(
            h_freq,
            freqs,
            room_irs,
            sample_rate,
            smoothing_fraction,
            arrivals=arrivals,
            progress=(lambda f: _report(progress, "smooth_h", 0.1 + 0.195 * f)),
            cancel=cancel,
        )

    _report(progress, "target", 0.3)
    _check_cancel(cancel)
    profile_weights, profile_effort_power = build_profile_weights(freqs, profiles)
    reference_power = estimate_reference_power(h_freq, freqs, profile_weights, mic_weights, config)
    target_mode = str(config.get("target_mode", "flat")).lower()
    if target_mode == "anchored":
        y_freq, _target_level = build_anchored_target_matrix(
            freqs, h_freq, room_irs, sample_rate, profile_weights, mic_weights, config,
            arrivals=arrivals,
        )
    elif target_mode == "flat":
        y_freq, _target_level = build_target_matrix(freqs, h_freq, profile_weights, mic_weights, config)
    else:
        raise ValueError("target_mode must be 'flat' or 'anchored'.")

    _report(progress, "solve", 0.45)
    _check_cancel(cancel)
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

    _report(progress, "smooth_x", 0.7)
    x_smoothing_fraction = float(config.get("x_smoothing_fraction", 0.0))
    if x_smoothing_fraction > 0.0:
        x_freq = smooth_solution_matrix(
            x_freq,
            freqs,
            x_smoothing_fraction,
            float(config.get("target_delay_ms", 40.0)) / 1000.0,
            fft_size,
            progress=(lambda f: _report(progress, "smooth_x", 0.7 + 0.145 * f)),
            cancel=cancel,
        )

    _report(progress, "firs", 0.85)
    _check_cancel(cancel)
    firs, fir_warnings = frequency_matrix_to_firs(x_freq, config)

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
    _report(progress, "done", 1.0)
    return SolveResult(
        sample_rate=sample_rate,
        freqs=freqs,
        h_freq=h_freq,
        y_freq=y_freq,
        x_freq=x_freq,
        firs=firs,
        profiles=profiles,
        diagnostics=diagnostics,
        config=config,
    )


def export(result: SolveResult, base_dir: Path) -> ExportPaths:
    """Write FIR files, the CamillaDSP YAML snippet, and diagnostics.json."""
    config = result.config
    diagnostics = result.diagnostics
    output_dir = resolve_path(config.get("output_dir", "mimo_fir_output"), base_dir)
    output_format = str(config.get("output_format", "wav"))
    filter_paths = export_firs(result.firs, result.sample_rate, output_dir, output_format)
    yaml_text = generate_camilladsp_yaml(
        result.firs.shape[1], result.y_freq.shape[2], filter_paths, output_dir, config
    )
    yaml_path = output_dir / "camilladsp_fir_matrix.yml"
    yaml_path.write_text(yaml_text, encoding="utf-8")

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
    return ExportPaths(
        output_dir=output_dir,
        filter_paths=filter_paths,
        yaml_path=yaml_path,
        diagnostics_path=diagnostics_path,
    )


def solve_from_room_irs(
    room_irs: np.ndarray,
    sample_rate: int,
    config: dict[str, Any],
    base_dir: Path,
) -> tuple[np.ndarray, SolveDiagnostics, dict[tuple[int, int], dict[str, Path]], Path]:
    """Backwards-compatible wrapper: solve, export, and update `config` in place."""
    result = solve(room_irs, sample_rate, config)
    for key in ("num_mic_positions", "num_speakers", "fft_size", "filter_taps"):
        config[key] = result.config[key]
    paths = export(result, base_dir)
    return result.firs, result.diagnostics, paths.filter_paths, paths.yaml_path


def validate_config(config: dict[str, Any]) -> list[tuple[str, str]]:
    """Pre-flight validation of checks that do not require loading files.

    Returns a list of (field, message) issues; empty when valid.
    """
    issues: list[tuple[str, str]] = []

    def check(field: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except (ValueError, KeyError, TypeError) as exc:
            issues.append((field, str(exc)))

    num_speakers = 0
    num_mics = 0
    try:
        num_speakers = int(config["num_speakers"])
        if num_speakers <= 0:
            issues.append(("num_speakers", "num_speakers must be positive."))
    except (KeyError, TypeError, ValueError):
        issues.append(("num_speakers", "num_speakers is required and must be an integer."))
    try:
        num_mics = int(config["num_mic_positions"])
        if num_mics <= 0:
            issues.append(("num_mic_positions", "num_mic_positions must be positive."))
    except (KeyError, TypeError, ValueError):
        issues.append(("num_mic_positions", "num_mic_positions is required and must be an integer."))
    if issues:
        return issues

    num_inputs = int(config.get("num_inputs", num_speakers))
    sample_rate = int(config.get("sample_rate", 48000))

    if "measurements" not in config and "measurement_pattern" not in config:
        issues.append(("measurements", "Config must provide either measurements or measurement_pattern."))
    elif "measurements" in config:
        check(
            "measurements",
            lambda: _validate_measurement_entries(config["measurements"], num_speakers, num_mics),
        )

    check("speaker_profiles", lambda: parse_speaker_profiles(config, sample_rate))
    check("input_speakers", lambda: parse_input_speaker_mask(config, num_speakers, num_inputs))
    check(
        "target_curve_points_db",
        lambda: target_curve_amplitude(np.asarray([20.0, 1000.0]), config),
    )

    curve_file = config.get("target_curve_file")
    curve_ir = config.get("target_curve_ir_file")
    if curve_file and curve_ir:
        issues.append(
            ("target_curve_file", "Set only one of target_curve_file or target_curve_ir_file, not both.")
        )
    if curve_file and not isinstance(curve_file, str):
        issues.append(("target_curve_file", "target_curve_file must be a string path."))
    if curve_ir and not isinstance(curve_ir, str):
        issues.append(("target_curve_ir_file", "target_curve_ir_file must be a string path."))
    ir_smoothing = config.get("target_curve_ir_smoothing_fraction")
    if ir_smoothing is not None:
        try:
            val = float(ir_smoothing)
            if val < 0.0:
                issues.append(
                    ("target_curve_ir_smoothing_fraction", "Must be non-negative.")
                )
        except (TypeError, ValueError):
            issues.append(
                ("target_curve_ir_smoothing_fraction", "Must be a number.")
            )

    target_mode = str(config.get("target_mode", "flat")).lower()
    if target_mode not in {"flat", "anchored"}:
        issues.append(("target_mode", "target_mode must be 'flat' or 'anchored'."))
    elif target_mode == "anchored":
        check(
            "input_primary_speaker",
            lambda: parse_input_primary_speakers(config, num_speakers, num_inputs),
        )

    mic_weights = config.get("mic_weights")
    if mic_weights is not None:
        weights = np.asarray(mic_weights, dtype=np.float64)
        if weights.shape != (num_mics,):
            issues.append(("mic_weights", f"mic_weights must have shape ({num_mics},)."))
        elif np.any(weights < 0.0) or not np.any(weights > 0.0):
            issues.append(
                ("mic_weights", "mic_weights must be non-negative with at least one positive weight.")
            )

    output_format = str(config.get("output_format", "wav")).lower()
    if output_format not in {"wav", "txt", "both"}:
        issues.append(("output_format", "output_format must be wav, txt, or both."))
    conv_type = str(config.get("camilladsp_conv_type", "wav")).lower()
    if conv_type not in {"wav", "raw"}:
        issues.append(("camilladsp_conv_type", "camilladsp_conv_type must be 'wav' or 'raw'."))
    elif (conv_type == "raw" and output_format == "wav") or (
        conv_type == "wav" and output_format == "txt"
    ):
        issues.append(
            (
                "camilladsp_conv_type",
                f"camilladsp_conv_type '{conv_type}' requires output_format "
                f"{'txt' if conv_type == 'raw' else 'wav'} or both.",
            )
        )

    return issues


def _validate_measurement_entries(measurements: Any, num_speakers: int, num_mics: int) -> None:
    if not isinstance(measurements, list):
        raise ValueError("measurements must be a list of {speaker, mic, path} objects.")
    seen: set[tuple[int, int]] = set()
    for entry in measurements:
        speaker = int(entry["speaker"])
        mic = int(entry["mic"])
        if speaker < 0 or speaker >= num_speakers or mic < 0 or mic >= num_mics:
            raise ValueError(f"Measurement index out of range: {entry}")
        seen.add((mic, speaker))
    missing = [
        f"mic {m}, speaker {s}"
        for m in range(num_mics)
        for s in range(num_speakers)
        if (m, s) not in seen
    ]
    if missing:
        raise ValueError("Missing measurements: " + ", ".join(missing))


__all__ = [
    "ExportPaths",
    "ProgressFn",
    "SolveCancelled",
    "SolveDiagnostics",
    "SolveResult",
    "export",
    "solve",
    "solve_from_room_irs",
    "validate_config",
]
