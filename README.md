# MIMO Room Correction FIR Matrix Solver

This workspace contains a foundational Python pipeline for a Dirac ART/CABS-style active room-correction matrix. It loads one REW impulse response per speaker/microphone pair, builds the frequency-domain room matrix `H(f)`, solves a regularized MIMO inverse, exports `N x N` FIR filters, and writes a CamillaDSP branch/filter/sum YAML snippet.

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

Set `output_format` to `wav` or `both` when using the generated CamillaDSP snippet. Text coefficient export is useful for inspection or import into other tools, but this snippet references WAV convolution filters.

## Important DSP Notes

Choose `target_delay_ms` large enough to make the inverse causal. If diagnostics warn about wrap-point energy, increase `target_delay_ms`, `fft_size`, or `filter_taps`.

Small-speaker protection is implemented twice: the solver removes each speaker from the optimization outside its `speaker_profiles` band, then applies gain caps after solving. Because any finite FIR has transition leakage, use realistic transition bands and sufficiently long taps for low-frequency control.
