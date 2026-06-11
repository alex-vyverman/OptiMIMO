# MIMO Room Correction FIR Matrix Solver

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
