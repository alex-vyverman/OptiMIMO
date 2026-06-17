# OptiMIMO

*MIMO Room Correction FIR Matrix Solver*

This workspace contains a foundational Python pipeline for an active room-correction matrix with support speakers. It loads one REW impulse response per speaker/microphone pair, builds the frequency-domain room matrix `H(f)`, solves a regularized MIMO inverse, exports `N x N` FIR filters, and writes a CamillaDSP branch/filter/sum YAML snippet.

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

```bash
python3 -m pip install -r requirements.txt
python3 mimo_room_correction.py --write-example-config example_config.json
python3 mimo_room_correction.py --config example_config.json
```

Run the synthetic smoke test without REW files:

```bash
python3 mimo_room_correction.py --smoke-test --output-dir /tmp/mimo_room_correction_smoke
```

## GUI

A local browser-based GUI (NiceGUI) covers the full workflow: config editing, measurement assignment with validation, and solving with progress, cancellation, diagnostics, and export.

```bash
python3 -m pip install -r requirements.txt   # includes nicegui
python3 -m mimo_acoustic.gui.app --config config_ir.json
```

This opens `http://localhost:8080` (use `--port` and `--no-browser` to change behavior; `pip install -e .` also provides a `mimo-gui` command). Tabs:

- **Config** — all solver parameters grouped as in the Configuration Reference, with load/save of the same JSON files the CLI uses
- **Measurements** — speaker x mic file grid with a folder-assign helper and per-file validation (existence, sample rate, length)
- **Run** — pre-flight config validation, solve with stage progress and cancel, diagnostics summary, FIR/YAML export
- **Analysis** — interactive plots from the last solve: measured responses per speaker across mics, predicted corrected response vs target per mic with a per-band residual-error table, filter magnitudes per crosspoint, and impulse envelopes with a target-delay marker and pre-ringing metric

GUI tests run with `python3 -m pytest tests/` (process-isolated via pytest-forked; configured in `pytest.ini`).

## Code Layout

The implementation lives in the `mimo_acoustic` package; `mimo_room_correction.py` is a thin compatibility shim that re-exports the public API, so both the command line above and `import mimo_room_correction` keep working. The package can also be installed with `pip install -e .`, which provides a `mimo-solve` console command.

- `mimo_acoustic/core/` — measurement loading, complex smoothing, target builders, and the regularized MIMO solver
- `mimo_acoustic/core/pipeline.py` — `solve()` returns a `SolveResult` with all artifacts (`h_freq`, `y_freq`, `x_freq`, FIRs, diagnostics) without writing files, supports progress callbacks and cancellation; `export()` writes FIRs, the CamillaDSP YAML, and `diagnostics.json`; `validate_config()` returns pre-flight config issues
- `mimo_acoustic/export/` — FIR coefficient files and CamillaDSP YAML generation
- `mimo_acoustic/cli.py` — the command-line interface

## REW Measurement and Export Workflow

The solver inverts the measured transfer matrix at full FFT-bin resolution (no internal spectral smoothing) and depends on phase coherence between all measurements. The realism of the output is therefore decided almost entirely by how the impulse responses are measured and exported in REW. Follow these steps:

### 1. Timing reference (non-negotiable)

- Enable an **acoustic timing reference** (or soundcard loopback) in REW: Preferences -> Analysis -> "Use acoustic timing reference".
- Use the **same reference speaker** for all speaker/mic measurements in the session.
- Do **not** let REW set `t=0` at each IR peak independently. The MIMO solve sums speakers at each mic position, so the relative time-of-flight between speakers and across mic positions must be physically correct. Per-measurement peak alignment destroys this and the matrix solution becomes meaningless.

### 2. Measurement hygiene

- Keep output level and mic gain identical for every sweep; never change levels mid-session.
- Load the microphone calibration file before measuring (it is baked into the export).
- Use long sweeps (256k-512k at 96 kHz) with 2+ repetitions for SNR. The solver will happily "correct" low-frequency noise as if it were real room response, especially near the `authority_floor_db` limit.

### 3. Windowing in REW (this is your smoothing)

REW's fractional-octave smoothing only affects the displayed magnitude trace, **not** the exported impulse response. The time-domain equivalent is windowing, and this script applies none, so do it in REW before export:

- Apply a **frequency-dependent window (FDW)** of roughly **6-10 cycles** (IR Windows -> "Add frequency dependent window"). This is equivalent to ~1/6-1/10 octave complex smoothing: it discards late, position-dependent reflections at mid/high frequencies while preserving full modal detail at low frequencies. Without it, the solver inverts seat-specific high-frequency comb filtering that is wrong everywhere except at the exact mic position.
- Left window: short (about 1 ms Tukey) ahead of the reference.
- Right window: long enough for low-frequency resolution (for example 65536 samples at 96 kHz is 683 ms, matching `ir_length_samples`).
- Export with the windows applied.

### 4. Export settings

File -> Export -> "Export impulse response as WAV":

- **32-bit float**, mono.
- Native measurement sample rate (no resampling); it must match `sample_rate` in the config.
- **No normalisation.** The `auto_target_level` and acoustic-authority logic depend on consistent relative levels between speakers and mic positions.
- Same export length for all files.
- Windowed data (from step 3).

### 5. Verify after applying filters

Load the generated filters into CamillaDSP, then re-measure all mic positions in REW with the same timing reference and compare against the predicted target. Also watch the wrap-energy warning in `diagnostics.json`; if it fires, increase `fft_size` or `target_delay_ms`.

### Recommended config restraint

- Full-matrix correction of mains up to 20 kHz across spaced mic positions is not physically meaningful; high-frequency phase decorrelates over centimetres. Consider lowering `max_hz` for the main speakers to the Schroeder region (~300-500 Hz) so the matrix only handles the modal range, or rely on the FDW from step 3 to suppress high-frequency artifacts.
- Start with `max_boost_db` of 6-9 dB until verification measurements confirm the correction is benign.

## Measurement Naming

Use either an explicit `measurements` list or a pattern:

```json
"measurement_pattern": "measurements/spk_{speaker:02d}_mic_{mic:02d}.wav"
```

The pattern supports zero-based `{speaker}`, `{mic}` and one-based `{speaker1}`, `{mic1}` placeholders.

## Configuration Reference

An up-to-date example lives in `example_config.json` (regenerate any time with `--write-example-config`). All parameters by group:

### Dimensions and measurements

| Parameter | Default | Description |
|---|---|---|
| `num_speakers` | required | Number of physical output channels (N). Speaker indices used everywhere else refer to this ordering. |
| `num_mic_positions` | required | Number of microphone positions (M) in the measurement grid. |
| `num_inputs` | `num_speakers` | Number of input channels (K), e.g. `2` for stereo sources. Produces N x K FIR filters. |
| `sample_rate` | from WAVs | Expected sample rate. Optional for WAV input (read from files, mismatches rejected); required for text IRs without a time column. |
| `measurements` | — | Explicit list of `{speaker, mic, path}` entries, one IR per speaker/mic pair. |
| `measurement_pattern` | — | Alternative to `measurements`: filename template such as `"measurements/spk_{speaker:02d}_mic_{mic:02d}.wav"`. |
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

Choose `target_delay_ms` large enough to make the inverse causal. If diagnostics warn about wrap-point energy, increase `target_delay_ms`, `fft_size`, or `filter_taps`.

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
