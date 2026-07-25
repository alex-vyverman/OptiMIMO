"""Native benchmark: runs the same synthetic solve and outputs timings + FIR checksum.

Usage:
    /Users/avyverman/optiMIMO/.venv/bin/python3 spike/native_bench.py
"""
import base64
import io
import json
import sys
import time

import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from optimimo.core.pipeline import solve


def synthetic_room_irs(sample_rate, num_mics, num_speakers, length):
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
            t = np.arange(length, dtype=np.float64) / sample_rate
            mode_hz = 37.0 + 4.0 * mic + 3.0 * speaker
            envelope = np.exp(-t / 0.18)
            polarity = -1.0 if (mic + speaker) % 2 else 1.0
            room[mic, speaker, :] += polarity * 0.04 * np.sin(2.0 * np.pi * mode_hz * t) * envelope
    return room


def main():
    sample_rate = 48000
    num_speakers = 3
    num_mics = 3
    ir_length = 32768

    room_irs = synthetic_room_irs(sample_rate, num_mics, num_speakers, ir_length)

    config = {
        "num_speakers": num_speakers,
        "num_mic_positions": num_mics,
        "output_dir": "/tmp/optimimo_native_out",
        "output_format": "wav",
        "filter_taps": 8192,
        "target_delay_ms": 100.0,
        "max_boost_db": 9.0,
        "max_cut_db": 18.0,
        "mic_weights": [1.0, 0.8, 0.6],
        "speaker_profiles": {
            "0": {"name": "Sub", "min_hz": 10.0, "max_hz": 120.0, "transition_hz": 12.0},
            "1": {"name": "Small L", "min_hz": 80.0, "max_hz": 20000.0, "transition_hz": 24.0},
            "2": {"name": "Small R", "min_hz": 80.0, "max_hz": 20000.0, "transition_hz": 24.0},
        },
        "input_speakers": {"0": [0, 1], "1": [0, 2]},
        "num_inputs": 2,
        "target_mode": "flat",
        "target_curve_points_db": [[20.0, -3.0], [80.0, 0.0], [20000.0, 0.0]],
        "auto_target_level": True,
        "reference_band_hz": [20.0, 200.0],
        "h_smoothing_fraction": 6.0,
        "x_smoothing_fraction": 6.0,
        "authority_floor_db": -30.0,
        "enforce_row_sum_gain_cap": True,
        "fade_out_samples": 256,
    }

    progress_log = []

    def progress_cb(stage, fraction):
        now = time.monotonic()
        progress_log.append({"stage": stage, "fraction": fraction, "t": now})

    t_start = time.monotonic()
    result = solve(room_irs, sample_rate, config, progress=progress_cb, cancel=None)
    t_end = time.monotonic()

    total_ms = (t_end - t_start) * 1000.0

    stage_durations = {}
    for entry in progress_log:
        s = entry["stage"]
        if s not in stage_durations:
            stage_durations[s] = {"first_t": entry["t"], "last_t": entry["t"]}
        else:
            stage_durations[s]["last_t"] = entry["t"]

    stage_ms = {}
    for s, d in stage_durations.items():
        stage_ms[s] = (d["last_t"] - d["first_t"]) * 1000.0

    firs = result.firs
    firs_checksum = float(np.sum(np.abs(firs)))
    firs_max = float(np.max(np.abs(firs)))
    firs_shape = list(firs.shape)

    buf = io.BytesIO()
    np.save(buf, firs)
    firs_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    # Also save raw .npy for direct comparison
    np.save("/tmp/optimimo_native_firs.npy", firs)

    output = {
        "native": {
            "solve_total_ms": total_ms,
            "stage_ms": stage_ms,
            "firs_checksum": firs_checksum,
            "firs_max": firs_max,
            "firs_shape": firs_shape,
            "fft_size": result.config.get("fft_size"),
            "filter_taps": result.config.get("filter_taps"),
            "progress_count": len(progress_log),
        },
        "firs_b64": firs_b64,
        "status": "complete",
    }

    print(json.dumps(output, indent=2))
    print(f"\nNative FIRs saved to /tmp/optimimo_native_firs.npy", file=sys.stderr)


if __name__ == "__main__":
    main()
