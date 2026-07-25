#!/usr/bin/env python3
"""Compare webapp solve vs native solve with identical synthetic data."""

import json
import numpy as np
import sys

# Add the repo to the path
sys.path.insert(0, "/Users/avyverman/optiMIMO/.kilo/worktrees/spike-pyodide-solver")

from optimimo.core.pipeline import solve

# Generate synthetic IRs matching the webapp's generator
def generate_synthetic(num_mics=3, num_speakers=5, ir_length=32768, sample_rate=96000):
    """Match the webapp's generateSynthetic() function."""
    seed = 1234

    def rng():
        nonlocal seed
        seed |= 0
        seed = (seed + 0x6D2B79F5) | 0
        t = (seed ^ (seed >> 15)) & 0xFFFFFFFF
        t = (t * (1 | seed)) & 0xFFFFFFFF
        t = (t + ((t ^ (t >> 7)) * (61 | t))) & 0xFFFFFFFF
        t = t ^ (t >> 14)
        return (t & 0xFFFFFFFF) / 4294967296

    result = []
    for m in range(num_mics):
        for s in range(num_speakers):
            ir = np.zeros(ir_length, dtype=np.float64)
            direct_delay = 48 + 9 * m + 13 * s
            if direct_delay < ir_length:
                ir[direct_delay] += 0.8 + 0.2 * rng()
            for ref in range(1, 8):
                delay = direct_delay + ref * (80 + 11 * m + 5 * s)
                if delay >= ir_length:
                    break
                sign = 1 if ref % 2 else -1
                ir[delay] += sign * (0.25 / ref) * (0.7 + 0.5 * rng())
            # Modal tail
            mode_hz = 37 + 4 * m + 3 * s
            polarity = -1 if (m + s) % 2 else 1
            t = np.arange(ir_length) / sample_rate
            ir += polarity * 0.04 * np.sin(2 * np.pi * mode_hz * t) * np.exp(-t / 0.18)
            result.append(ir)

    # Reshape to (mics, speakers, samples)
    room_irs = np.array(result).reshape(num_mics, num_speakers, ir_length)
    return room_irs

# Config matching the webapp's getConfig()
config = {
    "num_speakers": 5,
    "num_mic_positions": 3,
    "num_inputs": 2,
    "sample_rate": 96000,
    "filter_taps": 8192,
    "target_delay_ms": 100.0,
    "max_boost_db": 9.0,
    "max_cut_db": 18.0,
    "h_smoothing_fraction": 6.0,
    "x_smoothing_fraction": 6.0,
    "fade_out_samples": 256,
    "target_mode": "anchored",
    "auto_target_level": True,
    "target_curve_points_db": [[20.0, -3.0], [80.0, 0.0], [20000.0, 0.0]],
    "speaker_profiles": {
        "0": {"name": "L", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 24.0},
        "1": {"name": "R", "min_hz": 20.0, "max_hz": 20000.0, "transition_hz": 24.0},
        "2": {"name": "Sub M", "min_hz": 20.0, "max_hz": 300.0, "transition_hz": 24.0},
        "3": {"name": "Sub R", "min_hz": 40.0, "max_hz": 300.0, "transition_hz": 24.0},
        "4": {"name": "Sub L", "min_hz": 40.0, "max_hz": 300.0, "transition_hz": 24.0},
    },
    "input_speakers": {"0": [0, 1, 2, 3, 4], "1": [0, 1, 2, 3, 4]},
    "input_primary_speaker": {"0": 0, "1": 1},
    "mic_weights": [1.0, 1.0, 1.0],
    "reference_band_hz": [20.0, 200.0],
    "authority_floor_db": -30.0,
    "enforce_row_sum_gain_cap": True,
    "output_format": "wav",
}

print("Generating synthetic IRs...")
room_irs = generate_synthetic()
print(f"IR shape: {room_irs.shape}")

print("Running native solve...")
result = solve(room_irs, 96000, config)

print(f"FIR shape: {result.firs.shape}")
print(f"Max filter gain: {result.diagnostics.max_filter_gain_db:.2f} dB")
print(f"Max row-sum gain: {result.diagnostics.max_row_sum_gain_db:.2f} dB")
print(f"Warnings: {result.diagnostics.warnings}")

# Save for comparison with webapp
np.save("/tmp/native_firs.npy", result.firs)
print("\nSaved native FIRs to /tmp/native_firs.npy")

# Also save the room_irs for the webapp test
np.save("/tmp/synthetic_irs.npy", room_irs)
print("Saved synthetic IRs to /tmp/synthetic_irs.npy")

# Print config as JSON for the webapp test
print("\nConfig JSON:")
print(json.dumps(config, indent=2))
