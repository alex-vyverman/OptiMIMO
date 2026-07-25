// Test REW import via CDP
const CDP_PORT = 9223;
const PAGE_URL = "http://127.0.0.1:8878/index.html";

async function main() {
  const { default: CDP } = await import("chrome-remote-interface").catch(() => {
    console.error("chrome-remote-interface not installed. Run: npm install chrome-remote-interface");
    process.exit(1);
  });

  const client = await CDP({ port: CDP_PORT });
  const { Page, Runtime, Console } = client;

  await Page.enable();
  await Runtime.enable();
  await Console.enable();

  const logs = [];
  Console.messageAdded(({ message }) => {
    logs.push(`[console.${message.level}] ${message.text}`);
  });

  console.log(`Navigating to ${PAGE_URL}`);
  await Page.navigate({ url: PAGE_URL });
  await Page.loadEventFired();
  await new Promise((r) => setTimeout(r, 2000));

  // Step 1: Fetch REW measurements
  console.log("Step 1: Fetching REW measurements...");
  await Runtime.evaluate({ expression: `fetchRewMeasurements()` });
  await new Promise((r) => setTimeout(r, 3000));

  // Check if measurements were loaded
  const measCount = await Runtime.evaluate({
    expression: `rewMeasurements.length`,
    returnByValue: true,
  });
  console.log(`Found ${measCount.result.value} measurements`);

  if (measCount.result.value === 0) {
    console.error("No measurements found. Is REW running with API enabled?");
    await client.close();
    process.exit(1);
  }

  // Step 2: Auto-assign measurements (first N measurements to grid)
  console.log("Step 2: Auto-assigning measurements...");
  const speakers = await Runtime.evaluate({
    expression: `parseInt(document.getElementById("cfg-speakers").value)`,
    returnByValue: true,
  });
  const mics = await Runtime.evaluate({
    expression: `parseInt(document.getElementById("cfg-mics").value)`,
    returnByValue: true,
  });
  const nSpk = speakers.result.value;
  const nMic = mics.result.value;
  console.log(`Grid: ${nMic} mics × ${nSpk} speakers`);

  // Assign first N measurements to grid
  for (let m = 0; m < nMic; m++) {
    for (let s = 0; s < nSpk; s++) {
      const idx = m * nSpk + s;
      if (idx < measCount.result.value) {
        await Runtime.evaluate({
          expression: `
            const select = document.getElementById("rew-assign-${m}-${s}");
            if (select && select.options.length > ${idx + 1}) {
              select.selectedIndex = ${idx + 1}; // Skip the "—" option
            }
          `,
        });
      }
    }
  }

  // Step 3: Import from REW
  console.log("Step 3: Importing from REW...");
  await Runtime.evaluate({ expression: `importFromRew()` });
  await new Promise((r) => setTimeout(r, 10000));

  // Check if IRs were loaded
  const irShape = await Runtime.evaluate({
    expression: `irShape ? JSON.stringify(irShape) : "null"`,
    returnByValue: true,
  });
  console.log(`IR shape: ${irShape.result.value}`);

  if (irShape.result.value === "null") {
    console.error("No IRs loaded. Check the log for errors.");
  } else {
    // Step 4: Run solve
    console.log("Step 4: Running solve...");
    await Runtime.evaluate({ expression: `startSolve()` });

    // Wait for completion
    let done = false;
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      const status = await Runtime.evaluate({
        expression: `document.getElementById("progress-label").textContent`,
        returnByValue: true,
      });
      const text = status.result.value;
      if (i % 5 === 0) console.log(`[${i}s] ${text}`);
      if (text === "Done" || text === "Cancelled" || text.includes("Error")) {
        done = true;
        break;
      }
    }

    if (done) {
      const results = await Runtime.evaluate({
        expression: `document.getElementById("out").textContent`,
        returnByValue: true,
      });
      console.log("\n=== RESULTS ===");
      console.log(results.result.value);
    }
  }

  // Print page log
  const pageLog = await Runtime.evaluate({
    expression: `document.getElementById("log").innerText`,
    returnByValue: true,
  });
  console.log("\n=== PAGE LOG ===");
  console.log(pageLog.result.value);

  await client.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
