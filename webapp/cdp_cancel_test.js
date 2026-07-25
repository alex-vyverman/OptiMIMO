// CDP test: graceful cancellation via SharedArrayBuffer.
// 1. Generate synthetic data, start solve
// 2. Cancel mid-solve, verify worker survives
// 3. Start another solve, verify it completes
const { spawn } = require("child_process");
const fs = require("fs");

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PAGE_URL = process.argv[2] || "http://127.0.0.1:8878/index.html";
const PORT = 9337;
const TIMEOUT_MS = 300000;

const chrome = spawn(CHROME, [
  `--remote-debugging-port=${PORT}`,
  "--no-first-run",
  "--user-data-dir=/tmp/kilo/webapp_cancel_chrome",
  "--disable-gpu",
  "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getWsUrl() {
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const tabs = await res.json();
      const page = tabs.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {}
    await sleep(250);
  }
  throw new Error("Chrome CDP not reachable");
}

(async () => {
  const wsUrl = await getWsUrl();
  const ws = new WebSocket(wsUrl);
  let id = 0;
  const pending = new Map();
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const msgId = ++id;
      pending.set(msgId, { resolve, reject });
      ws.send(JSON.stringify({ id: msgId, method, params }));
    });

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id).resolve(msg.result);
      pending.delete(msg.id);
    }
  };

  await new Promise((r) => (ws.onopen = r));
  await send("Page.enable");
  await send("Runtime.enable");

  console.log(`Navigating to ${PAGE_URL}`);
  await send("Page.navigate", { url: PAGE_URL });
  await sleep(2000);

  // Check SharedArrayBuffer support
  const sabCheck = await send("Runtime.evaluate", {
    expression: `typeof SharedArrayBuffer !== "undefined" && crossOriginIsolated`,
    returnByValue: true,
  });
  console.log(`SharedArrayBuffer + crossOriginIsolated: ${sabCheck.result?.value}`);
  if (!sabCheck.result?.value) {
    console.log("FAIL: SharedArrayBuffer not available. Check COOP/COEP headers.");
    ws.close(); chrome.kill(); process.exit(1);
  }

  // Step 1: Generate synthetic data (larger config for slower solve)
  console.log("\nStep 1: Generating synthetic data (5spk x 4mic x 65536)...");
  await send("Runtime.evaluate", {
    expression: `
      document.getElementById('cfg-speakers').value = '5';
      document.getElementById('cfg-mics').value = '4';
      document.getElementById('cfg-inputs').value = '3';
      document.getElementById('synth-irlen').value = '65536';
      buildSpeakerProfiles(); buildInputRouting();
      setInputSource('synthetic'); generateSynthetic();
    `,
  });
  await sleep(500);

  // Verify IR data loaded
  const irCheck = await send("Runtime.evaluate", {
    expression: `irShape ? JSON.stringify(irShape) : "null"`,
    returnByValue: true,
  });
  console.log(`IR shape: ${irCheck.result?.value}`);

  // Init worker and wait for ready
  console.log("Initializing worker...");
  await send("Runtime.evaluate", { expression: `ensureWorker();` });
  for (let i = 0; i < 30; i++) {
    await sleep(500);
    const r = await send("Runtime.evaluate", { expression: `workerReady`, returnByValue: true });
    if (r.result?.value) break;
  }
  console.log("Worker ready");

  // Step 2: Start solve and cancel mid-way
  console.log("Step 2: Starting solve, will cancel in 200ms...");
  await send("Runtime.evaluate", { expression: `startSolve();` });
  await sleep(200);
  console.log("Cancelling...");
  await send("Runtime.evaluate", { expression: `cancelSolve();` });
  await sleep(3000);

  // Check cancellation result
  const cancelLog = await send("Runtime.evaluate", {
    expression: `document.getElementById("log")?.textContent || ""`,
    returnByValue: true,
  });
  const logText = cancelLog.result?.value || "";
  const cancelledOk = logText.includes("worker still alive") || logText.includes("cancelled");
  console.log(`Cancellation logged: ${cancelledOk}`);

  // Check worker is still alive
  const workerAlive = await send("Runtime.evaluate", {
    expression: `worker !== null && workerReady`,
    returnByValue: true,
  });
  console.log(`Worker still alive and ready: ${workerAlive.result?.value}`);

  // Step 3: Run another solve to completion
  console.log("\nStep 3: Running second solve to completion...");
  await send("Runtime.evaluate", { expression: `startSolve();` });

  const startMs = Date.now();
  let result = null;
  while (Date.now() - startMs < TIMEOUT_MS) {
    await sleep(2000);
    const outRes = await send("Runtime.evaluate", {
      expression: `document.getElementById("out")?.textContent || ""`,
      returnByValue: true,
    });
    const outText = outRes.result?.value || "";
    if (outText && outText.length > 10) {
      try { result = JSON.parse(outText); break; } catch {}
    }
    const statusRes = await send("Runtime.evaluate", {
      expression: `document.getElementById("progress-label")?.textContent || ""`,
      returnByValue: true,
    });
    const elapsed = ((Date.now() - startMs) / 1000).toFixed(0);
    console.log(`[${elapsed}s] ${statusRes.result?.value}`);
  }

  // Final log
  const fullLogRes = await send("Runtime.evaluate", {
    expression: `document.getElementById("log")?.textContent || ""`,
    returnByValue: true,
  });
  console.log("\n=== PAGE LOG ===");
  console.log(fullLogRes.result?.value || "(empty)");

  console.log("\n=== VERDICT ===");
  const passed = cancelledOk && workerAlive.result?.value && result && result.status === "complete";
  console.log(`Graceful cancel works: ${cancelledOk}`);
  console.log(`Worker survives cancel: ${workerAlive.result?.value}`);
  console.log(`Second solve completes: ${result?.status === "complete"}`);
  console.log(`Overall: ${passed ? "PASS" : "FAIL"}`);

  if (result) {
    console.log(`\nSecond solve: ${result.total_ms?.toFixed(0)}ms, firs=${JSON.stringify(result.firs_shape)}`);
  }

  ws.close();
  chrome.kill();
  process.exit(passed ? 0 : 1);
})().catch((e) => {
  console.error("DRIVER ERROR:", e.message);
  chrome.kill();
  process.exit(1);
});
