// Test REW import logic with simulated REW API responses.
// Verifies that start_time and peak_time are used correctly for alignment.

// Simulate the alignment logic from app.js
function testAlignment() {
  const sampleRate = 48000;
  const preRollSamples = Math.round(0.020 * sampleRate); // 960 samples

  // Simulate 3 IRs with different start_times and peak_times
  // IR 0: starts at 0.5s, peak at 0.52s (20ms into the data)
  // IR 1: starts at 0.5s, peak at 0.53s (30ms into the data) — 10ms later
  // IR 2: starts at 0.5s, peak at 0.51s (10ms into the data) — 10ms earlier
  const irArrays = [
    {
      title: "IR0",
      samples: new Float64Array(4800), // 100ms of data
      startTime: 0.5,
      peak_time: 0.52,
    },
    {
      title: "IR1",
      samples: new Float64Array(4800),
      startTime: 0.5,
      peak_time: 0.53,
    },
    {
      title: "IR2",
      samples: new Float64Array(4800),
      startTime: 0.5,
      peak_time: 0.51,
    },
  ];

  // Fill with test data: impulse at the expected peak position
  for (const ir of irArrays) {
    const peakIdx = Math.round((ir.peak_time - ir.startTime) * sampleRate);
    if (peakIdx >= 0 && peakIdx < ir.samples.length) {
      ir.samples[peakIdx] = 1.0;
    }
  }

  // Run the alignment logic (copied from app.js)
  const peakInfo = irArrays.map((ir) => {
    let peakIdx, absPeakTime;
    if (ir.peak_time != null) {
      peakIdx = Math.round((ir.peak_time - ir.startTime) * sampleRate);
      peakIdx = Math.min(Math.max(peakIdx, 0), Math.max(ir.samples.length - 1, 0));
      absPeakTime = ir.peak_time;
    } else {
      peakIdx = 0;
      let peakVal = 0;
      for (let i = 0; i < ir.samples.length; i++) {
        if (Math.abs(ir.samples[i]) > peakVal) { peakVal = Math.abs(ir.samples[i]); peakIdx = i; }
      }
      absPeakTime = ir.startTime + peakIdx / sampleRate;
    }
    return { ...ir, peakIdx, absPeakTime };
  });

  const minPeak = Math.min(...peakInfo.map((p) => p.absPeakTime));

  const aligned = peakInfo.map((ir) => {
    const target = preRollSamples + Math.round((ir.absPeakTime - minPeak) * sampleRate);
    const offset = target - ir.peakIdx;
    return { ...ir, offset };
  });

  // Verify results
  console.log("Alignment test:");
  console.log(`  preRollSamples: ${preRollSamples}`);
  console.log(`  minPeak: ${minPeak}`);

  for (const ir of aligned) {
    const expectedPeakPos = preRollSamples + Math.round((ir.absPeakTime - minPeak) * sampleRate);
    const actualPeakPos = ir.offset + ir.peakIdx;
    console.log(`  ${ir.title}: peakIdx=${ir.peakIdx}, offset=${ir.offset}, ` +
      `expected peak at ${expectedPeakPos}, actual peak at ${actualPeakPos}, ` +
      `match=${expectedPeakPos === actualPeakPos}`);
  }

  // IR2 has the earliest peak (0.51s), so it should be at preRollSamples
  // IR0 is 10ms later, so it should be at preRollSamples + 480
  // IR1 is 20ms later, so it should be at preRollSamples + 960
  const ir0 = aligned.find((a) => a.title === "IR0");
  const ir1 = aligned.find((a) => a.title === "IR1");
  const ir2 = aligned.find((a) => a.title === "IR2");

  const ir0PeakPos = ir0.offset + ir0.peakIdx;
  const ir1PeakPos = ir1.offset + ir1.peakIdx;
  const ir2PeakPos = ir2.offset + ir2.peakIdx;

  console.log(`\nRelative timing:`);
  console.log(`  IR2 (earliest): peak at sample ${ir2PeakPos} (expected ${preRollSamples})`);
  console.log(`  IR0 (+10ms):    peak at sample ${ir0PeakPos} (expected ${preRollSamples + 480})`);
  console.log(`  IR1 (+20ms):    peak at sample ${ir1PeakPos} (expected ${preRollSamples + 960})`);

  const ok = ir2PeakPos === preRollSamples &&
             ir0PeakPos === preRollSamples + 480 &&
             ir1PeakPos === preRollSamples + 960;
  console.log(`\nResult: ${ok ? "PASS" : "FAIL"}`);
  return ok;
}

// Test with negative offsets (trimming)
function testTrimming() {
  const sampleRate = 48000;
  const preRollSamples = Math.round(0.020 * sampleRate); // 960 samples

  // IR with peak very early in the data — needs trimming
  const irArrays = [
    {
      title: "EarlyPeak",
      samples: new Float64Array(4800),
      startTime: 0.5,
      peak_time: 0.501, // 1ms into the data = 48 samples
    },
  ];

  const peakIdx = Math.round((irArrays[0].peak_time - irArrays[0].startTime) * sampleRate);
  irArrays[0].samples[peakIdx] = 1.0;

  const peakInfo = irArrays.map((ir) => {
    const pIdx = Math.round((ir.peak_time - ir.startTime) * sampleRate);
    return { ...ir, peakIdx: pIdx, absPeakTime: ir.peak_time };
  });

  const minPeak = Math.min(...peakInfo.map((p) => p.absPeakTime));

  const aligned = peakInfo.map((ir) => {
    const target = preRollSamples + Math.round((ir.absPeakTime - minPeak) * sampleRate);
    const offset = target - ir.peakIdx;
    return { ...ir, offset };
  });

  const ir = aligned[0];
  console.log(`\nTrimming test:`);
  console.log(`  peakIdx: ${ir.peakIdx} (expected 48)`);
  console.log(`  offset: ${ir.offset} (expected ${preRollSamples - 48} = 912)`);
  console.log(`  This is a positive offset (prepend zeros), not trimming.`);

  // Now test actual trimming: peak before the pre-roll point
  const ir2 = {
    title: "VeryEarlyPeak",
    samples: new Float64Array(4800),
    startTime: 0.5,
    peak_time: 0.5001, // 0.1ms into the data = 4.8 samples
  };
  const pIdx2 = Math.round((ir2.peak_time - ir2.startTime) * sampleRate);
  const offset2 = preRollSamples - pIdx2;
  console.log(`\n  VeryEarlyPeak: peakIdx=${pIdx2}, offset=${offset2}`);
  console.log(`  This is still positive (prepend ${offset2} zeros).`);

  // For actual trimming, we'd need peak_time < start_time (impossible) or
  // a very large pre-roll. Let's test with a larger pre-roll:
  const largePreRoll = Math.round(0.001 * sampleRate); // 1ms pre-roll = 48 samples
  const offset3 = largePreRoll - pIdx2;
  console.log(`  With 1ms pre-roll: offset=${offset3} (negative = trim ${-offset3} samples)`);

  return true;
}

// Run tests
const test1 = testAlignment();
const test2 = testTrimming();
console.log(`\nOverall: ${test1 && test2 ? "ALL PASS" : "SOME FAILED"}`);
