"""Command-line interface for the MIMO room-correction solver."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .core.io import load_measurement_matrix, read_config, resolve_target_curve
from .core.pipeline import solve_from_room_irs


def example_config() -> dict[str, Any]:
    """Example configuration for a stereo system with three support subs.

    Every parameter is documented in the Configuration Reference section of
    the README.
    """
    return {
        "num_speakers": 5,
        "num_inputs": 2,
        "num_mic_positions": 4,
        "sample_rate": 96000,
        "measurement_pattern": "measurements/spk_{speaker:02d}_mic_{mic:02d}.wav",
        "output_dir": "output_firs",
        "output_format": "both",
        "camilladsp_conv_type": "raw",
        "ir_length_samples": 65536,
        "filter_taps": 65536,
        "fft_size": 262144,
        "target_delay_ms": 100.0,
        "max_boost_db": 9.0,
        "max_cut_db": 18.0,
        "mic_weights": [1.0, 0.75, 0.75, 0.5],
        "speaker_profiles": {
            "0": {"name": "Sub L", "min_hz": 40.0, "max_hz": 120.0, "transition_hz": 12.0},
            "1": {"name": "Sub M", "min_hz": 17.0, "max_hz": 120.0, "transition_hz": 8.0},
            "2": {"name": "Sub R", "min_hz": 40.0, "max_hz": 120.0, "transition_hz": 12.0},
            "3": {"name": "Main L", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 10.0},
            "4": {"name": "Main R", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 10.0},
        },
        "input_speakers": {
            "0": [0, 1, 2, 3],
            "1": [0, 1, 2, 4],
        },
        "target_mode": "anchored",
        "input_primary_speaker": {"0": 3, "1": 4},
        "anchor_phase_smoothing_fraction": 1.0,
        "anchor_level_floor_db": -30.0,
        "target_curve_points_db": [[10.0, -12.0], [17.0, -3.0], [20.0, 0.0], [20000.0, 0.0]],
        "auto_target_level": True,
        "reference_band_hz": [20.0, 200.0],
        "h_smoothing_fraction": 6.0,
        "x_smoothing_fraction": 6.0,
        "authority_floor_db": -30.0,
        "profile_disable_threshold": 1.0e-4,
        "enforce_row_sum_gain_cap": True,
        "enforce_diagonal_cut_floor": False,
        "fade_out_samples": 2048,
    }


def write_example_config(path: Path) -> None:
    """Write the example configuration to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(example_config(), handle, indent=2)
        handle.write("\n")


def run_pipeline(config_path: Path) -> None:
    config = read_config(config_path)
    base_dir = config_path.resolve().parent
    sample_rate, room_irs = load_measurement_matrix(config, base_dir)
    resolve_target_curve(config, base_dir, sample_rate)
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
        default=Path(tempfile.gettempdir()) / "optimimo_smoke",
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
