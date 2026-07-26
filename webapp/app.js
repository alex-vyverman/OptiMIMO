// OptiMIMO Web App — main thread logic.
// Manages: config UI, REW import, file upload, synthetic data, worker lifecycle, results.

// ============================================================
// State
// ============================================================
let worker = null;
let workerReady = false;
let solving = false;
let cancelBuffer = null; // SharedArrayBuffer for graceful cancellation
let irData = null;        // Float64Array[] — one per (mic*speakers + speaker)
let irShape = null;       // [numMics, numSpeakers, irLength]
let irArrivals = null;    // [{mic, speaker, arrival_ms}] from REW import, else null
let rewMeasurements = []; // from REW /measurements
let solveResult = null;   // last solve result
let configExtras = {};    // solver keys from a loaded config JSON that the UI doesn't expose

// REW Bridge extension (chrome.runtime messaging for hosted-app REW import).
const REW_BRIDGE_EXT_ID = "moojndmfeecbgpfpkpnilhmcbioojpmo";
// Chrome Web Store listing URL — set once the extension is published.
const REW_BRIDGE_STORE_URL = "";

// ============================================================
// Tab switching
// ============================================================
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
  });
});

// ============================================================
// Logging
// ============================================================
const logEl = document.getElementById("log");
function log(msg, cls = "") {
  const line = document.createElement("div");
  if (cls) line.style.color = `var(--${cls})`;
  line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function setStatus(msg, type = "info") {
  const area = document.getElementById("status-area");
  area.innerHTML = `<div class="status status-${type}">${msg}</div>`;
}

// ============================================================
// Config helpers
// ============================================================
function getConfig() {
  const speakers = parseInt(document.getElementById("cfg-speakers").value);
  const mics = parseInt(document.getElementById("cfg-mics").value);
  const inputs = parseInt(document.getElementById("cfg-inputs").value);
  const sampleRate = parseInt(document.getElementById("cfg-samplerate").value);
  const taps = parseInt(document.getElementById("cfg-taps").value);
  const fft = parseInt(document.getElementById("cfg-fft").value);
  const delay = parseFloat(document.getElementById("cfg-delay").value);
  const boost = parseFloat(document.getElementById("cfg-boost").value);
  const cut = parseFloat(document.getElementById("cfg-cut").value);
  const hSmooth = parseFloat(document.getElementById("cfg-hsmooth").value);
  const xSmooth = parseFloat(document.getElementById("cfg-xsmooth").value);
  const fade = parseInt(document.getElementById("cfg-fade").value);
  const irLen = parseInt(document.getElementById("cfg-irlen").value) || 0;
  const targetMode = document.getElementById("cfg-target-mode").value;
  const autoLevel = document.getElementById("cfg-auto-level").value === "true";
  const curveStr = document.getElementById("cfg-curve").value;

  // Parse curve points
  const curvePoints = curveStr.split(";").map((s) => {
    const [f, d] = s.trim().split(",").map(Number);
    return [f, d];
  }).filter(([f, d]) => !isNaN(f) && !isNaN(d));

  // Speaker profiles: UI fields win per key; loaded-config profile extras
  // (e.g. effort_penalty_db) are preserved per speaker index.
  const profiles = {};
  for (let i = 0; i < speakers; i++) {
    const extra = configExtras.speaker_profiles?.[String(i)] || {};
    const name = document.getElementById(`spk-name-${i}`)?.value || `Speaker ${i}`;
    const minHz = parseFloat(document.getElementById(`spk-min-${i}`)?.value || 20);
    const maxHz = parseFloat(document.getElementById(`spk-max-${i}`)?.value || 20000);
    const trans = parseFloat(document.getElementById(`spk-trans-${i}`)?.value || 10);
    profiles[String(i)] = { ...extra, name, min_hz: minHz, max_hz: maxHz, transition_hz: trans };
  }

  // Input routing
  const inputSpeakers = {};
  for (let i = 0; i < inputs; i++) {
    const val = document.getElementById(`input-spk-${i}`)?.value || "";
    inputSpeakers[String(i)] = val.split(",").map(Number).filter((n) => !isNaN(n));
  }

  // Primary speaker per input (required for anchored target mode)
  const inputPrimary = {};
  for (let i = 0; i < inputs; i++) {
    const val = parseInt(document.getElementById(`input-primary-${i}`)?.value || i);
    inputPrimary[String(i)] = val;
  }

  // Mic weights: UI field wins; then loaded config; then all-1.0
  const micWeights = parseMicWeights(mics);

  // Loaded-config solver keys the UI doesn't expose form the base; UI wins.
  const config = { ...configExtras };
  Object.assign(config, {
    num_speakers: speakers,
    num_mic_positions: mics,
    num_inputs: inputs,
    sample_rate: sampleRate,
    filter_taps: taps,
    target_delay_ms: delay,
    max_boost_db: boost,
    max_cut_db: cut,
    h_smoothing_fraction: hSmooth,
    x_smoothing_fraction: xSmooth,
    fade_out_samples: fade,
    target_mode: targetMode,
    auto_target_level: autoLevel,
    target_curve_points_db: curvePoints,
    speaker_profiles: profiles,
    input_speakers: inputSpeakers,
    mic_weights: micWeights || config.mic_weights || Array(mics).fill(1.0),
    output_format: "wav",
  });
  if (irLen > 0) config.ir_length_samples = irLen; else delete config.ir_length_samples;
  if (config.reference_band_hz === undefined) config.reference_band_hz = [20.0, 200.0];
  if (config.authority_floor_db === undefined) config.authority_floor_db = -30.0;
  if (config.enforce_row_sum_gain_cap === undefined) config.enforce_row_sum_gain_cap = true;
  if (targetMode === "anchored") {
    config.input_primary_speaker = inputPrimary;
  }
  if (fft > 0) config.fft_size = fft; else delete config.fft_size;
  return config;
}

function parseMicWeights(mics) {
  const raw = document.getElementById("cfg-mic-weights").value.trim();
  if (!raw) return null;
  const parts = raw.split(",").map(Number).filter((v) => !isNaN(v) && v >= 0);
  if (parts.length === 0) return null;
  while (parts.length < mics) parts.push(1.0);
  return parts.slice(0, mics);
}

// Solver-relevant config keys that have no UI field. Stashed on JSON load
// and merged into getConfig() so desktop configs round-trip faithfully.
const EXTRA_CONFIG_KEYS = [
  "anchor_phase_smoothing_fraction", "anchor_level_floor_db",
  "enforce_diagonal_cut_floor", "enforce_final_gain_cap",
  "profile_disable_threshold", "profile_transition_penalty",
  "profile_disable_penalty", "null_regularization_strength",
  "remove_denormals", "wrap_energy_warning_ratio", "target_level_linear",
  "reference_band_hz", "authority_floor_db", "enforce_row_sum_gain_cap",
];

async function loadConfigFile(file) {
  if (!file) return;
  try {
    const json = JSON.parse(await file.text());
    applyConfigJson(json);
    log(`Loaded config file ${file.name}`);
    setStatus(`Loaded config from ${file.name}`, "success");
  } catch (err) {
    log(`Config load failed: ${err.message}`, "error");
    setStatus(`Config load failed: ${err.message}`, "error");
  }
}

function applyConfigJson(json) {
  configExtras = {};
  for (const k of EXTRA_CONFIG_KEYS) {
    if (json[k] !== undefined) configExtras[k] = json[k];
  }
  if (json.speaker_profiles && typeof json.speaker_profiles === "object") {
    configExtras.speaker_profiles = json.speaker_profiles;
  }

  const set = (id, v) => { if (v !== undefined && v !== null) document.getElementById(id).value = v; };
  set("cfg-speakers", json.num_speakers);
  set("cfg-mics", json.num_mic_positions);
  set("cfg-inputs", json.num_inputs);
  set("cfg-samplerate", json.sample_rate);
  set("cfg-taps", json.filter_taps);
  set("cfg-fft", json.fft_size ?? 0);
  set("cfg-delay", json.target_delay_ms);
  set("cfg-boost", json.max_boost_db);
  set("cfg-cut", json.max_cut_db);
  set("cfg-hsmooth", json.h_smoothing_fraction);
  set("cfg-xsmooth", json.x_smoothing_fraction);
  set("cfg-fade", json.fade_out_samples);
  set("cfg-irlen", json.ir_length_samples ?? 0);
  set("cfg-target-mode", json.target_mode);
  set("cfg-auto-level", String(json.auto_target_level !== false));
  set("cfg-mic-weights", Array.isArray(json.mic_weights) ? json.mic_weights.join(", ") : "");

  if (Array.isArray(json.target_curve_points_db)) {
    set("cfg-curve", json.target_curve_points_db.map((p) => `${p[0]},${p[1]}`).join("; "));
  } else if (json.target_curve_file) {
    log(`Note: config uses target_curve_file '${json.target_curve_file}' — paste the curve points manually`, "warn");
  }

  // Rebuild dynamic sections from (possibly new) counts, then populate
  buildSpeakerProfiles();
  buildInputRouting();
  const n = parseInt(document.getElementById("cfg-speakers").value);
  for (let i = 0; i < n; i++) {
    const p = json.speaker_profiles?.[String(i)] ?? json.speaker_profiles?.[i];
    if (!p) continue;
    set(`spk-name-${i}`, p.name);
    set(`spk-min-${i}`, p.min_hz);
    set(`spk-max-${i}`, p.max_hz);
    set(`spk-trans-${i}`, p.transition_hz);
  }
  const ni = parseInt(document.getElementById("cfg-inputs").value);
  for (let i = 0; i < ni; i++) {
    const spk = json.input_speakers?.[String(i)] ?? json.input_speakers?.[i];
    if (Array.isArray(spk)) set(`input-spk-${i}`, spk.join(","));
    const prim = json.input_primary_speaker?.[String(i)] ?? json.input_primary_speaker?.[i];
    if (prim !== undefined) set(`input-primary-${i}`, prim);
  }
}

function saveConfigFile() {
  const config = getConfig();
  const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "optimimo_config.json";
  a.click();
  URL.revokeObjectURL(url);
  log("Saved current configuration as optimimo_config.json");
}

// ============================================================
// Speaker profiles UI
// ============================================================
function buildSpeakerProfiles() {
  const n = parseInt(document.getElementById("cfg-speakers").value);
  const container = document.getElementById("speaker-profiles");

  // Save current profiles before rebuilding
  const currentProfiles = [];
  for (let i = 0; i < 20; i++) { // Save up to 20 profiles
    const nameEl = document.getElementById(`spk-name-${i}`);
    if (!nameEl) break;
    currentProfiles.push({
      name: nameEl.value,
      min: parseFloat(document.getElementById(`spk-min-${i}`)?.value || 20),
      max: parseFloat(document.getElementById(`spk-max-${i}`)?.value || 20000),
      trans: parseFloat(document.getElementById(`spk-trans-${i}`)?.value || 10),
    });
  }

  container.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "speaker-grid";
  const defaults = [
    { name: "Sub", min: 10, max: 120, trans: 12 },
    { name: "Main L", min: 80, max: 20000, trans: 24 },
    { name: "Main R", min: 80, max: 20000, trans: 24 },
  ];
  for (let i = 0; i < n; i++) {
    // Use saved profile if available, else default
    const d = currentProfiles[i] || defaults[i] || { name: `Speaker ${i}`, min: 20, max: 20000, trans: 10 };
    grid.innerHTML += `
      <div class="speaker-card">
        <h4>Speaker ${i}</h4>
        <div class="form-row">
          <div class="form-group"><label>Name</label><input type="text" id="spk-name-${i}" value="${d.name}"></div>
          <div class="form-group"><label>Min Hz</label><input type="number" id="spk-min-${i}" value="${d.min}"></div>
          <div class="form-group"><label>Max Hz</label><input type="number" id="spk-max-${i}" value="${d.max}"></div>
          <div class="form-group"><label>Transition</label><input type="number" id="spk-trans-${i}" value="${d.trans}"></div>
        </div>
      </div>`;
  }
  container.appendChild(grid);
}

function buildInputRouting() {
  const inputs = parseInt(document.getElementById("cfg-inputs").value);
  const speakers = parseInt(document.getElementById("cfg-speakers").value);
  const container = document.getElementById("input-routing");
  container.innerHTML = "";
  for (let i = 0; i < inputs; i++) {
    const defaultVal = Array.from({ length: speakers }, (_, s) => s).join(",");
    const defaultPrimary = Math.min(i, speakers - 1);
    container.innerHTML += `
      <div class="form-row" style="margin-bottom:4px">
        <div class="form-group"><label>Input ${i} → speakers</label>
        <input type="text" id="input-spk-${i}" value="${defaultVal}" style="width:200px"></div>
        <div class="form-group"><label>Primary</label>
        <input type="number" id="input-primary-${i}" value="${defaultPrimary}" min="0" max="${speakers - 1}" style="width:60px"></div>
      </div>`;
  }
}

// Initialize on load
buildSpeakerProfiles();
buildInputRouting();
loadRewProxyUrl();
updateRewBridgeStatus();
document.getElementById("cfg-speakers").addEventListener("change", () => { buildSpeakerProfiles(); buildInputRouting(); });
document.getElementById("cfg-inputs").addEventListener("change", buildInputRouting);

// ============================================================
// Input source selection
// ============================================================
function setInputSource(src) {
  ["rew", "file", "synthetic"].forEach((s) => {
    document.getElementById("panel-" + s)?.classList.add("hidden");
    document.getElementById("btn-src-" + s)?.classList.remove("btn");
    document.getElementById("btn-src-" + s)?.classList.add("btn-secondary");
  });
  const panelId = src === "synthetic" ? "panel-synthetic" : "panel-" + src;
  document.getElementById(panelId)?.classList.remove("hidden");
  const btnId = src === "synthetic" ? "btn-src-synth" : "btn-src-" + src;
  document.getElementById(btnId)?.classList.remove("btn-secondary");
  document.getElementById(btnId)?.classList.add("btn");
}

// ============================================================
// REW Import
// ============================================================
// REW API requests can reach the local REW instance three ways, tried in
// order:
//   1. REW Proxy URL field set        → companion serve.py on this machine
//   2. REW Bridge extension installed → chrome.runtime messaging (hosted app)
//   3. Otherwise                      → same-origin /rew (local serve.py)
function rewProxyBase() {
  return (document.getElementById("rew-proxy-url")?.value || "").trim().replace(/\/+$/, "");
}

function saveRewProxyUrl() {
  try { localStorage.setItem("rewProxyUrl", rewProxyBase()); } catch {}
}

function loadRewProxyUrl() {
  try {
    const v = localStorage.getItem("rewProxyUrl");
    if (v) document.getElementById("rew-proxy-url").value = v;
  } catch {}
}

function rewBridgeAvailable() {
  return typeof chrome !== "undefined" && !!chrome.runtime && !!chrome.runtime.sendMessage;
}

function updateRewBridgeStatus() {
  const el = document.getElementById("rew-bridge-status");
  if (!el) return;
  if (rewBridgeAvailable()) {
    el.innerHTML = `<span style="color:var(--success)">REW Bridge extension: installed</span>`;
    return;
  }
  const store = REW_BRIDGE_STORE_URL
    ? `<a href="${REW_BRIDGE_STORE_URL}" target="_blank" rel="noopener">Install from the Chrome Web Store</a> (recommended)`
    : `Chrome Web Store listing coming soon`;
  el.innerHTML = `REW Bridge extension: not detected — ${store}; ` +
    `or <a href="extension.zip">download extension.zip</a>, unzip, enable Developer mode in chrome://extensions, and "Load unpacked".`;
}

async function rewFetchJson(path) {
  const base = rewProxyBase();
  if (base) {
    const resp = await fetch(`${base}/rew${path}`, { signal: AbortSignal.timeout(30000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }
  if (rewBridgeAvailable()) {
    const resp = await Promise.race([
      chrome.runtime.sendMessage(REW_BRIDGE_EXT_ID, { type: "rew_fetch", path }),
      new Promise((_, reject) => setTimeout(() => reject(new Error("REW Bridge timeout")), 30000)),
    ]);
    if (!resp) throw new Error("REW Bridge extension not responding");
    if (!resp.ok) throw new Error(resp.error || `HTTP ${resp.status} from REW`);
    return JSON.parse(resp.body);
  }
  const resp = await fetch(`/rew${path}`, { signal: AbortSignal.timeout(30000) });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

function rewFetchHint() {
  const onLoopback = /^(https?:\/\/)?(127\.0\.0\.1|localhost|\[::1\])/i.test(location.origin);
  if (!onLoopback && !rewProxyBase() && !rewBridgeAvailable()) {
    return 'Install the OptiMIMO REW Bridge extension (webapp/extension), or run "python3 serve.py 8878" and set the REW Proxy URL.';
  }
  return "Is REW running with API enabled?";
}

async function fetchRewMeasurements() {
  const base = rewProxyBase();
  const via = base ? base : rewBridgeAvailable() ? "REW Bridge extension" : "same-origin proxy";
  log(`Fetching REW measurements via ${via}...`);
  try {
    const data = await rewFetchJson("/measurements");
    rewMeasurements = [];
    for (const [idx, summary] of Object.entries(data)) {
      if (!summary.uuid) continue;
      rewMeasurements.push({
        index: idx,
        uuid: summary.uuid,
        title: summary.title || `Measurement ${idx}`,
        sample_rate: summary.sampleRate || null,
        has_ir: !!(summary.timeOfIRPeakSeconds || summary.timeOfIRStartSeconds || summary.cumulativeIRShiftSeconds),
        peak_time: summary.timeOfIRPeakSeconds || null,
      });
    }
    rewMeasurements.sort((a, b) => parseInt(a.index) - parseInt(b.index));
    log(`Found ${rewMeasurements.length} REW measurements`);
    renderRewAssignments();
  } catch (err) {
    log(`REW fetch failed: ${err.message}`, "error");
    setStatus(`REW not reachable. ${rewFetchHint()}`, "error");
  }
}

function renderRewAssignments() {
  const speakers = parseInt(document.getElementById("cfg-speakers").value);
  const mics = parseInt(document.getElementById("cfg-mics").value);
  const container = document.getElementById("rew-assignments");
  const measContainer = document.getElementById("rew-measurements");

  // Show available measurements
  measContainer.innerHTML = `<h3>Available (${rewMeasurements.length})</h3>` +
    rewMeasurements.map((m) =>
      `<div style="font-size:12px;padding:2px 0">${m.index}: ${m.title} ${m.has_ir ? "" : "(no IR)"}</div>`
    ).join("");

  // Build assignment grid
  let html = "<h3>Assign to Grid</h3><div class='measurement-grid'>";
  for (let m = 0; m < mics; m++) {
    for (let s = 0; s < speakers; s++) {
      const opts = rewMeasurements.map((r) =>
        `<option value="${r.uuid}">${r.index}: ${r.title}</option>`
      ).join("");
      html += `<div class="measurement-slot">
        <div class="label">Mic ${m} / Speaker ${s}</div>
        <select id="rew-assign-${m}-${s}"><option value="">—</option>${opts}</select>
      </div>`;
    }
  }
  html += "</div>";
  container.innerHTML = html;
  document.getElementById("btn-rew-import").classList.remove("hidden");
}

async function importFromRew() {
  const speakers = parseInt(document.getElementById("cfg-speakers").value);
  const mics = parseInt(document.getElementById("cfg-mics").value);

  // Collect assignments
  const assignments = [];
  for (let m = 0; m < mics; m++) {
    for (let s = 0; s < speakers; s++) {
      const uuid = document.getElementById(`rew-assign-${m}-${s}`)?.value;
      if (!uuid) { setStatus(`Missing assignment for mic ${m}, speaker ${s}`, "warn"); return; }
      const meas = rewMeasurements.find((r) => r.uuid === uuid);
      assignments.push({ mic: m, speaker: s, uuid, title: meas?.title || uuid, peak_time: meas?.peak_time });
    }
  }

  log(`Importing ${assignments.length} IRs from REW...`);
  setStatus("Fetching impulse responses from REW...", "info");

  try {
    const irArrays = [];
    let sampleRate = null;
    let maxLen = 0;

    for (const a of assignments) {
      const data = await rewFetchJson(`/measurements/${encodeURIComponent(a.uuid)}/impulse-response?normalised=false`)
        .catch((err) => { throw new Error(`${err.message} for ${a.title}`); });
      const encoded = data.data || data.Data;
      if (!encoded) throw new Error(`No IR data for ${a.title}`);

      // Decode base64 big-endian float32
      const raw = atob(encoded);
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
      const samples = new Float64Array(bytes.length / 4);
      const view = new DataView(bytes.buffer);
      for (let i = 0; i < samples.length; i++) {
        samples[i] = view.getFloat32(i * 4, false); // big-endian
      }

      // Sanity check: IR should have some energy and no NaN/Inf
      let maxAbs = 0;
      let hasNaN = false;
      for (let i = 0; i < samples.length; i++) {
        const v = Math.abs(samples[i]);
        if (Number.isNaN(v) || !Number.isFinite(v)) { hasNaN = true; break; }
        if (v > maxAbs) maxAbs = v;
      }
      if (hasNaN) throw new Error(`IR data for ${a.title} contains NaN or Inf`);
      if (maxAbs === 0) throw new Error(`IR data for ${a.title} is all zeros`);
      if (maxAbs > 100) log(`  Warning: ${a.title} has very high peak (${maxAbs.toFixed(1)}), may be normalized incorrectly`, "warn");

      const fs = data.sampleRate || data.sample_rate || data.fs;
      if (fs) {
        if (!sampleRate) {
          sampleRate = fs;
        } else if (fs !== sampleRate) {
          throw new Error(`Sample rate mismatch: '${a.title}' is ${fs} Hz but others are ${sampleRate} Hz. All measurements must share one sample rate.`);
        }
      }
      // Extract start_time: where the first sample sits on REW's timing axis.
      // Critical for correct peak alignment — REW returns ~1s of pre-peak lead-in.
      const startTime = data.startTime ?? data.start_time ?? data.timeOfIRStartSeconds ?? 0.0;
      maxLen = Math.max(maxLen, samples.length);
      irArrays.push({ ...a, samples, startTime });
      log(`  ${a.title}: ${samples.length} samples @ ${fs} Hz, start=${startTime.toFixed(4)}s`);
    }

    if (!sampleRate) sampleRate = parseInt(document.getElementById("cfg-samplerate").value);

    // Align by peak time and pad to same length.
    // Mirrors the Python rew.py logic: use REW's robust peak_time (on the
    // timing-reference axis) combined with start_time to find the peak index
    // within each returned IR. Fall back to argmax only when peak_time is
    // unavailable.
    const preRollSamples = Math.round(0.020 * sampleRate); // 20ms pre-roll

    // Compute per-IR peak indices and absolute peak times
    const peakInfo = irArrays.map((ir) => {
      let peakIdx, absPeakTime;
      if (ir.peak_time != null) {
        // Use REW's robust peak time: index = (peak_time - start_time) * fs
        peakIdx = Math.round((ir.peak_time - ir.startTime) * sampleRate);
        peakIdx = Math.min(Math.max(peakIdx, 0), Math.max(ir.samples.length - 1, 0));
        absPeakTime = ir.peak_time;
      } else {
        // Fallback: argmax (unreliable for subwoofers)
        peakIdx = 0;
        let peakVal = 0;
        for (let i = 0; i < ir.samples.length; i++) {
          if (Math.abs(ir.samples[i]) > peakVal) { peakVal = Math.abs(ir.samples[i]); peakIdx = i; }
        }
        absPeakTime = ir.startTime + peakIdx / sampleRate;
      }
      return { ...ir, peakIdx, absPeakTime };
    });

    // Find the earliest arrival across all IRs
    const minPeak = Math.min(...peakInfo.map((p) => p.absPeakTime));

    // Compute per-IR offset: where to place each IR in the output array.
    // Positive = prepend zeros; negative = trim leading samples.
    const aligned = peakInfo.map((ir) => {
      const target = preRollSamples + Math.round((ir.absPeakTime - minPeak) * sampleRate);
      const offset = target - ir.peakIdx;
      return { ...ir, offset };
    });

    // Compute final length
    let finalLen = 0;
    for (const ir of aligned) {
      const end = Math.max(0, ir.offset) + ir.samples.length - Math.max(0, -ir.offset);
      finalLen = Math.max(finalLen, end);
    }
    finalLen = Math.max(finalLen, 4096);

    // Build flat arrays
    const result = [];
    for (const ir of aligned) {
      const padded = new Float64Array(finalLen);
      const start = Math.max(0, ir.offset);
      const srcStart = Math.max(0, -ir.offset);
      const copyLen = Math.min(ir.samples.length - srcStart, finalLen - start);
      for (let i = 0; i < copyLen; i++) {
        padded[start + i] = ir.samples[srcStart + i];
      }
      result.push(padded);
    }

    irData = result;
    irShape = [mics, speakers, finalLen];
    document.getElementById("cfg-samplerate").value = sampleRate;

    // Record per-(mic, speaker) arrival times on the aligned IR timeline.
    // The solver uses these (via config.measurements[].arrival_ms) to
    // de-rotate H smoothing and the anchored target — far more reliable
    // than argmax for subwoofers. offset+peakIdx is the peak position in
    // the aligned array; cropIrData slices from sample 0, so no offset.
    irArrivals = aligned.map((ir) => ({
      mic: ir.mic,
      speaker: ir.speaker,
      arrival_ms: ((ir.offset + ir.peakIdx) / sampleRate) * 1000.0,
    }));

    log(`Imported ${result.length} IRs, ${finalLen} samples each @ ${sampleRate} Hz`);
    cropIrData();
    const [, , effLen] = irShape;
    setStatus(`Loaded ${result.length} measurements from REW (${effLen} samples @ ${sampleRate} Hz)`, "success");
    updateMeasurementSummary();
  } catch (err) {
    log(`REW import failed: ${err.message}`, "error");
    setStatus(`REW import failed: ${err.message}. ${rewFetchHint()}`, "error");
  }
}

// ============================================================
// IR cropping — the solver's filter must be longer than the IR
// content it inverts; long reverb tails also inflate fft_size.
// Crop to cfg-irlen samples (0 = keep full length).
// ============================================================
function cropIrData() {
  if (!irData || !irShape) return;
  const crop = parseInt(document.getElementById("cfg-irlen")?.value) || 0;
  if (crop <= 0) return;
  const [m, s, len] = irShape;
  if (len <= crop) return;
  irData = irData.map((ir) => ir.slice(0, crop));
  irShape = [m, s, crop];
  log(`Cropped IRs to ${crop} samples (${(crop / parseInt(document.getElementById("cfg-samplerate").value) * 1000).toFixed(0)} ms)`);
}

// ============================================================
// File Upload
// ============================================================
const fileDrop = document.getElementById("file-drop");
const fileInput = document.getElementById("file-input");

fileDrop.addEventListener("click", () => fileInput.click());
fileDrop.addEventListener("dragover", (e) => { e.preventDefault(); fileDrop.classList.add("dragover"); });
fileDrop.addEventListener("dragleave", () => fileDrop.classList.remove("dragover"));
fileDrop.addEventListener("drop", (e) => {
  e.preventDefault();
  fileDrop.classList.remove("dragover");
  handleFiles(e.dataTransfer.files);
});
fileInput.addEventListener("change", () => handleFiles(fileInput.files));

async function handleFiles(files) {
  const speakers = parseInt(document.getElementById("cfg-speakers").value);
  const mics = parseInt(document.getElementById("cfg-mics").value);
  const expected = speakers * mics;

  if (files.length !== expected) {
    setStatus(`Expected ${expected} WAV files (${mics} mics × ${speakers} speakers), got ${files.length}`, "warn");
  }

  log(`Loading ${files.length} WAV files...`);
  const arrays = [];
  let sampleRate = null;
  let maxLen = 0;

  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    const buf = await f.arrayBuffer();
    const { rate, samples } = parseWav(buf);
    if (!sampleRate) sampleRate = rate;
    if (rate !== sampleRate) {
      log(`Warning: ${f.name} has sample rate ${rate}, expected ${sampleRate}`, "warn");
    }
    maxLen = Math.max(maxLen, samples.length);
    arrays.push({ name: f.name, samples, rate });
    log(`  ${f.name}: ${samples.length} samples @ ${rate} Hz`);
  }

  // Pad to same length
  const result = arrays.map((a) => {
    const padded = new Float64Array(maxLen);
    padded.set(a.samples);
    return padded;
  });

  irData = result;
  irShape = [mics, speakers, maxLen];
  irArrivals = null; // no reliable arrival info for manual files
  if (sampleRate) document.getElementById("cfg-samplerate").value = sampleRate;

  log(`Loaded ${result.length} files, ${maxLen} samples each @ ${sampleRate} Hz`);
  cropIrData();
  const [, , effLen] = irShape;
  setStatus(`Loaded ${result.length} WAV files (${effLen} samples @ ${sampleRate} Hz)`, "success");
  updateMeasurementSummary();
}

// Minimal WAV parser (PCM 16/24/32 + float32)
function parseWav(buffer) {
  const view = new DataView(buffer);
  if (view.getUint32(0, false) !== 0x52494646) throw new Error("Not a RIFF file");
  if (view.getUint32(8, false) !== 0x57415645) throw new Error("Not a WAVE file");

  let offset = 12;
  let formatTag, channels, sampleRate, bitsPerSample, dataBytes;

  while (offset + 8 <= buffer.byteLength) {
    const chunkId = view.getUint32(offset, false);
    const chunkLen = view.getUint32(offset + 4, true);
    if (chunkId === 0x666d7420) { // "fmt "
      formatTag = view.getUint16(offset + 8, true);
      channels = view.getUint16(offset + 10, true);
      sampleRate = view.getUint32(offset + 12, true);
      bitsPerSample = view.getUint16(offset + 22, true);
    } else if (chunkId === 0x64617461) { // "data"
      dataBytes = new Uint8Array(buffer, offset + 8, chunkLen);
      break;
    }
    offset += 8 + chunkLen + (chunkLen % 2);
  }

  if (!dataBytes) throw new Error("No data chunk found");

  let samples;
  if (formatTag === 1 && bitsPerSample === 16) {
    const i16 = new Int16Array(dataBytes.buffer, dataBytes.byteOffset, dataBytes.length / 2);
    samples = new Float64Array(i16.length);
    for (let i = 0; i < i16.length; i++) samples[i] = i16[i] / 32768.0;
  } else if (formatTag === 1 && bitsPerSample === 32) {
    const i32 = new Int32Array(dataBytes.buffer, dataBytes.byteOffset, dataBytes.length / 4);
    samples = new Float64Array(i32.length);
    for (let i = 0; i < i32.length; i++) samples[i] = i32[i] / 2147483648.0;
  } else if (formatTag === 3 && bitsPerSample === 32) {
    const f32 = new Float32Array(dataBytes.buffer, dataBytes.byteOffset, dataBytes.length / 4);
    samples = new Float64Array(f32.length);
    for (let i = 0; i < f32.length; i++) samples[i] = f32[i];
  } else if (formatTag === 1 && bitsPerSample === 24) {
    const n = dataBytes.length / 3;
    samples = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      const b0 = dataBytes[i * 3], b1 = dataBytes[i * 3 + 1], b2 = dataBytes[i * 3 + 2];
      let val = b0 | (b1 << 8) | (b2 << 16);
      if (val >= 0x800000) val -= 0x1000000;
      samples[i] = val / 8388608.0;
    }
  } else {
    throw new Error(`Unsupported WAV format: tag=${formatTag} bits=${bitsPerSample}`);
  }

  // Take first channel if multi-channel
  if (channels > 1) {
    const mono = new Float64Array(samples.length / channels);
    for (let i = 0; i < mono.length; i++) mono[i] = samples[i * channels];
    samples = mono;
  }

  return { rate: sampleRate, samples };
}

// ============================================================
// Synthetic Data
// ============================================================
function generateSynthetic() {
  const speakers = parseInt(document.getElementById("cfg-speakers").value);
  const mics = parseInt(document.getElementById("cfg-mics").value);
  const length = parseInt(document.getElementById("synth-irlen").value);
  const sampleRate = parseInt(document.getElementById("cfg-samplerate").value);

  log(`Generating synthetic IRs: ${mics}×${speakers}×${length} @ ${sampleRate} Hz`);

  // Simple seeded RNG (mulberry32)
  let seed = 1234;
  function rng() {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  const result = [];
  for (let m = 0; m < mics; m++) {
    for (let s = 0; s < speakers; s++) {
      const ir = new Float64Array(length);
      const directDelay = 48 + 9 * m + 13 * s;
      if (directDelay < length) ir[directDelay] += 0.8 + 0.2 * rng();
      for (let ref = 1; ref < 8; ref++) {
        const delay = directDelay + ref * (80 + 11 * m + 5 * s);
        if (delay >= length) break;
        const sign = ref % 2 ? -1 : 1;
        ir[delay] += sign * (0.25 / ref) * (0.7 + 0.5 * rng());
      }
      // Modal tail
      const modeHz = 37 + 4 * m + 3 * s;
      const polarity = (m + s) % 2 ? -1 : 1;
      for (let i = 0; i < length; i++) {
        const t = i / sampleRate;
        ir[i] += polarity * 0.04 * Math.sin(2 * Math.PI * modeHz * t) * Math.exp(-t / 0.18);
      }
      result.push(ir);
    }
  }

  irData = result;
  irShape = [mics, speakers, length];
  irArrivals = null; // synthetic data has no measured arrivals
  log(`Generated ${result.length} synthetic IRs`);
  cropIrData();
  const [, , effLen] = irShape;
  setStatus(`Generated ${result.length} synthetic IRs (${effLen} samples)`, "success");
  updateMeasurementSummary();
}

// ============================================================
// Measurement summary
// ============================================================
function updateMeasurementSummary() {
  const el = document.getElementById("measurement-summary");
  if (!irData || !irShape) {
    el.textContent = "No measurements loaded.";
    return;
  }
  const [m, s, len] = irShape;
  const sr = document.getElementById("cfg-samplerate").value;
  el.innerHTML = `<table>
    <tr><th>Grid</th><td>${m} mics × ${s} speakers</td></tr>
    <tr><th>IR Length</th><td>${len} samples (${(len / sr * 1000).toFixed(1)} ms)</td></tr>
    <tr><th>Sample Rate</th><td>${sr} Hz</td></tr>
    <tr><th>Arrays</th><td>${irData.length}</td></tr>
  </table>`;
}

// ============================================================
// Worker management
// ============================================================
function ensureWorker() {
  if (worker) return;
  worker = new Worker("solver-worker.js?v=" + Date.now());
  worker.onmessage = (ev) => {
    const msg = ev.data;
    if (msg.type === "progress") {
      if (msg.stage === "ready") {
        workerReady = true;
        log(`Pyodide ready (boot: ${msg.boot_ms?.toFixed(0)}ms, numpy: ${msg.package_load_ms?.toFixed(0)}ms, optimimo: ${msg.optimimo_load_ms?.toFixed(0)}ms, numpy_version: ${msg.numpy_version || "unknown"})`);
        setStatus("Solver engine ready", "success");
        document.getElementById("btn-solve").disabled = false;
      } else if (msg.stage === "solve_progress") {
        const pct = Math.round((msg.fraction || 0) * 100);
        document.getElementById("progress-bar").style.width = pct + "%";
        document.getElementById("progress-label").textContent = `${msg.stage} — ${pct}%`;
      } else if (msg.stage === "stderr") {
        log(`[py] ${msg.text}`, "warn");
      } else {
        log(`[${msg.stage}] ${msg.boot_ms ? `boot=${msg.boot_ms.toFixed(0)}ms` : ""}${msg.package_load_ms ? ` numpy=${msg.package_load_ms.toFixed(0)}ms` : ""}${msg.optimimo_load_ms ? ` optimimo=${msg.optimimo_load_ms.toFixed(0)}ms` : ""}`);
      }
    } else if (msg.type === "result") {
      solving = false;
      document.getElementById("btn-solve").classList.remove("hidden");
      document.getElementById("btn-cancel").classList.add("hidden");
      document.getElementById("progress-bar").style.width = "100%";
      document.getElementById("progress-label").textContent = "Done";
      solveResult = msg;
      displayResults(msg);
      log(`Solve complete in ${msg.total_ms?.toFixed(0)}ms`);
      setStatus("Solve complete", "success");
    } else if (msg.type === "cancelled") {
      solving = false;
      document.getElementById("btn-solve").classList.remove("hidden");
      document.getElementById("btn-cancel").classList.add("hidden");
      document.getElementById("progress-label").textContent = "Cancelled";
      log(`Solve cancelled after ${msg.total_ms?.toFixed(0)}ms (worker still alive)`);
      setStatus("Solve cancelled. Worker is ready for next run.", "warn");
    } else if (msg.type === "error") {
      solving = false;
      estimating = false;
      document.getElementById("btn-solve").classList.remove("hidden");
      document.getElementById("btn-cancel").classList.add("hidden");
      log(`ERROR: ${msg.message}`, "error");
      setStatus(`Solve failed: ${msg.message}`, "error");
    } else if (msg.type === "delay_estimate") {
      handleDelayEstimate(msg.estimate);
    }
  };
  worker.onerror = (err) => {
    log(`Worker error: ${err.message}`, "error");
    setStatus("Worker error", "error");
  };
  log("Initializing Pyodide worker...");
  worker.postMessage({ type: "init" });
}

// ============================================================
// Solve
// ============================================================
function startSolve() {
  if (!irData || !irShape) {
    setStatus("Load measurements first (Measurements tab)", "warn");
    return;
  }
  if (solving) return;

  // Hard guard: the filter must be longer than the target delay, otherwise
  // the ideal inverse needs negative-time taps, wraps around the circular
  // FFT buffer, and produces garbage filters (observed: everything collapses
  // to low-frequency mud). Require margin; the pipeline default margins are
  // 10 ms (anchored) / 20 ms (flat).
  const config = getConfig();
  if (irArrivals) config.measurements = irArrivals;
  const delaySamples = (config.target_delay_ms / 1000.0) * config.sample_rate;
  const minTaps = Math.ceil(delaySamples);
  if (config.filter_taps < minTaps) {
    const filterMs = (config.filter_taps / config.sample_rate) * 1000;
    setStatus(
      `Filter too short: ${config.filter_taps} taps = ${filterMs.toFixed(1)} ms @ ${config.sample_rate} Hz, ` +
      `but target delay is ${config.target_delay_ms} ms (needs ≥ ${minTaps} taps). ` +
      `Increase Filter Taps or reduce Target Delay.`,
      "error"
    );
    log(`Refusing to solve: filter_taps (${config.filter_taps}) < target delay in samples (${minTaps})`, "error");
    return;
  }

  ensureWorker();
  if (!workerReady) {
    setStatus("Waiting for Pyodide to boot...", "info");
    const check = setInterval(() => {
      if (workerReady) { clearInterval(check); startSolve(); }
    }, 500);
    return;
  }

  solving = true;
  document.getElementById("btn-solve").classList.add("hidden");
  document.getElementById("btn-cancel").classList.remove("hidden");
  document.getElementById("progress-bar").style.width = "0%";
  document.getElementById("progress-label").textContent = "Starting...";

  // Update run summary
  const [m, s, len] = irShape;
  document.getElementById("run-summary").innerHTML = `<table>
    <tr><th>Grid</th><td>${m}×${s}×${len}</td></tr>
    <tr><th>Filter taps</th><td>${config.filter_taps}</td></tr>
    <tr><th>FFT size</th><td>${config.fft_size || "auto"}</td></tr>
    <tr><th>Smoothing</th><td>H=${config.h_smoothing_fraction} X=${config.x_smoothing_fraction}</td></tr>
    <tr><th>Target</th><td>${config.target_mode}</td></tr>
  </table>`;

  // Switch to Run tab
  document.querySelector('.tab[data-tab="run"]').click();

  log(`Starting solve: ${m}×${s}×${len}, taps=${config.filter_taps}`);

  // Create SharedArrayBuffer for graceful cancellation
  cancelBuffer = new SharedArrayBuffer(4);
  new Int32Array(cancelBuffer)[0] = 0;

  worker.postMessage({ type: "solve", irData, irShape, config, cancelBuffer });
}

function cancelSolve() {
  if (!solving || !worker) return;

  if (cancelBuffer) {
    // Graceful: set the SharedArrayBuffer flag; Python raises SolveCancelled
    // at the next checkpoint. Worker stays alive.
    log("Requesting graceful cancellation...");
    Atomics.store(new Int32Array(cancelBuffer), 0, 1);
  } else {
    // Fallback: hard kill (no SharedArrayBuffer support)
    log("Cancelling (terminating worker)...");
    worker.terminate();
    worker = null;
    workerReady = false;
    solving = false;
    document.getElementById("btn-solve").classList.remove("hidden");
    document.getElementById("btn-cancel").classList.add("hidden");
    document.getElementById("progress-label").textContent = "Cancelled";
    setStatus("Solve cancelled. Pyodide will re-boot on next run.", "warn");
  }
}

// ============================================================
// Results display
// ============================================================
function displayResults(msg) {
  const d = msg.diagnostics || {};
  const stageMs = msg.stage_ms || {};

  let html = `<div class="results-grid">
    <div>
      <h3>Timing</h3>
      <table>
        <tr><th>Total solve</th><td>${msg.total_ms?.toFixed(1)} ms</td></tr>
        ${Object.entries(stageMs).map(([s, ms]) =>
          `<tr><th>${s}</th><td>${ms.toFixed(1)} ms</td></tr>`
        ).join("")}
      </table>
    </div>
    <div>
      <h3>Diagnostics</h3>
      <table>
        <tr><th>FFT size</th><td>${d.fft_size}</td></tr>
        <tr><th>Filter taps</th><td>${d.filter_taps}</td></tr>
        <tr><th>FIR shape</th><td>${JSON.stringify(msg.firs_shape)}</td></tr>
        <tr><th>Max filter gain</th><td>${d.max_filter_gain_db?.toFixed(2)} dB</td></tr>
        <tr><th>Max row-sum gain</th><td>${d.max_row_sum_gain_db?.toFixed(2)} dB</td></tr>
        <tr><th>Checksum</th><td>${msg.firs_checksum?.toExponential(6)}</td></tr>
      </table>
    </div>
  </div>`;

  if (d.warnings && d.warnings.length > 0) {
    html += `<h3>Warnings</h3><ul>${d.warnings.map((w) => `<li style="color:var(--warn)">${w}</li>`).join("")}</ul>`;
  }

  document.getElementById("results-content").innerHTML = html;
  document.getElementById("download-card").classList.remove("hidden");

  // Switch to Results tab first so canvases have layout when drawn
  document.querySelector('.tab[data-tab="results"]').click();

  // Render plots (predicted vs target, filter magnitudes) if provided
  renderPlots(msg.plots);

  // Write to hidden #out for CDP scraping
  document.getElementById("out").textContent = JSON.stringify({
    status: "complete",
    total_ms: msg.total_ms,
    stage_ms: stageMs,
    firs_shape: msg.firs_shape,
    firs_checksum: msg.firs_checksum,
    diagnostics: d,
  });
}

// ============================================================
// Delay estimation — group-delay analysis via the worker
// ============================================================
let estimating = false;

function estimateDelay() {
  if (!irData || !irShape) {
    setStatus("Load measurements first (Measurements tab)", "warn");
    return;
  }
  if (solving || estimating) return;
  ensureWorker();
  if (!workerReady) {
    setStatus("Waiting for Pyodide to boot...", "info");
    const check = setInterval(() => {
      if (workerReady) { clearInterval(check); estimateDelay(); }
    }, 500);
    return;
  }
  estimating = true;
  setStatus("Estimating target delay from measured group delay...", "info");
  log("Estimating target delay (group-delay analysis per speaker band)...");
  const config = getConfig();
  if (irArrivals) config.measurements = irArrivals;
  worker.postMessage({ type: "estimate_delay", irData, irShape, config });
}

function handleDelayEstimate(e) {
  estimating = false;
  const rec = Math.ceil(e.recommended_ms);
  document.getElementById("cfg-delay").value = rec;
  log(`Delay estimate: worst group delay ${e.max_group_delay_ms.toFixed(1)} ms + ${e.margin_ms.toFixed(0)} ms ${e.target_mode}-mode margin → ${e.recommended_ms.toFixed(1)} ms; set ${rec} ms`);
  if (e.h_smoothing_applied) log(`  (estimate reflects ${e.h_smoothing_fraction}/oct H smoothing)`);
  for (const issue of e.issues || []) log(`  issue: ${issue}`, "warn");
  if (e.constrained_by_fft) {
    setStatus(`Recommended ${rec} ms exceeds the ${e.max_delay_budget_ms.toFixed(0)} ms budget from current FFT/taps — increase FFT size or reduce taps`, "warn");
  } else {
    setStatus(`Target delay set to ${rec} ms (worst group delay ${e.max_group_delay_ms.toFixed(1)} ms)`, "success");
  }
}

// ============================================================
// Plots — canvas line plots of predicted/target/filter magnitudes
// ============================================================
const PLOT_COLORS = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff", "#39c5cf", "#ff9f43", "#ff6b81", "#7ee787", "#ffa198", "#a5d6ff", "#56d364"];
const PLOT_X_TICKS = [10, 15, 20, 30, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000];
const PLOT_X_LABELS = { 20: "20", 50: "50", 100: "100", 200: "200", 500: "500", 1000: "1k", 2000: "2k", 5000: "5k", 10000: "10k", 20000: "20k" };

function renderPlots(plots) {
  const container = document.getElementById("plots-container");
  container.innerHTML = "";
  if (!plots) return;
  if (plots.error) { log(`Plot generation failed: ${plots.error}`, "warn"); return; }
  const inputs = [...new Set(plots.curves.filter((c) => c.kind === "predicted").map((c) => c.input))].sort((a, b) => a - b);
  for (const k of inputs) {
    const predSeries = plots.curves
      .filter((c) => c.input === k && (c.kind === "predicted" || c.kind === "target"))
      .map((c) => ({ label: `${c.kind === "target" ? "target" : "pred"} mic ${c.mic}`, db: c.db, group: c.mic, dash: c.kind === "target" }));
    container.appendChild(makePlotCard(`Predicted vs Target — input ${k}`, plots.freqs, predSeries));

    const filtSeries = plots.curves
      .filter((c) => c.input === k && c.kind === "filter")
      .map((c) => ({ label: plots.speaker_names?.[c.speaker] ?? `spk ${c.speaker}`, db: c.db, group: c.speaker, dash: false }));
    container.appendChild(makePlotCard(`Filter Magnitude — input ${k}`, plots.freqs, filtSeries));
  }
}

function makePlotCard(title, freqs, series) {
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<h2>${title}</h2>`;
  const canvas = document.createElement("canvas");
  canvas.style.width = "100%";
  canvas.style.maxWidth = "720px";
  canvas.style.height = "260px";
  card.appendChild(canvas);
  drawLinePlot(canvas, freqs, series);
  return card;
}

function drawLinePlot(canvas, freqs, series) {
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth || 720;
  const H = canvas.clientHeight || 260;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const padL = 44, padR = 8, padT = 8, padB = 22, legendH = Math.ceil(series.length / 4) * 13 + 4;
  const plotW = W - padL - padR, plotH = H - padT - padB - legendH;
  const f0 = freqs[0], f1 = freqs[freqs.length - 1];
  const lf0 = Math.log10(f0), lf1 = Math.log10(f1);
  const xOf = (f) => padL + ((Math.log10(f) - lf0) / (lf1 - lf0)) * plotW;

  let yMin = Infinity, yMax = -Infinity;
  for (const s of series) for (const v of s.db) { if (v < yMin) yMin = v; if (v > yMax) yMax = v; }
  if (!isFinite(yMin)) { yMin = -60; yMax = 0; }
  const yPad = Math.max(3, (yMax - yMin) * 0.08);
  yMin -= yPad; yMax += yPad;
  const yOf = (v) => padT + (1 - (v - yMin) / (yMax - yMin)) * plotH;

  const css = getComputedStyle(document.documentElement);
  const colBorder = css.getPropertyValue("--border").trim() || "#30363d";
  const colText = css.getPropertyValue("--text-dim").trim() || "#8b949e";

  // Grid + x ticks
  ctx.strokeStyle = colBorder; ctx.fillStyle = colText; ctx.lineWidth = 1;
  ctx.font = "10px " + (css.getPropertyValue("--mono") || "monospace");
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (const t of PLOT_X_TICKS) {
    if (t < f0 || t > f1) continue;
    const x = xOf(t);
    ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, padT + plotH); ctx.stroke();
    if (PLOT_X_LABELS[t]) ctx.fillText(PLOT_X_LABELS[t], x, padT + plotH + 5);
  }
  // Y ticks (nice step)
  const range = yMax - yMin;
  const step = [1, 2, 5, 10, 20, 50].find((s) => range / s <= 8) || 100;
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (let v = Math.ceil(yMin / step) * step; v <= yMax; v += step) {
    const y = yOf(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + plotW, y); ctx.stroke();
    ctx.fillText(String(Math.round(v)), padL - 4, y);
  }
  ctx.textAlign = "center";
  ctx.fillText("Hz", padL + plotW / 2, H - 12);
  ctx.save(); ctx.translate(10, padT + plotH / 2); ctx.rotate(-Math.PI / 2); ctx.fillText("dB", 0, 0); ctx.restore();

  // Curves
  for (const s of series) {
    ctx.strokeStyle = PLOT_COLORS[s.group % PLOT_COLORS.length];
    ctx.lineWidth = 1.4;
    ctx.setLineDash(s.dash ? [5, 4] : []);
    ctx.beginPath();
    for (let i = 0; i < freqs.length; i++) {
      const x = xOf(freqs[i]), y = yOf(s.db[i]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);

  // Legend (below plot, up to 4 columns)
  const cols = 4, rowH = 13, colW = plotW / cols;
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  series.forEach((s, i) => {
    const cx = padL + (i % cols) * colW + 4, cy = padT + plotH + padB - 2 + Math.floor(i / cols) * rowH + rowH / 2;
    ctx.strokeStyle = PLOT_COLORS[s.group % PLOT_COLORS.length];
    ctx.lineWidth = 1.6;
    ctx.setLineDash(s.dash ? [4, 3] : []);
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + 14, cy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = colText;
    ctx.fillText(s.label, cx + 18, cy);
  });
}

// ============================================================
// Download
// ============================================================
function downloadFirs(format) {
  if (!solveResult || !solveResult.firs_buffer) return;
  const [taps, speakers, inputs] = solveResult.firs_shape;
  const sampleRate = parseInt(document.getElementById("cfg-samplerate").value);

  // Parse the .npy buffer to get raw float64 data
  const buf = solveResult.firs_buffer;
  const view = new DataView(buf);
  // Skip .npy header: magic(6) + version(2) + headerLen(2 or 4) + header
  const majorVersion = view.getUint8(6);
  let headerLen;
  if (majorVersion === 1) {
    headerLen = view.getUint16(8, true);
    var dataOffset = 10 + headerLen;
  } else {
    headerLen = view.getUint32(8, true);
    var dataOffset = 12 + headerLen;
  }
  const firs = new Float64Array(buf, dataOffset);

  // Generate files
  const files = [];
  for (let s = 0; s < speakers; s++) {
    for (let k = 0; k < inputs; k++) {
      const offset = s * inputs + k; // column-major? No — numpy is C-order: [tap, speaker, input]
      // firs[tap, speaker, input] => index = tap * speakers * inputs + speaker * inputs + input
      const coeffs = new Float64Array(taps);
      for (let t = 0; t < taps; t++) {
        coeffs[t] = firs[t * speakers * inputs + s * inputs + k];
      }
      const name = `fir_spk${String(s).padStart(2, "0")}_in${String(k).padStart(2, "0")}`;
      if (format === "wav") {
        files.push({ name: name + ".wav", data: encodeWav(coeffs, sampleRate) });
      } else {
        files.push({ name: name + ".txt", data: new TextEncoder().encode(coeffs.map((c) => c.toExponential(15)).join("\n")) });
      }
    }
  }

  // Create zip (simple store-only zip)
  const zip = createZip(files);
  const blob = new Blob([zip], { type: "application/zip" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `optimimo_firs_${format}.zip`;
  a.click();
  URL.revokeObjectURL(url);
  log(`Downloaded ${files.length} ${format.toUpperCase()} files as zip`);
}

function downloadDiagnostics() {
  if (!solveResult) return;
  const d = solveResult.diagnostics || {};
  const json = JSON.stringify(d, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "optimimo_diagnostics.json";
  a.click();
  URL.revokeObjectURL(url);
}

// Encode float64 samples as float32 WAV
function encodeWav(samples, sampleRate) {
  const n = samples.length;
  const dataSize = n * 4;
  const buf = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buf);

  // RIFF header
  writeStr(view, 0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeStr(view, 8, "WAVE");
  writeStr(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 3, true); // IEEE float
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 4, true);
  view.setUint16(32, 4, true);
  view.setUint16(34, 32, true);
  writeStr(view, 36, "data");
  view.setUint32(40, dataSize, true);

  for (let i = 0; i < n; i++) {
    view.setFloat32(44 + i * 4, samples[i], true);
  }
  return new Uint8Array(buf);
}

function writeStr(view, offset, str) {
  for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
}

// Minimal ZIP creator (store-only, no compression)
function createZip(files) {
  const parts = [];
  const centralDir = [];
  let offset = 0;

  for (const file of files) {
    const nameBytes = new TextEncoder().encode(file.name);
    const crc = crc32(file.data);

    // Local file header
    const local = new ArrayBuffer(30 + nameBytes.length);
    const lv = new DataView(local);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint16(4, 20, true);
    lv.setUint16(6, 0, true);
    lv.setUint16(8, 0, true); // store
    lv.setUint16(10, 0, true);
    lv.setUint16(12, 0, true);
    lv.setUint32(14, crc, true);
    lv.setUint32(18, file.data.length, true);
    lv.setUint32(22, file.data.length, true);
    lv.setUint16(26, nameBytes.length, true);
    lv.setUint16(28, 0, true);
    new Uint8Array(local, 30).set(nameBytes);
    parts.push(new Uint8Array(local));
    parts.push(file.data);

    // Central directory entry
    const cd = new ArrayBuffer(46 + nameBytes.length);
    const cv = new DataView(cd);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0, true);
    cv.setUint16(10, 0, true);
    cv.setUint16(12, 0, true);
    cv.setUint16(14, 0, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, file.data.length, true);
    cv.setUint32(24, file.data.length, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint16(30, 0, true);
    cv.setUint16(32, 0, true);
    cv.setUint16(34, 0, true);
    cv.setUint16(36, 0, true);
    cv.setUint32(38, 0, true);
    cv.setUint32(42, offset, true);
    new Uint8Array(cd, 46).set(nameBytes);
    centralDir.push(new Uint8Array(cd));

    offset += 30 + nameBytes.length + file.data.length;
  }

  // End of central directory
  let cdSize = 0;
  for (const cd of centralDir) cdSize += cd.length;
  const eocd = new ArrayBuffer(22);
  const ev = new DataView(eocd);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(4, 0, true);
  ev.setUint16(6, 0, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, cdSize, true);
  ev.setUint32(16, offset, true);
  ev.setUint16(20, 0, true);

  // Concatenate
  let total = offset + cdSize + 22;
  const zip = new Uint8Array(total);
  let pos = 0;
  for (const p of parts) { zip.set(p, pos); pos += p.length; }
  for (const cd of centralDir) { zip.set(cd, pos); pos += cd.length; }
  zip.set(new Uint8Array(eocd), pos);
  return zip;
}

// CRC32
function crc32(data) {
  let crc = 0xFFFFFFFF;
  const table = crc32.table || (crc32.table = (() => {
    const t = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
      let c = i;
      for (let j = 0; j < 8; j++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
      t[i] = c;
    }
    return t;
  })());
  for (let i = 0; i < data.length; i++) {
    crc = table[(crc ^ data[i]) & 0xFF] ^ (crc >>> 8);
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

// ============================================================
// Init
// ============================================================
log("OptiMIMO web app loaded. Configure, load measurements, then run.");
setStatus("Configure system, load measurements, then run the solver.", "info");
