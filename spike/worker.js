// Pyodide Web Worker: loads Pyodide, numpy, scipy, unpacks optimimo, runs solve()
// Posts progress and timing results back to the main thread.

const PYODIDE_VERSION = "0.27.5";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/pyodide.js`;

let pyodide = null;

function postStatus(stage, fraction, extra = {}) {
  postMessage({ type: "progress", stage, fraction, ...extra });
}

function postResult(data) {
  postMessage({ type: "result", ...data });
}

function postError(msg) {
  postMessage({ type: "error", message: msg });
}

async function initPyodide() {
  const t0 = performance.now();
  importScripts(PYODIDE_URL);
  pyodide = await loadPyodide({ indexURL: `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/` });
  const bootMs = performance.now() - t0;
  postStatus("pyodide_boot", 0, { boot_ms: bootMs });
  return bootMs;
}

async function loadPackages() {
  const t0 = performance.now();
  await pyodide.loadPackage(["numpy"]);
  const pkgMs = performance.now() - t0;
  postStatus("packages_loaded", 0, { package_load_ms: pkgMs });
  return pkgMs;
}

async function loadOptimimo() {
  const t0 = performance.now();
  const resp = await fetch("optimimo.zip");
  if (!resp.ok) throw new Error(`Failed to fetch optimimo.zip: ${resp.status}`);
  const buf = await resp.arrayBuffer();
  const fetchMs = performance.now() - t0;

  const t1 = performance.now();
  pyodide.unpackArchive(buf, "zip", "/tmp/optimimo_pkg");
  const unpackMs = performance.now() - t1;

  pyodide.runPython(`
import sys
sys.path.insert(0, "/tmp/optimimo_pkg")
`);
  const totalMs = performance.now() - t0;
  postStatus("optimimo_loaded", 0, { fetch_ms: fetchMs, unpack_ms: unpackMs, total_ms: totalMs });
  return totalMs;
}

async function runSolve() {
  const pyCode = `
import time
import json
import numpy as np

# Synthetic IRs matching cli.py synthetic_room_irs
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

sample_rate = 48000
num_speakers = 3
num_mics = 3
ir_length = 32768

room_irs = synthetic_room_irs(sample_rate, num_mics, num_speakers, ir_length)

config = {
    "num_speakers": num_speakers,
    "num_mic_positions": num_mics,
    "output_dir": "/tmp/optimimo_out",
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

from optimimo.core.pipeline import solve

stage_timings = {}
progress_log = []

def progress_cb(stage, fraction):
    import time as _t
    now = _t.monotonic()
    progress_log.append({"stage": stage, "fraction": fraction, "t": now})
    # Record first-seen time per stage
    if stage not in stage_timings:
        stage_timings[stage] = {"start": now}
    else:
        stage_timings[stage]["end"] = now

t_start = time.monotonic()
result = solve(room_irs, sample_rate, config, progress=progress_cb, cancel=None)
t_end = time.monotonic()

total_ms = (t_end - t_start) * 1000.0

# Compute per-stage durations from progress_log timestamps
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

# FIR checksum for parity verification
firs = result.firs
firs_checksum = float(np.sum(np.abs(firs)))
firs_max = float(np.max(np.abs(firs)))
firs_shape = list(firs.shape)

# Serialize firs to bytes for parity check
import io
buf = io.BytesIO()
np.save(buf, firs)
firs_bytes_b64 = __import__("base64").b64encode(buf.getvalue()).decode("ascii")

result_json = json.dumps({
    "total_ms": total_ms,
    "stage_ms": stage_ms,
    "firs_checksum": firs_checksum,
    "firs_max": firs_max,
    "firs_shape": firs_shape,
    "fft_size": result.config.get("fft_size"),
    "filter_taps": result.config.get("filter_taps"),
    "progress_count": len(progress_log),
})
`;

  const t0 = performance.now();
  pyodide.setStdout({ batched: (s) => postStatus("stdout", 0, { text: s }) });
  pyodide.setStderr({ batched: (s) => postStatus("stderr", 0, { text: s }) });

  // Set up progress bridge: Python calls postMessage via JS callback
  pyodide.globals.set("_postProgress", (stage, fraction) => {
    postStatus("solve_progress", fraction, { stage });
  });

  // Inject a JS-side progress bridge that Python can call
  pyodide.runPython(`
import js
_original_progress = None
def _js_progress_bridge(stage, fraction):
    js._postProgress(stage, fraction)
`);

  // Replace the progress callback in the solve call with the JS bridge
  const resultStr = pyodide.runPython(`
import time
import json
import numpy as np

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

sample_rate = 48000
num_speakers = 3
num_mics = 3
ir_length = 32768

room_irs = synthetic_room_irs(sample_rate, num_mics, num_speakers, ir_length)

config = {
    "num_speakers": num_speakers,
    "num_mic_positions": num_mics,
    "output_dir": "/tmp/optimimo_out",
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

from optimimo.core.pipeline import solve

progress_log = []
def progress_cb(stage, fraction):
    import time as _t
    now = _t.monotonic()
    progress_log.append({"stage": stage, "fraction": fraction, "t": now})
    try:
        import js
        js._postProgress(stage, fraction)
    except Exception:
        pass

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

import io, base64
buf = io.BytesIO()
np.save(buf, firs)
firs_bytes_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

json.dumps({
    "total_ms": total_ms,
    "stage_ms": stage_ms,
    "firs_checksum": firs_checksum,
    "firs_max": firs_max,
    "firs_shape": firs_shape,
    "fft_size": result.config.get("fft_size"),
    "filter_taps": result.config.get("filter_taps"),
    "progress_count": len(progress_log),
    "firs_b64_len": len(firs_bytes_b64),
})
`);

  const solveMs = performance.now() - t0;

  // Get the firs bytes for parity comparison
  const firsB64 = pyodide.runPython(`firs_bytes_b64`);

  const resultData = JSON.parse(resultStr);
  resultData.solve_wall_ms = solveMs;
  resultData.firs_b64 = firsB64;

  return resultData;
}

async function main() {
  try {
    postStatus("init", 0);

    const bootMs = await initPyodide();
    const pkgMs = await loadPackages();
    const optMs = await loadOptimimo();

    postStatus("solving", 0.1);
    const result = await runSolve();

    postResult({
      boot_ms: bootMs,
      package_load_ms: pkgMs,
      optimimo_load_ms: optMs,
      solve: result,
    });
  } catch (err) {
    postError(`${err.message}\n${err.stack || ""}`);
  }
}

main();
