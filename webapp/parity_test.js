#!/usr/bin/env node
/**
 * Parity harness: webapp (Pyodide) vs native solver on identical inputs.
 *
 * Orchestrates: native solve (parity_native.py via the repo venv), spawns a
 * local HTTP server + headless Chrome, injects the same IRs/config through
 * the webapp UI, runs the browser solve for each target mode, and diffs FIRs
 * and diagnostics against native.
 *
 * Usage:
 *   npm install          # once (chrome-remote-interface)
 *   npm run parity       # or: node parity_test.js
 *
 * Env overrides:
 *   OPTIMIMO_PYTHON      python for the native solve (default: main repo .venv)
 *   CHROME_BIN           Chrome executable (default: macOS app, then linux names)
 *   PARITY_SERVER_PORT   default 8891
 *   PARITY_CDP_PORT      default 9225
 *   PARITY_REL_TOL       max |fir diff| / max |native fir| (default 0.10)
 *   PARITY_GAIN_TOL_DB   max |gain diff| in dB (default 1.0)
 *
 * Exit code 0 = parity within tolerance for all modes, 1 otherwise.
 */

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");

const WEBAPP = __dirname;
const REPO_ROOT = path.resolve(WEBAPP, "..");
const SERVER_PORT = Number(process.env.PARITY_SERVER_PORT || 8891);
const CDP_PORT = Number(process.env.PARITY_CDP_PORT || 9225);
const REL_TOL = Number(process.env.PARITY_REL_TOL || 0.1);
const GAIN_TOL_DB = Number(process.env.PARITY_GAIN_TOL_DB || 1.0);
const MODES = ["anchored", "flat"];
const PAGE_URL = `http://127.0.0.1:${SERVER_PORT}/index.html`;

const children = [];
function killChildren() {
  for (const p of children) {
    try { p.kill("SIGKILL"); } catch {}
  }
}
process.on("exit", killChildren);
process.on("SIGINT", () => { killChildren(); process.exit(130); });

function findPython() {
  if (process.env.OPTIMIMO_PYTHON) return process.env.OPTIMIMO_PYTHON;
  try {
    const common = spawnSync("git", ["rev-parse", "--git-common-dir"], { cwd: REPO_ROOT })
      .stdout.toString().trim();
    // --git-common-dir points at the main repo's .git dir; venv lives beside it
    const mainRoot = path.resolve(common, "..");
    for (const p of [path.join(mainRoot, ".venv", "bin", "python3"), path.join(REPO_ROOT, ".venv", "bin", "python3")]) {
      if (fs.existsSync(p)) return p;
    }
  } catch {}
  return "python3";
}

function findChrome() {
  const candidates = [
    process.env.CHROME_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);
  for (const c of candidates) if (fs.existsSync(c)) return c;
  throw new Error("Chrome not found; set CHROME_BIN");
}

function waitForHttp(url, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const poll = () => {
      http.get(url, (res) => { res.resume(); resolve(); })
        .on("error", () => {
          if (Date.now() > deadline) reject(new Error(`${label} did not come up at ${url}`));
          else setTimeout(poll, 250);
        });
    };
    poll();
  });
}

async function main() {
  let CDP;
  try {
    CDP = require("chrome-remote-interface");
  } catch {
    console.error("chrome-remote-interface not installed. Run: npm install");
    process.exit(1);
  }

  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "parity-"));

  // 1. Native side
  const python = findPython();
  console.log(`[1/4] Native solve (${python})...`);
  const nat = spawnSync(python, [path.join(WEBAPP, "parity_native.py"), "--out", outDir], { encoding: "utf8" });
  if (nat.status !== 0) {
    console.error(nat.stdout); console.error(nat.stderr);
    throw new Error("native solve failed");
  }
  console.log(nat.stdout.trim().split("\n").map((l) => "      " + l).join("\n"));
  const meta = JSON.parse(fs.readFileSync(path.join(outDir, "meta.json")));

  // 2. Server + Chrome
  console.log(`[2/4] Starting server (:${SERVER_PORT}) and headless Chrome (:${CDP_PORT})...`);
  children.push(spawn("python3", [path.join(WEBAPP, "serve.py"), String(SERVER_PORT)], { cwd: WEBAPP, stdio: "ignore" }));
  const chromeProfile = fs.mkdtempSync(path.join(os.tmpdir(), "parity-chrome-"));
  children.push(spawn(findChrome(), [
    "--headless=new", `--remote-debugging-port=${CDP_PORT}`, "--disable-gpu", "--no-sandbox",
    `--user-data-dir=${chromeProfile}`, "about:blank",
  ], { stdio: "ignore" }));
  await waitForHttp(`http://127.0.0.1:${SERVER_PORT}/index.html`, 15000, "server");
  await waitForHttp(`http://127.0.0.1:${CDP_PORT}/json/version`, 20000, "chrome");

  // 3. Browser side
  console.log("[3/4] Browser solves...");
  const client = await CDP({ port: CDP_PORT });
  const { Page, Runtime } = client;
  await Page.enable();
  await Runtime.enable();
  await Page.navigate({ url: PAGE_URL });
  await Page.loadEventFired();
  await new Promise((r) => setTimeout(r, 1500));

  const ev = async (expr) => {
    const r = await Runtime.evaluate({ expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error("page eval failed: " + JSON.stringify(r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  };

  // Inject IRs (mic-major float64) once
  const irsB64 = fs.readFileSync(path.join(outDir, "irs.bin")).toString("base64");
  await ev(`
    (() => {
      const buf = Uint8Array.from(atob(${JSON.stringify(irsB64)}), (c) => c.charCodeAt(0));
      const all = new Float64Array(buf.buffer);
      const M = ${meta.num_mics}, S = ${meta.num_speakers}, L = ${meta.ir_length};
      irData = [];
      for (let m = 0; m < M; m++) for (let s = 0; s < S; s++) irData.push(all.slice((m * S + s) * L, (m * S + s + 1) * L));
      irShape = [M, S, L];
      irArrivals = null;
    })()
  `);

  // Static UI fields from the shared config (mode-specific fields set per round)
  const cfg0 = JSON.parse(fs.readFileSync(path.join(outDir, `config_${MODES[0]}.json`)));
  await ev(`
    (() => {
      document.getElementById("cfg-speakers").value = ${cfg0.num_speakers};
      document.getElementById("cfg-mics").value = ${cfg0.num_mic_positions};
      document.getElementById("cfg-inputs").value = ${cfg0.num_inputs};
      document.getElementById("cfg-samplerate").value = ${cfg0.sample_rate};
      document.getElementById("cfg-taps").value = ${cfg0.filter_taps};
      document.getElementById("cfg-fft").value = 0;
      document.getElementById("cfg-delay").value = ${cfg0.target_delay_ms};
      document.getElementById("cfg-boost").value = ${cfg0.max_boost_db};
      document.getElementById("cfg-cut").value = ${cfg0.max_cut_db};
      document.getElementById("cfg-hsmooth").value = ${cfg0.h_smoothing_fraction};
      document.getElementById("cfg-xsmooth").value = ${cfg0.x_smoothing_fraction};
      document.getElementById("cfg-fade").value = ${cfg0.fade_out_samples};
      document.getElementById("cfg-irlen").value = 0;
      document.getElementById("cfg-auto-level").value = String(${cfg0.auto_target_level});
      document.getElementById("cfg-curve").value = ${JSON.stringify(cfg0.target_curve_points_db.map((p) => `${p[0]},${p[1]}`).join("; "))};
      document.getElementById("cfg-mic-weights").value = "";
      buildSpeakerProfiles();
      buildInputRouting();
      ${cfg0.speaker_profiles ? Object.entries(cfg0.speaker_profiles).map(([i, p]) => `
        document.getElementById("spk-name-${i}").value = ${JSON.stringify(p.name)};
        document.getElementById("spk-min-${i}").value = ${p.min_hz};
        document.getElementById("spk-max-${i}").value = ${p.max_hz};
        document.getElementById("spk-trans-${i}").value = ${p.transition_hz};`).join("") : ""}
      ${cfg0.input_speakers ? Object.entries(cfg0.input_speakers).map(([i, v]) => `
        document.getElementById("input-spk-${i}").value = "${v.join(",")}";`).join("") : ""}
      ${cfg0.input_primary_speaker ? Object.entries(cfg0.input_primary_speaker).map(([i, v]) => `
        document.getElementById("input-primary-${i}").value = ${v};`).join("") : ""}
    })()
  `);

  const results = [];
  for (const mode of MODES) {
    await ev(`document.getElementById("cfg-target-mode").value = "${mode}"`);

    // Config fidelity check: getConfig() vs native config
    const nativeCfg = JSON.parse(fs.readFileSync(path.join(outDir, `config_${mode}.json`)));
    const pageCfg = JSON.parse(await ev("JSON.stringify(getConfig())"));
    const cfgDiffs = diffConfig(nativeCfg, pageCfg, mode);
    if (cfgDiffs.length) {
      console.error(`      [${mode}] config mismatch between native and webapp UI:`);
      for (const d of cfgDiffs) console.error(`        ${d}`);
      results.push({ mode, ok: false, reason: "config mismatch" });
      continue;
    }

    await ev("startSolve()");
    let done = false, lastLabel = "";
    for (let i = 0; i < 240; i++) {
      await new Promise((r) => setTimeout(r, 500));
      const label = await ev(`document.getElementById("progress-label").textContent`).catch(() => "");
      if (label && label !== lastLabel) { lastLabel = label; }
      if (label === "Done" || (label || "").includes("Error")) { done = true; break; }
    }
    if (!done) { results.push({ mode, ok: false, reason: "solve timeout" }); continue; }

    const out = JSON.parse(await ev(`document.getElementById("out").textContent`));
    const firsB64 = await ev(`
      (() => {
        const b = new Uint8Array(solveResult.firs_buffer);
        let s = "";
        for (let i = 0; i < b.length; i += 32768) s += String.fromCharCode.apply(null, b.subarray(i, i + 32768));
        return btoa(s);
      })()
    `);
    const browser = parseNpy(Buffer.from(firsB64, "base64"));
    const nativeDiag = JSON.parse(fs.readFileSync(path.join(outDir, `diag_${mode}.json`)));
    const nativeFirs = new Float64Array(fs.readFileSync(path.join(outDir, `firs_${mode}.bin`)).buffer);

    const cmp = compareFirs(nativeFirs, browser.data, nativeDiag.firs_shape, browser.shape);
    const gainDiff = Math.abs(out.diagnostics.max_filter_gain_db - nativeDiag.max_filter_gain_db);
    const rowDiff = Math.abs(out.diagnostics.max_row_sum_gain_db - nativeDiag.max_row_sum_gain_db);
    const ok = cmp.rel <= REL_TOL && gainDiff <= GAIN_TOL_DB && rowDiff <= GAIN_TOL_DB;
    results.push({ mode, ok, cmp, gainDiff, rowDiff, nativeDiag, browserDiag: out.diagnostics });
  }

  const browserNumpy = (await ev(`document.getElementById("log").innerText`).catch(() => "")).match(/numpy_version: ([\d.]+)/)?.[1] || "?";

  await client.close();
  killChildren();

  // 4. Report
  console.log("[4/4] Report");
  console.log(`      native numpy ${meta.numpy} vs browser numpy ${browserNumpy}`);
  let allOk = true;
  for (const r of results) {
    if (!r.ok) {
      allOk = false;
      console.log(`      FAIL ${r.mode}: ${r.reason || "tolerance exceeded"}`);
      if (r.cmp) printCmp(r);
      continue;
    }
    console.log(`      PASS ${r.mode}: rel_fir_diff=${r.cmp.rel.toExponential(2)} (tol ${REL_TOL}), ` +
      `gain_diff=${r.gainDiff.toFixed(3)} dB, rowsum_diff=${r.rowDiff.toFixed(3)} dB (tol ${GAIN_TOL_DB})`);
    console.log(`           max_gain native=${r.nativeDiag.max_filter_gain_db.toFixed(2)} dB, browser=${r.browserDiag.max_filter_gain_db.toFixed(2)} dB`);
  }
  fs.rmSync(outDir, { recursive: true, force: true });
  fs.rmSync(chromeProfile, { recursive: true, force: true });
  console.log(allOk ? "\nPARITY OK" : "\nPARITY FAILED");
  process.exit(allOk ? 0 : 1);
}

function printCmp(r) {
  console.log(`        rel=${r.cmp.rel.toExponential(2)} maxdiff=${r.cmp.maxDiff.toExponential(2)} ` +
    `gain_diff=${r.gainDiff?.toFixed(3)} dB rowsum_diff=${r.rowDiff?.toFixed(3)} dB`);
}

function parseNpy(buf) {
  if (buf.subarray(0, 6).toString("latin1") !== "\x93NUMPY") throw new Error("bad npy magic");
  const major = buf[6];
  const off = major === 1 ? 10 + buf.readUInt16LE(8) : 12 + buf.readUInt32LE(8);
  const header = buf.subarray(major === 1 ? 10 : 12, off).toString("latin1");
  const m = header.match(/'shape':\s*\(([^)]*)\)/);
  if (!m) throw new Error("bad npy header: " + header);
  const shape = m[1].split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n));
  const data = new Float64Array(buf.buffer, buf.byteOffset + off, (buf.length - off) / 8);
  return { shape, data };
}

function compareFirs(native, browser, nativeShape, browserShape) {
  if (native.length !== browser.length) {
    return { rel: Infinity, maxDiff: Infinity, note: `length ${browser.length} != native ${native.length}` };
  }
  let maxDiff = 0, maxNative = 0;
  for (let i = 0; i < native.length; i++) {
    const d = Math.abs(native[i] - browser[i]);
    if (d > maxDiff) maxDiff = d;
    const a = Math.abs(native[i]);
    if (a > maxNative) maxNative = a;
  }
  return { rel: maxNative > 0 ? maxDiff / maxNative : maxDiff, maxDiff, maxNative };
}

function diffConfig(native, page, mode) {
  const diffs = [];
  const keys = [
    "num_speakers", "num_mic_positions", "num_inputs", "sample_rate", "filter_taps",
    "target_delay_ms", "max_boost_db", "max_cut_db", "h_smoothing_fraction",
    "x_smoothing_fraction", "fade_out_samples", "target_mode", "auto_target_level",
    "authority_floor_db", "enforce_row_sum_gain_cap",
  ];
  for (const k of keys) {
    if (JSON.stringify(native[k]) !== JSON.stringify(page[k])) diffs.push(`${k}: native=${JSON.stringify(native[k])} page=${JSON.stringify(page[k])}`);
  }
  for (const k of ["target_curve_points_db", "speaker_profiles", "input_speakers", "mic_weights", "reference_band_hz"]) {
    if (JSON.stringify(native[k]) !== JSON.stringify(page[k])) diffs.push(`${k}: native=${JSON.stringify(native[k])} page=${JSON.stringify(page[k])}`);
  }
  if (mode === "anchored" && JSON.stringify(native.input_primary_speaker) !== JSON.stringify(page.input_primary_speaker)) {
    diffs.push(`input_primary_speaker: native=${JSON.stringify(native.input_primary_speaker)} page=${JSON.stringify(page.input_primary_speaker)}`);
  }
  return diffs;
}

main().catch((e) => { console.error("PARITY ERROR:", e.message); killChildren(); process.exit(1); });
