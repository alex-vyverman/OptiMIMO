// CDP driver: launches Chrome, navigates to the spike page, waits for completion,
// scrapes results from #out, and exits.
const { spawn } = require("child_process");
const fs = require("fs");

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PAGE_URL = process.argv[2] || "http://127.0.0.1:8877/index.html";
const PORT = 9334;
const TIMEOUT_MS = 300000; // 5 minutes for Pyodide boot + solve

const chrome = spawn(CHROME, [
  `--remote-debugging-port=${PORT}`,
  "--no-first-run",
  "--user-data-dir=/tmp/kilo/pyodide_spike_chrome",
  "--disable-gpu",
  "--disable-software-rasterizer",
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
  throw new Error("Chrome CDP not reachable after 15s");
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

  // Poll for completion: check #out element for non-empty content
  const startMs = Date.now();
  let result = null;
  while (Date.now() - startMs < TIMEOUT_MS) {
    await sleep(2000);
    const elapsed = ((Date.now() - startMs) / 1000).toFixed(0);
    const statusRes = await send("Runtime.evaluate", {
      expression: `document.getElementById("status-text")?.textContent || ""`,
      returnByValue: true,
    });
    const status = statusRes.result?.value || "";
    console.log(`[${elapsed}s] status: ${status}`);

    const outRes = await send("Runtime.evaluate", {
      expression: `document.getElementById("out")?.textContent || ""`,
      returnByValue: true,
    });
    const outText = outRes.result?.value || "";
    if (outText && outText.length > 10) {
      try {
        result = JSON.parse(outText);
        break;
      } catch {}
    }

    // Check for errors
    const logRes = await send("Runtime.evaluate", {
      expression: `document.getElementById("log")?.textContent || ""`,
      returnByValue: true,
    });
    const logText = logRes.result?.value || "";
    if (logText.includes("ERROR:")) {
      console.log("Page reported error:");
      console.log(logText);
      break;
    }
  }

  // Get full log
  const fullLogRes = await send("Runtime.evaluate", {
    expression: `document.getElementById("log")?.textContent || ""`,
    returnByValue: true,
  });
  console.log("\n=== PAGE LOG ===");
  console.log(fullLogRes.result?.value || "(empty)");

  // Extract firs b64 for parity comparison
  const firsRes = await send("Runtime.evaluate", {
    expression: `document.getElementById("firs-b64")?.textContent || ""`,
    returnByValue: true,
  });
  const firsB64 = firsRes.result?.value || "";
  if (firsB64) {
    fs.writeFileSync("/tmp/pyodide_spike_firs.b64", firsB64);
    console.log(`\nSaved firs b64 (${firsB64.length} chars) to /tmp/pyodide_spike_firs.b64`);
  }

  console.log("\n=== RESULTS ===");
  if (result) {
    console.log(JSON.stringify(result, null, 2));
    // Save to file
    fs.writeFileSync("/tmp/pyodide_spike_results.json", JSON.stringify(result, null, 2));
    console.log("\nSaved to /tmp/pyodide_spike_results.json");
  } else {
    console.log("No results (timeout or error)");
  }

  ws.close();
  chrome.kill();
  process.exit(result ? 0 : 1);
})().catch((e) => {
  console.error("DRIVER ERROR:", e.message);
  chrome.kill();
  process.exit(1);
});
