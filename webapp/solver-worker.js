// OptiMIMO Solver Web Worker — runs the MIMO FIR solver in Pyodide (WASM).
// Receives: { type: "solve", irData: Float64Array[], config: {...}, irShape: [M, N, samples],
//             cancelBuffer: SharedArrayBuffer (Int32, index 0: 0=running, 1=cancel) }
// Posts:    { type: "progress", stage, fraction }
//           { type: "result", firs: ArrayBuffer, diagnostics: {...}, timings: {...} }
//           { type: "error", message }
//           { type: "cancelled" }

const PYODIDE_VERSION = "0.27.5";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/pyodide.js`;

let pyodide = null;
let booted = false;
let cancelView = null; // Int32Array over SharedArrayBuffer

function postStatus(stage, fraction, extra = {}) {
  postMessage({ type: "progress", stage, fraction, ...extra });
}

function postResult(data) {
  postMessage({ type: "result", ...data });
}

function postError(msg) {
  postMessage({ type: "error", message: msg });
}

function isCancelled() {
  return cancelView ? Atomics.load(cancelView, 0) !== 0 : false;
}

async function initPyodide() {
  if (booted) return;
  const t0 = performance.now();
  importScripts(PYODIDE_URL);
  pyodide = await loadPyodide({
    indexURL: `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`,
  });
  const bootMs = performance.now() - t0;
  postStatus("pyodide_boot", 0, { boot_ms: bootMs });

  const t1 = performance.now();
  await pyodide.loadPackage(["numpy"]);
  const pkgMs = performance.now() - t1;
  postStatus("packages_loaded", 0, { package_load_ms: pkgMs });

  const t2 = performance.now();
  const resp = await fetch("optimimo.zip");
  if (!resp.ok) throw new Error(`Failed to fetch optimimo.zip: ${resp.status}`);
  const buf = await resp.arrayBuffer();
  pyodide.unpackArchive(buf, "zip", "/tmp/optimimo_pkg");
  pyodide.runPython(`import sys; sys.path.insert(0, "/tmp/optimimo_pkg")`);
  const optMs = performance.now() - t2;
  postStatus("optimimo_loaded", 0, { optimimo_load_ms: optMs });

  // Capture Python stderr for debugging
  pyodide.setStderr({
    batched: (text) => {
      postStatus("stderr", 0, { text });
    },
  });

  // Check numpy version
  const numpyVersion = pyodide.runPython(`import numpy; numpy.__version__`);

  booted = true;
  postStatus("ready", 0, { boot_ms: bootMs, package_load_ms: pkgMs, optimimo_load_ms: optMs, numpy_version: numpyVersion });
}

async function runSolve(msg) {
  const { irData, irShape, config } = msg;
  const [numMics, numSpeakers, irLength] = irShape;

  // Set up cancel buffer
  if (msg.cancelBuffer) {
    cancelView = new Int32Array(msg.cancelBuffer);
    // Do NOT reset the flag here — the main thread manages it.
    // It may have been set before the solve message arrived.
  } else {
    cancelView = null;
  }

  // Write IR data into Pyodide's FS as a binary file, then load in Python
  const totalSamples = numMics * numSpeakers * irLength;
  const flat = new Float64Array(totalSamples);
  for (let m = 0; m < numMics; m++) {
    for (let s = 0; s < numSpeakers; s++) {
      const src = irData[m * numSpeakers + s];
      flat.set(src, (m * numSpeakers + s) * irLength);
    }
  }
  pyodide.FS.writeFile("/tmp/ir_data.bin", new Uint8Array(flat.buffer));

  // Set up progress bridge — must be on globalThis so Python's `import js` can find it
  globalThis._postProgress = (stage, fraction) => {
    postStatus("solve_progress", fraction, { stage });
  };

  // Set up cancel check bridge: Python calls js._isCancelled() to check SharedArrayBuffer
  globalThis._isCancelled = () => isCancelled();

  const configJson = JSON.stringify(config);

  const resultJson = pyodide.runPython(`
import time, json, io, base64
import numpy as np

# Load IR data from binary file
ir_shape = (${numMics}, ${numSpeakers}, ${irLength})
flat = np.fromfile("/tmp/ir_data.bin", dtype=np.float64)
room_irs = flat.reshape(ir_shape)

config = json.loads('''${configJson}''')
sample_rate = int(config.get("sample_rate", 48000))

# Debug: return config in result
_debug_config = dict(config)

from optimimo.core.pipeline import solve
from optimimo.core.smoothing import SolveCancelled

# Cancel token that mimics threading.Event but checks a SharedArrayBuffer
# via the JS bridge. solve() calls cancel.is_set() at each checkpoint.
class JSCancelToken:
    def is_set(self):
        try:
            import js
            return bool(js._isCancelled())
        except Exception:
            return False

cancel_token = JSCancelToken()

progress_log = []
def progress_cb(stage, fraction):
    now = time.monotonic()
    progress_log.append({"stage": stage, "fraction": fraction, "t": now})
    try:
        import js
        js._postProgress(stage, fraction)
    except Exception:
        pass

t_start = time.monotonic()
try:
    result = solve(room_irs, sample_rate, config, progress=progress_cb, cancel=cancel_token)
    cancelled = False
except SolveCancelled:
    result = None
    cancelled = True
t_end = time.monotonic()

total_ms = (t_end - t_start) * 1000.0

if cancelled:
    _result_json = json.dumps({"cancelled": True, "total_ms": total_ms})
else:
    stage_durations = {}
    for entry in progress_log:
        s = entry["stage"]
        if s not in stage_durations:
            stage_durations[s] = {"first_t": entry["t"], "last_t": entry["t"]}
        else:
            stage_durations[s]["last_t"] = entry["t"]
    stage_ms = {s: (d["last_t"] - d["first_t"]) * 1000.0 for s, d in stage_durations.items()}

    # Serialize firs
    firs = result.firs
    buf = io.BytesIO()
    np.save(buf, firs)
    firs_bytes = buf.getvalue()

    # Write firs to FS for transfer
    with open("/tmp/firs_out.npy", "wb") as fh:
        fh.write(firs_bytes)

    diagnostics = {
        "sample_rate": result.diagnostics.sample_rate,
        "fft_size": result.diagnostics.fft_size,
        "filter_taps": result.diagnostics.filter_taps,
        "reference_power": result.diagnostics.reference_power,
        "max_filter_gain_db": result.diagnostics.max_filter_gain_db,
        "max_row_sum_gain_db": result.diagnostics.max_row_sum_gain_db,
        "warnings": list(result.diagnostics.warnings),
    }

    _result_json = json.dumps({
        "cancelled": False,
        "total_ms": total_ms,
        "stage_ms": stage_ms,
        "firs_shape": list(firs.shape),
        "firs_checksum": float(np.sum(np.abs(firs))),
        "firs_max": float(np.max(np.abs(firs))),
        "fft_size": result.config.get("fft_size"),
        "filter_taps": result.config.get("filter_taps"),
        "diagnostics": diagnostics,
    })

_result_json
`);

  const result = JSON.parse(resultJson);

  if (result.cancelled) {
    return result;
  }

  // Read firs from FS
  const firsBytes = pyodide.FS.readFile("/tmp/firs_out.npy");
  const firsBuffer = firsBytes.buffer.slice(firsBytes.byteOffset, firsBytes.byteOffset + firsBytes.byteLength);
  result.firs_buffer = firsBuffer;

  return result;
}

onmessage = async (ev) => {
  const msg = ev.data;
  try {
    if (msg.type === "init") {
      await initPyodide();
    } else if (msg.type === "solve") {
      if (!booted) await initPyodide();
      postStatus("solving", 0.05);
      const result = await runSolve(msg);
      if (result.cancelled) {
        postMessage({ type: "cancelled", total_ms: result.total_ms });
      } else {
        postResult(result);
      }
    }
  } catch (err) {
    postError(`${err.message}\n${err.stack || ""}`);
  }
};
