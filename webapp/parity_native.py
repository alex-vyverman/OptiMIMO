#!/usr/bin/env python3
"""Native side of the webapp parity harness.

Generates deterministic synthetic IRs and solves them with the repo's
native pipeline, writing artifacts the Node orchestrator compares
against the browser (Pyodide) solve:

  <out>/meta.json          IR shape + sample rate
  <out>/irs.bin            float64 IRs, mic-major (m*S+s), C-order
  <out>/config_<mode>.json solver config per target mode
  <out>/firs_<mode>.bin    float64 FIRs (taps, N, K), C-order
  <out>/diag_<mode>.json   diagnostics per mode

Run by parity_test.js; can also be run standalone:
  python3 parity_native.py --out /tmp/parity_out
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from optimimo.core.pipeline import solve  # noqa: E402

NUM_MICS = 2
NUM_SPEAKERS = 2
IR_LENGTH = 16384
SAMPLE_RATE = 48000
MODES = ("anchored", "flat")


def make_irs(seed: int = 42) -> np.ndarray:
    """Deterministic synthetic room IRs: direct + reflections + modal tail."""
    rng = np.random.default_rng(seed)
    irs = np.zeros((NUM_MICS, NUM_SPEAKERS, IR_LENGTH), dtype=np.float64)
    for m in range(NUM_MICS):
        for s in range(NUM_SPEAKERS):
            ir = np.zeros(IR_LENGTH)
            direct = 480 + 90 * m + 130 * s
            ir[direct] += 0.8 + 0.2 * rng.random()
            for ref in range(1, 8):
                d = direct + ref * (800 + 110 * m + 50 * s)
                if d >= IR_LENGTH:
                    break
                ir[d] += ((-1.0) ** ref) * (0.25 / ref) * (0.7 + 0.5 * rng.random())
            mode_hz = 37 + 4 * m + 3 * s
            t = np.arange(IR_LENGTH) / SAMPLE_RATE
            polarity = 1.0 if (m + s) % 2 == 0 else -1.0
            ir += polarity * 0.04 * np.sin(2 * np.pi * mode_hz * t) * np.exp(-t / 0.18)
            irs[m, s] = ir
    return irs


def build_config(mode: str) -> dict:
    """Solver config shared by both sides. The Node side maps this onto the
    webapp UI fields; keep every solver-relevant key explicit here."""
    return {
        "num_speakers": NUM_SPEAKERS,
        "num_mic_positions": NUM_MICS,
        "num_inputs": 2,
        "sample_rate": SAMPLE_RATE,
        "filter_taps": 8192,
        "target_delay_ms": 100.0,
        "max_boost_db": 24.0,
        "max_cut_db": 24.0,
        "h_smoothing_fraction": 3.0,
        "x_smoothing_fraction": 3.0,
        "fade_out_samples": 256,
        "target_mode": mode,
        "auto_target_level": True,
        "target_curve_points_db": [[20.0, -3.0], [80.0, 0.0], [20000.0, 0.0]],
        "speaker_profiles": {
            "0": {"name": "Main", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 24.0},
            "1": {"name": "Sub", "min_hz": 20.0, "max_hz": 200.0, "transition_hz": 12.0},
        },
        "input_speakers": {"0": [0, 1], "1": [0, 1]},
        "input_primary_speaker": {"0": 0, "1": 1},
        "mic_weights": [1.0, 1.0],
        "reference_band_hz": [20.0, 200.0],
        "authority_floor_db": -30.0,
        "enforce_row_sum_gain_cap": True,
        "output_format": "wav",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output directory for artifacts")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    irs = make_irs()
    irs.tofile(out / "irs.bin")
    (out / "meta.json").write_text(json.dumps({
        "num_mics": NUM_MICS,
        "num_speakers": NUM_SPEAKERS,
        "ir_length": IR_LENGTH,
        "sample_rate": SAMPLE_RATE,
        "numpy": np.__version__,
    }))

    for mode in MODES:
        cfg = build_config(mode)
        result = solve(irs, SAMPLE_RATE, dict(cfg))
        result.firs.astype(np.float64).tofile(out / f"firs_{mode}.bin")
        (out / f"config_{mode}.json").write_text(json.dumps(cfg))
        (out / f"diag_{mode}.json").write_text(json.dumps({
            "fft_size": result.diagnostics.fft_size,
            "filter_taps": result.diagnostics.filter_taps,
            "firs_shape": list(result.firs.shape),
            "max_filter_gain_db": result.diagnostics.max_filter_gain_db,
            "max_row_sum_gain_db": result.diagnostics.max_row_sum_gain_db,
            "warnings": list(result.diagnostics.warnings),
        }))
        print(f"  native {mode}: max_gain={result.diagnostics.max_filter_gain_db:.2f} dB")

    print(f"native artifacts written to {out}")


if __name__ == "__main__":
    main()
