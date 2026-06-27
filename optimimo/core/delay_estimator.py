"""Estimate the minimum target_delay_ms tolerable for a measurement set.

The inverse of a causal IR is acausal — its group delay is the negative
of the IR's. Embedding a bulk delay into the target shifts the inverse
forward in time so it can be a stable causal FIR. The minimum delay
needed is roughly the worst-case group delay of the measured matrix
inside each speaker's correction band, plus a margin so the inverse
can develop without spilling into the FFT wrap point.

The estimator mirrors the solver's preprocessing: when
``h_smoothing_fraction`` is set, the same complex smoothing is applied
to the spectrum before group delay is read off. With heavy smoothing
the inverse the solver actually computes is much smoother than the
raw measurements imply, so a smoothing-aware estimate is materially
smaller (and closer to what the solve will tolerate).

``x_smoothing_fraction`` happens after the solve and bounds FIR Q rather
than changing the inverse's required bulk delay, so it does not enter
the estimate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np

from ..util import next_power_of_two
from .io import compute_measurement_arrivals, load_measurement_matrix
from .smoothing import fractional_octave_complex_smooth


def suggest_target_delay_ms(
    config: Mapping[str, Any],
    base_dir: Path,
    *,
    quantile: float = 0.99,
    flat_margin_ms: float = 20.0,
    anchored_margin_ms: float = 10.0,
    min_nfft: int = 8192,
) -> dict[str, Any]:
    """Estimate the minimum tolerable target_delay_ms from measurements.

    For each (speaker, mic) pair, computes the group delay tau_g(f) of
    the measured IR and takes a robust max across the speaker's
    configured correction band (speaker_profiles[s].min_hz..max_hz).
    Aggregates the overall max across pairs, then adds a mode-dependent
    margin so the inverse has room to develop.

    The robust max is a high quantile (default 99th percentile) rather
    than the absolute maximum: at spectral nulls the phase derivative is
    dominated by noise and produces meaningless spikes that the
    quantile rejects.

    Returns:
        A dict with:
        - max_group_delay_ms: raw worst-case tau_g (no margin)
        - recommended_ms: max_group_delay_ms + mode-appropriate margin
        - margin_ms: the margin applied
        - target_mode: "flat" or "anchored", driving the margin choice
        - current_ms: the target_delay_ms currently in the config
        - constrained_by_fft: True when recommended_ms exceeds the
          headroom (fft_size - filter_taps) the current config allows
        - max_delay_budget_ms: that budget when fft_size > 0, else None
        - per_measurement: [{speaker, mic, max_group_delay_ms}, ...]
        - sample_rate: detected sample rate
        - issues: human-readable caveats (missing measurements, etc.)
    """
    sample_rate, ir_matrix = load_measurement_matrix(config, base_dir)
    num_mics, num_speakers, ir_length = ir_matrix.shape
    arrivals = compute_measurement_arrivals(config, sample_rate)

    # Match the solver's FFT resolution so the smoothing produces the
    # same spectrum the solver will see. Use the configured fft_size
    # when present, otherwise the same default the pipeline derives.
    filter_taps = int(config.get("filter_taps", 8192))
    configured_fft_size = int(config.get("fft_size", 0) or 0)
    if configured_fft_size > 0:
        nfft = configured_fft_size
    else:
        nfft = max(min_nfft, next_power_of_two(ir_length + filter_taps - 1))

    freqs = np.fft.rfftfreq(nfft, d=1.0 / sample_rate)
    omega = 2.0 * np.pi * freqs

    # Build the same spectrum the solver builds: rfft over time, then
    # pivot the frequency axis to the front. Apply h smoothing here if
    # configured, with the same de-rotation by direct-arrival time.
    h_freq = np.fft.rfft(ir_matrix, n=nfft, axis=2)
    h_freq = np.moveaxis(h_freq, 2, 0)
    h_freq = np.ascontiguousarray(h_freq)
    smoothing_fraction = float(config.get("h_smoothing_fraction", 0.0) or 0.0)
    smoothing_applied = False
    if smoothing_fraction > 0.0:
        h_freq = fractional_octave_complex_smooth(
            h_freq, freqs, ir_matrix, sample_rate, smoothing_fraction, arrivals=arrivals
        )
        smoothing_applied = True

    profiles = config.get("speaker_profiles", {}) or {}
    issues: list[str] = []
    per_measurement: list[dict[str, Any]] = []
    max_gd_ms = 0.0

    for s in range(num_speakers):
        profile = profiles.get(str(s), {}) or {}
        min_hz = float(profile.get("min_hz", 0.0) or 0.0)
        max_hz_raw = profile.get("max_hz")
        max_hz = float(max_hz_raw) if max_hz_raw not in (None, 0) else sample_rate / 2.0
        if max_hz <= min_hz:
            issues.append(
                f"speaker {s} ({profile.get('name', '?')}): "
                f"min_hz >= max_hz; using full band for the estimate"
            )
            min_hz, max_hz = 0.0, sample_rate / 2.0
        band_mask = (freqs >= min_hz) & (freqs <= max_hz)
        if not band_mask.any():
            continue

        for m in range(num_mics):
            spectrum = h_freq[:, m, s]
            if not np.any(spectrum):
                continue
            phase = np.unwrap(np.angle(spectrum))
            gd_seconds = -np.gradient(phase, omega)
            band_gd_ms = gd_seconds[band_mask] * 1000.0
            if band_gd_ms.size == 0:
                continue
            gd_robust = float(np.quantile(band_gd_ms, quantile))
            # Prefer the robust supplied arrival (e.g. REW's reported IR peak);
            # fall back to argmax, which is unreliable for subwoofers.
            if np.isfinite(arrivals[m, s]):
                arrival_ms = float(arrivals[m, s] * 1000.0)
            else:
                ir = ir_matrix[m, s, :]
                arrival_ms = float(int(np.argmax(np.abs(ir))) / sample_rate * 1000.0)
            per_measurement.append(
                {
                    "speaker": s,
                    "mic": m,
                    "max_group_delay_ms": gd_robust,
                    "direct_arrival_ms": arrival_ms,
                }
            )
            if gd_robust > max_gd_ms:
                max_gd_ms = gd_robust

    if not per_measurement:
        issues.append("no usable measurements; estimate is zero")

    target_mode = str(config.get("target_mode", "flat")).lower()
    margin = anchored_margin_ms if target_mode == "anchored" else flat_margin_ms
    recommended_ms = max_gd_ms + margin
    current_ms = float(config.get("target_delay_ms", 40.0))

    fft_size = int(config.get("fft_size", 0) or 0)
    filter_taps = int(config.get("filter_taps", 8192))
    max_delay_budget_ms: float | None = None
    constrained = False
    if fft_size > 0:
        max_delay_budget_ms = max(0.0, (fft_size - filter_taps) / sample_rate * 1000.0)
        if recommended_ms > max_delay_budget_ms:
            constrained = True
            issues.append(
                f"recommended {recommended_ms:.1f} ms exceeds the "
                f"{max_delay_budget_ms:.1f} ms budget allowed by current "
                f"fft_size ({fft_size}) and filter_taps ({filter_taps}); "
                "increase fft_size or reduce filter_taps."
            )

    return {
        "max_group_delay_ms": max_gd_ms,
        "recommended_ms": recommended_ms,
        "margin_ms": margin,
        "target_mode": target_mode,
        "current_ms": current_ms,
        "constrained_by_fft": constrained,
        "max_delay_budget_ms": max_delay_budget_ms,
        "per_measurement": per_measurement,
        "sample_rate": sample_rate,
        "h_smoothing_applied": smoothing_applied,
        "h_smoothing_fraction": smoothing_fraction if smoothing_applied else 0.0,
        "issues": issues,
    }
