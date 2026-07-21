<p align="center">
  <img src="images/optimimo-FULL.png" alt="OptiMIMO — MIMO Room Optimization" width="600">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.9%2B-blue.svg" alt="Python 3.9+"></a>
  <a href="https://github.com/alex-vyverman/OptiMIMO/actions/workflows/test.yml"><img src="https://github.com/alex-vyverman/OptiMIMO/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
</p>

# OptiMIMO

*MIMO Room Correction FIR Matrix Solver*

OptiMIMO is a desktop tool for designing an active room-correction matrix with support speakers. It loads one REW impulse response per speaker/microphone pair, builds the frequency-domain room matrix `H(f)`, solves a regularized MIMO inverse, exports `N x N` FIR filters, and writes a CamillaDSP branch/filter/sum YAML snippet.

At each frequency bin, the solver minimizes:

```text
J_f = || Wm^(1/2) (H_f X_f - Y_f) ||_F^2 + || Gamma_f^(1/2) X_f ||_F^2
```

with solution:

```text
X_f = (H_f^H Wm H_f + Gamma_f)^-1 H_f^H Wm Y_f
```

`Gamma_f` is diagonal and frequency-dependent. It increases when a speaker is outside its configured operating band, when the measured acoustic authority is weak, and when optional per-speaker effort penalties are configured. The solver also applies hard frequency-domain gain caps and can cap the summed drive sent to each physical speaker.

## Quick Start

Install dependencies and launch the GUI:

```bash
python3 -m pip install -r requirements.txt
python3 -m optimimo.gui.app
```

This opens `http://localhost:8080` in your default browser. Pass `--config path/to/config.json` to open an existing project, `--port` to change the port, or `--no-browser` to skip auto-launch. Installing with `pip install -e .` also provides a `mimo-gui` shortcut.

## Using the GUI

The GUI covers the full workflow end-to-end: configure the solve, assign measurements, run, and inspect the result. Each step is a tab.

### Config tab

All solver parameters are grouped exactly as in the [Configuration Reference](#configuration-reference) below. The same JSON files used by `--config` on the CLI load and save here, so configs are interchangeable between projects and machines.

- Edit parameters inline with validation as you type
- Load / Save buttons read and write JSON
- Group-by-group layout matches the reference tables for easy lookup

### Measurements tab

A speaker x mic file grid for assigning one IR per crosspoint. A folder-assign helper bulk-fills the grid from a directory, and every file is validated on load (existence, sample rate, length). An **Import from REW** button pulls impulse responses straight from a running REW instance over its HTTP API — see [Importing measurements from REW](#importing-measurements-from-rew-http-api) below.

Both the folder-assign and REW-import dialogs preselect each (speaker, mic) cell automatically when the filename (or REW measurement title) contains the speaker profile name and the mic position name as substrings. Matching is case-insensitive and treats `_`, `-`, `.`, `/`, `\` as equivalent to spaces, so "Sub L_MLP.wav" matches a Sub L / MLP cell just like "sub-l mlp.wav" would. Already-assigned candidates are hidden from other cells' dropdowns to avoid double-assignment.

An **Impulse responses** panel (Show / refresh) plots every measured IR stacked by speaker on a shared time axis, peak-normalized, with a tick marking the direct-arrival time the solver uses. It's a quick way to confirm the timing is physically aligned — mains should peak earliest and the subs later, each speaker consistent across mic positions. Toggle a speaker via the legend and set the visible **Window (ms)** to zoom the arrival region.

### Importing measurements from REW (HTTP API)

Instead of manually exporting one WAV per crosspoint, you can pull measurements directly from REW. On the Measurements tab (explicit file-list mode), click **Import from REW…**:

1. **Enable the API in REW.** This needs **REW V5.40 or newer** (currently a beta release line). Start the server from Preferences → API (or launch REW with `-api`). The API is loopback-only and unauthenticated.
2. **Connect.** The dialog defaults to `127.0.0.1:4735`; the host/port are remembered in the config. Connecting lists every loaded measurement that has an impulse response.
3. **Assign.** Pick the REW measurement for each speaker/mic pair (same layout as the folder-assign helper). Leave a cell empty to skip it.
4. **Import.** The selected impulse responses are downloaded (un-normalised, so relative levels between speakers and mic positions are preserved), written as 32-bit-float mono WAVs under `rew_import/` next to your config, and assigned into the grid. Validate as usual before solving.

Each imported measurement also records REW's reported IR peak time as an `arrival_ms` field on its `measurements` entry. The solver uses this as the per-IR direct-arrival time when de-rotating for `h_smoothing` and the anchored target (and the delay diagnostic displays it), falling back to the impulse-response `argmax` for measurements without it. This matters for subwoofers: a sub's IR has no sharp peak, so `argmax` is unreliable, whereas REW's value is robust.

REW returns each IR with about a second of pre-peak lead-in, so the import anchors on each IR's direct-peak time (REW's robust `timeOfIRPeakSeconds` where available) and places that peak at a small pre-roll plus the IR's arrival relative to the earliest measurement — trimming the lead-in while preserving the relative time-of-flight between measurements. This is only physically correct if the measurements were captured with an **acoustic (or loopback) timing reference** in REW — the API cannot recover relative timing that was never measured (see [REW Measurement and Export Workflow](#rew-measurement-and-export-workflow)).

### Run tab

Runs a pre-flight config check, then the solve. Progress is reported per stage and the solve can be cancelled mid-run. When it completes, a diagnostics summary appears and FIR coefficients plus the CamillaDSP YAML snippet are exported to `output_dir`.

### Analysis tab

Interactive plots from the most recent solve:

- Measured responses per speaker, overlaid across mic positions
- Predicted corrected response vs target per mic, with a per-band residual-error table. The prediction applies the *exported* FIRs (truncation, fade-out and gain caps included) to the measured matrix the solver saw, so it reflects what the filters actually do once loaded into the convolver; any remaining difference to a verification measurement is the documented `h_smoothing_fraction` intent, not the filter export.
- Filter magnitudes per crosspoint
- Impulse envelopes with a target-delay marker and pre-ringing metric

## Command-Line Use (optional)

The same pipeline is available headlessly for batch runs or scripted workflows:

```bash
python3 -m optimimo --write-example-config example_config.json
python3 -m optimimo --config example_config.json
python3 -m optimimo --smoke-test --output-dir /tmp/mimo_smoke
```

`pip install -e .` adds a `mimo-solve` console command for the CLI entry point shown above.

## Code Layout

The implementation lives in the `optimimo` package.

- `optimimo/core/` — measurement loading, complex smoothing, target builders, and the regularized MIMO solver
- `optimimo/core/pipeline.py` — `solve()` returns a `SolveResult` with all artifacts (`h_freq`, `y_freq`, `x_freq`, FIRs, diagnostics) without writing files, supports progress callbacks and cancellation; `export()` writes FIRs, the CamillaDSP YAML, and `diagnostics.json`; `validate_config()` returns pre-flight config issues
- `optimimo/export/` — FIR coefficient files and CamillaDSP YAML generation
- `optimimo/gui/` — the NiceGUI desktop interface
- `optimimo/cli.py` — the command-line interface

GUI tests run with `python3 -m pytest tests/` (process-isolated via pytest-forked; configured in `pytest.ini`).

## REW Measurement and Export Workflow

The solver inverts the measured transfer matrix at full FFT-bin resolution and depends on **phase coherence between all measurements**: the relative time-of-flight between speakers and across mic positions must be physically correct, because the MIMO solve sums speakers at each mic. Getting that timing right is the single most important part of the workflow. There are two ways to bring measurements in:

- **Import from REW over the HTTP API (recommended).** The Measurements tab's *Import from REW…* reads each IR together with REW's reported timing and reconstructs the relative arrivals automatically — no manual windowing or export fiddling, and it avoids the export timing trap below. See [Importing measurements from REW](#importing-measurements-from-rew-http-api).
- **Export WAVs from REW manually.** Workable, but the export step has a timing trap (step 4) that silently destroys the relative timing if you let REW peak-align the IRs.

Either way, the measurement itself has to capture the timing, and the level/SNR advice below applies to both.

### 1. Timing reference (non-negotiable)

- Enable an **acoustic timing reference** (or soundcard loopback) in REW: Preferences -> Analysis -> "Use acoustic timing reference".
- Use the **same reference speaker** (or loopback) for all speaker/mic measurements in the session; never change it mid-session.
- **Without a timing reference there is no shared time origin.** REW then parks every IR's peak at a fixed default position (about 1 s in) independently per measurement, so the relative time-of-flight is *gone and unrecoverable* — no import or export can put it back, and the matrix solution becomes meaningless.
- **How to check:** in the Measurements tab's **Impulse responses** plot (or the delay diagnostic), correct timing shows the direct-arrival peaks spread out — mains earliest, subs later, each speaker consistent across its mic positions. If every IR's peak reads the *same* time, the reference was off (or the export peak-aligned them — step 4).

### 2. Measurement hygiene

- Keep output level and mic gain identical for every sweep; never change levels mid-session.
- Load the microphone calibration file before measuring (it is baked into the export).
- Use long sweeps (256k-512k at 96 kHz) with 2+ repetitions for SNR. The solver will happily "correct" low-frequency noise as if it were real room response, especially near the `authority_floor_db` limit.

### 3. Frequency-dependent smoothing (solver or REW, not both)

Without some form of frequency-dependent smoothing, the solver inverts seat-specific high-frequency comb filtering that is wrong everywhere except at the exact mic position. Apply it **once**, in one of two places — but the two are not equally convenient:

- *In the solver (recommended):* set `h_smoothing_fraction` to `6.0`-`10.0` (≈ 1/6-1/10 octave) — the in-script equivalent of an FDW. It de-rotates each IR by its direct-sound arrival so relative phase is preserved, it's reproducible from the config, and crucially it needs **no windowing in REW**: you export (or import) the full, unwindowed IR and avoid the step-4 timing trap entirely.
- *In REW (only if you have a reason to bake it in):* apply a **frequency-dependent window (FDW)** of roughly **6-10 cycles** (IR Windows -> "Add frequency dependent window") before export. Same effect baked into the WAV — but an FDW *is* a window applied on export, which is exactly the operation that can peak-align your measurements (step 4). If you go this way you **must** export with a common `t=0` sample index for every file, then check the **Impulse responses** plot to confirm the peaks are still spread by speaker. (The API import never windows, so this caveat is for manual WAV export only.)

Do **not** do both, or the smoothing compounds and you lose modal detail you wanted to keep. (REW's fractional-octave *magnitude* smoothing is a separate thing — it only changes the displayed trace, never the exported IR, so it doesn't count here.)

### 4. Exporting WAVs without losing the timing

Skip this step entirely if you use the API import — it handles the timing for you.

**The trap.** REW stores each IR with about a second of pre-peak lead-in. On *File -> Export -> "Export impulse response as WAV"*, REW positions the peak at a fixed place in the file, and if you export a **peak-referenced time-domain window**, the window's left edge becomes sample 0 — so **every IR's peak lands at the same sample**. That peak-aligns all measurements and silently destroys the relative time-of-flight. The tell-tale: the **Impulse responses** plot shows every peak at the same time (e.g. all at the left-window length, or all at ~1 s).

**To preserve relative timing on a manual export:**

- Do **not** export a peak-referenced left-windowed IR as-is, and do **not** rely on REW's default per-peak placement.
- Use REW's **"place t=0 at a specific sample index"** option with the **same sample index for every file**, so all exports share one time origin and each peak lands at its true offset. (Exporting the full, unwindowed IR per step 3 and letting the solver smooth is the easiest way to stay out of trouble.)
- Use the same export length and lead-in for every file.

**Format (matters for both import paths):**

- **32-bit float**, mono.
- Native measurement sample rate (no resampling); it must match `sample_rate` in the config.
- **No normalisation.** The `auto_target_level` and acoustic-authority logic depend on consistent relative levels between speakers and mic positions.

### 5. Verify the timing before solving — and the result after

**Before solving:** open the **Impulse responses** plot on the Measurements tab and confirm the direct-arrival peaks are *spread out* by speaker (mains earliest, subs later, each speaker consistent across its mic positions). If every peak sits at the same time, the timing is collapsed — fix the reference (step 1) or the export (step 4) and re-import; a solve on peak-aligned measurements is meaningless.

**After applying filters:** load the generated filters into CamillaDSP, re-measure all mic positions in REW with the same timing reference, and compare against the predicted target. Also watch the wrap-energy warning in `diagnostics.json`; if it fires, increase `fft_size` or `target_delay_ms`.

### Recommended config restraint

- Full-matrix correction of mains up to 20 kHz across spaced mic positions is not physically meaningful; high-frequency phase decorrelates over centimetres. Consider lowering `max_hz` for the main speakers to the Schroeder region (~300-500 Hz) so the matrix only handles the modal range, or rely on the FDW from step 3 to suppress high-frequency artifacts.
- Start with `max_boost_db` of 6-9 dB until verification measurements confirm the correction is benign.

## Measurement Naming

Use either an explicit `measurements` list or a pattern:

```json
"measurement_pattern": "measurements/spk_{speaker:02d}_mic_{mic:02d}.wav"
```

The pattern supports these placeholders:

| Placeholder | Expands to |
|---|---|
| `{speaker}` / `{mic}` | Zero-based speaker / mic index |
| `{speaker1}` / `{mic1}` | One-based speaker / mic index |
| `{speaker_name}` | The speaker's `name` from its `speaker_profiles` entry |
| `{mic_name}` | The mic position's name from `mic_names` (set per position on the Config tab) |

For example, with speaker 3 named `Main L` and mic position 0 named `MLP`:

```json
"measurement_pattern": "measurements/{speaker_name}_{mic_name}.wav"
```

resolves that crosspoint to `measurements/Main L_MLP.wav`. Mic position names are edited in the Config tab under **Mic positions** (or set directly as the `mic_names` list); an empty name falls back to `mic{index}`. In the GUI, the Measurements tab's folder-assign helper accepts the same template.

## Configuration Reference

An up-to-date example lives in `example_config.json` (regenerate any time from the GUI's Config tab or with `--write-example-config`). All parameters by group:

### Dimensions and measurements

| Parameter | Default | Description |
|---|---|---|
| `num_speakers` | required | Number of physical output channels (N). Speaker indices used everywhere else refer to this ordering. |
| `num_mic_positions` | required | Number of microphone positions (M) in the measurement grid. |
| `num_inputs` | `num_speakers` | Number of input channels (K), e.g. `2` for stereo sources. Produces N x K FIR filters. |
| `sample_rate` | from WAVs | Expected sample rate. Optional for WAV input (read from files, mismatches rejected); required for text IRs without a time column. |
| `measurements` | — | Explicit list of `{speaker, mic, path}` entries, one IR per speaker/mic pair. Entries may carry an optional `arrival_ms` (the IR's direct-arrival time in the WAV's timeline, populated by the REW import) used for the smoothing de-rotation in place of `argmax`. |
| `measurement_pattern` | — | Alternative to `measurements`: filename template such as `"measurements/spk_{speaker:02d}_mic_{mic:02d}.wav"`. Supports `{speaker}`, `{mic}`, `{speaker1}`, `{mic1}`, `{speaker_name}` and `{mic_name}` (see [Measurement Naming](#measurement-naming)). |
| `wav_channel` | `0` | Channel to read from multichannel measurement WAVs. |
| `ir_crop_start_sample` / `ir_crop_start_ms` | `0` | Discard this much of the start of every IR before processing (both add together). Use only if all exports share a common dead-time; never crop per-measurement, that destroys relative timing. |
| `ir_length_samples` | longest IR | Length to which all IRs are cropped/zero-padded. Sets the low-frequency resolution of the measurement data. |

### Filter dimensions

| Parameter | Default | Description |
|---|---|---|
| `filter_taps` | `8192` | Length of the exported FIR filters. Determines how long a correction can ring; 65536 taps at 96 kHz is 683 ms. |
| `fft_size` | auto | Solve resolution; must be at least `ir_length + filter_taps - 1` (auto picks the next power of two). Larger values give the inverse more time to decay before the circular wrap point. |
| `target_delay_ms` | `40.0` | Bulk delay built into the target so the inverse can be causal (becomes system latency). Flat mode needs generous values (~180 ms); anchored mode tolerates less (~80–100 ms) because its phase target is mostly causal. |
| `fade_out_samples` | `0` | Hann fade applied to the FIR tail to avoid a truncation discontinuity. |

### Speaker protection

| Parameter | Default | Description |
|---|---|---|
| `speaker_profiles` | required | Per speaker: `name`, `min_hz`/`max_hz` (safe operating band — the speaker is removed from the optimization outside it), `transition_hz` (raised-sine ramp width inside the band edges), `effort_penalty_db` (optional extra regularization to make the solver prefer other speakers). |
| `max_boost_db` | `9.0` | Hard cap on filter gain, applied per crosspoint and (optionally) per speaker row sum, plus once more after FIR truncation. |
| `max_cut_db` | `120` | Floor for the diagonal filter magnitude; only enforced when `enforce_diagonal_cut_floor` is true. |
| `enforce_row_sum_gain_cap` | `true` | Cap the *summed* drive each physical speaker can receive across all inputs, not just each individual filter. |
| `enforce_diagonal_cut_floor` | `false` | Prevent the direct input-to-primary path from being cut below `max_cut_db`. |
| `enforce_final_gain_cap` | `true` | Re-check and rescale gains after FIR truncation and windowing. |

### Target

| Parameter | Default | Description |
|---|---|---|
| `target_mode` | `"flat"` | `"flat"` = identical house-curve/pure-delay target at all mics; `"anchored"` = target derived from each input's primary speaker (see Target Modes section). |
| `target_curve_points_db` | flat 0 dB | House curve as `[freq_hz, dB]` breakpoints, interpolated on a log-frequency axis. Ignored when `target_curve_file` or `target_curve_ir_file` is set. |
| `target_curve_file` | — | Path to a text file with `freq_hz` and `dB` columns (comments with `#`, comma separators accepted). Overrides `target_curve_points_db`. |
| `target_curve_ir_file` | — | Path to a WAV or text impulse response whose magnitude response is used as the house curve shape (normalised to 0 dB in the reference band). Overrides both `target_curve_points_db` and `target_curve_file`. |
| `target_curve_ir_smoothing_fraction` | `6.0` | Fractional-octave magnitude smoothing applied to the IR-derived house curve (`6.0` = 1/6 octave, `0` = off). |
| `input_primary_speaker` | — | Anchored mode: the speaker each input belongs to, e.g. `{"0": 3, "1": 4}`. |
| `anchor_phase_smoothing_fraction` | `1.0` | Anchored mode: fractional-octave complex smoothing of the primary's response before extracting target phase/levels (`1.0` = one octave). Heavy on purpose — keeps geometry, excludes the defects being corrected. |
| `anchor_level_floor_db` | `-30.0` | Anchored mode: below this level (relative to the primary's in-band average) the target magnitude shrinks toward zero, so nothing is demanded where the primary has no output. |
| `target_mic_matrix` / `target_mic_gains` | all ones | Flat mode only: per-mic (x per-input) scalar target gains. |
| `auto_target_level` | `true` | Scale the target from the median measured in-band response power, so results do not depend on absolute REW export level. |
| `target_level_linear` | — | Explicit linear target level, overrides `auto_target_level`. |
| `reference_band_hz` | `[20, 200]` | Band used for the auto level, the regularization reference power, and anchored-mode level estimation. |

### Routing

| Parameter | Default | Description |
|---|---|---|
| `input_speakers` | all | Allowed speakers per input, e.g. `{"0": [0,1,2,3], "1": [0,1,2,4]}`. Blocked pairs are removed from the optimization and exported as all-zero FIRs. |
| `mic_weights` | all ones | Relative importance of each mic position in the least-squares error (listening position highest). |
| `mic_names` | — | Optional per-position display names, usable in `measurement_pattern` as `{mic_name}`. Empty entries fall back to `mic{index}`. |

### Smoothing and regularization

| Parameter | Default | Description |
|---|---|---|
| `h_smoothing_fraction` | `0` (off) | Fractional-octave complex smoothing of the measured room matrix before solving (`6.0` = 1/6 octave). In-script equivalent of REW's frequency-dependent window. |
| `x_smoothing_fraction` | `0` (off) | Same smoothing applied to the solved filters; bounds the Q of every filter feature so FIRs decay well within `filter_taps`. |
| `base_regularization` | auto | Base Tikhonov term. Auto derives it from the reference power and `max_boost_db` so the unconstrained inverse naturally respects the boost cap. |
| `authority_floor_db` | `-30.0` | Speakers whose measured in-band response (acoustic authority) falls below this relative level get progressively stronger regularization instead of being boosted into inaudibility. |
| `null_regularization_strength` | `1.0` | Multiplier on the authority-floor penalty. |
| `profile_transition_penalty` | `10.0` | How quickly regularization grows inside a speaker's band-edge transition ramps. |
| `profile_disable_threshold` | `1e-4` | Profile weight below which a speaker counts as fully disabled at that frequency. |
| `profile_disable_penalty` | `1e12` | Regularization applied to disabled speaker/frequency (and blocked speaker/input) combinations. |

### Output

| Parameter | Default | Description |
|---|---|---|
| `output_dir` | `"mimo_fir_output"` | Destination for FIRs, YAML snippet and `diagnostics.json`. |
| `output_format` | `"wav"` | `wav`, `txt`, or `both`. |
| `camilladsp_conv_type` | `"wav"` | `wav` = Conv/Wav filters; `raw` = Conv/Raw with `format: TEXT` (workaround for the camillagui import bug, needs txt output). |
| `camilladsp_filter_path_prefix` | `""` | Prepended to coefficient filenames in the YAML — set to the coefficient directory on the DSP host. |
| `camilladsp_absolute_paths` | `false` | Reference coefficients by absolute local path instead. |
| `remove_denormals` | `true` | Zero out sub-1e-24 coefficients to avoid denormal CPU penalties. |
| `wrap_energy_warning_ratio` | `1e-3` | Threshold for the circular-wrap diagnostic warning. |

## CamillaDSP Topology

CamillaDSP does not apply a unique FIR per matrix-mixer source/destination crosspoint in one simple gain mixer, so the generated snippet uses this topology:

```text
N input channels -> N*N branch channels -> one FIR per branch -> summed to N output speakers
```

For example, `fir_o02_i00.wav` means input channel `0` feeding physical output speaker `2`.

By default the snippet references WAV convolution filters (`output_format` must be `wav` or `both`).

**CamillaDSP GUI import bug workaround:** current camillagui-backend crashes with `KeyError: 'format'` when importing any config containing `Conv`/`Wav` filters (its legacy-config migration reads the `format` parameter unconditionally, but `Wav` convolvers don't have one). The GUI then reports "Could not extract filters from file". Set `"camilladsp_conv_type": "raw"` to emit `Conv`/`Raw` filters with `format: TEXT` referencing the exported `.txt` coefficients instead — these import cleanly. This requires `output_format` set to `txt` or `both`. Once the upstream bug is fixed, switch back to `"wav"` (smaller files, faster loading).

## Important DSP Notes

Choose `target_delay_ms` large enough to make the inverse causal. If diagnostics warn about wrap-point energy, increase `target_delay_ms`, `fft_size`, or `filter_taps`. Click the **Estimate** button next to the Target delay field on the Config tab to compute the minimum tolerable value from the measurement set's worst-case group delay (plus a flat- or anchored-mode margin); the Run tab's Validate step issues the same warning when the current value is below the estimate.

Two complex-smoothing options bound how surgical the correction is allowed to be:

- `h_smoothing_fraction`: fractional-octave complex smoothing of the measured room matrix `H(f)` before solving (for example `6.0` for 1/6 octave). Each measurement is de-rotated by its direct-sound arrival time before smoothing, so relative phase between speakers and mic positions is preserved. This is the in-script equivalent of REW's frequency-dependent window and prevents the solver from inverting narrow, position-specific features.
- `x_smoothing_fraction`: the same smoothing applied to the solved filter matrix `X(f)` (de-rotated by `target_delay_ms`). The solver creates sharp spectral transitions at speaker band edges and regularization boundaries that can ring for seconds in the time domain; smoothing the solution bounds the Q of every filter feature so the FIR energy decays well within `filter_taps`. If diagnostics warn about wrap-point energy even with a large `fft_size`, enable this.

Both default to `0.0` (off). `1/6` octave is a reasonable starting point for either.

## Input Routing (`num_inputs` and `input_speakers`)

By default the solver produces `N x N` filters with the same target for every input channel, which makes every input column identical — and a multichannel system would collapse stereo to mono. For a real stereo system, set `num_inputs` and restrict which speakers may reproduce each input:

```json
"num_inputs": 2,
"input_speakers": {
  "0": [0, 1, 2, 3],
  "1": [0, 1, 2, 4]
}
```

Here input `0` (left) may use the three subs plus the left main, and input `1` (right) the three subs plus the right main. Disallowed speaker/input pairs are removed from the optimization (not just zeroed afterwards), so the remaining speakers are solved knowing they cannot rely on the blocked ones. The mains stay strictly stereo while all subs support both channels in their band — mono bass, stereo everything else. Blocked pairs are still exported as all-zero FIRs to keep the matrix topology regular.

## Target Modes (`target_mode`)

- `"flat"` (default): every input is asked to produce the house curve with one identical pure delay at all mic positions. Simple, but physically impossible for spaced mics — the solver wastes effort fighting propagation geometry, and the linear-phase demand risks pre-ringing.
- `"anchored"`: each input's target is derived from (anchored to) its **primary speaker's** measured response. Per mic position, the target keeps the primary's natural arrival time and broad phase (preserving ITD) and its broadband level relative to the other positions (preserving ILD), while the magnitude follows the house curve. The solver then corrects only true deviations (modes, resonances, SBIR), and support speakers are steered to cancel the difference between what the primary does and what it should do at each position.

Anchored mode configuration:

```json
"target_mode": "anchored",
"input_primary_speaker": {"0": 3, "1": 4},
"anchor_phase_smoothing_fraction": 1.0,
"anchor_level_floor_db": -30.0
```

- `input_primary_speaker`: the speaker each input "belongs" to (here: left main for input 0, right main for input 1).
- `anchor_phase_smoothing_fraction`: fractional-octave complex smoothing applied to the primary's measured response before extracting the target phase/levels (`1.0` = one octave). Heavy smoothing is intentional — it keeps geometry while excluding the room defects being corrected from the target itself.
- `anchor_level_floor_db`: where the primary's smoothed response falls below this level relative to its in-band average, the target magnitude shrinks toward zero, so the system never demands output where the primary has no authority (for example below the speaker's rolloff).

Small-speaker protection is implemented twice: the solver removes each speaker from the optimization outside its `speaker_profiles` band, then applies gain caps after solving. Because any finite FIR has transition leakage, use realistic transition bands and sufficiently long taps for low-frequency control.
