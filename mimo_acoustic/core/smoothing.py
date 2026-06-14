"""Fractional-octave complex smoothing on a log-frequency grid."""

from __future__ import annotations

import math
import threading
from typing import Callable

import numpy as np


class SolveCancelled(Exception):
    """Raised when a cancel event is set during a long-running computation."""


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise SolveCancelled()


def _build_log_smoothing_grid(
    freqs: np.ndarray, fraction: float
) -> tuple[np.ndarray, np.ndarray, int, float]:
    """Build the log-frequency grid and Gaussian kernel for complex smoothing."""
    f_low = max(float(freqs[1]), 0.5)
    f_high = float(freqs[-1])
    points_per_octave = max(int(round(32.0 * fraction)), 192)
    num_octaves = math.log2(f_high / f_low)
    num_points = int(math.ceil(num_octaves * points_per_octave)) + 1
    log_f = f_low * 2.0 ** (np.arange(num_points) / points_per_octave)

    # Gaussian kernel whose FWHM spans 1/fraction octave on the log grid.
    sigma = points_per_octave / (fraction * 2.354820045)
    radius = max(int(math.ceil(4.0 * sigma)), 1)
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    return log_f, kernel, radius, f_low


def _smooth_complex_spectrum(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    log_f: np.ndarray,
    kernel: np.ndarray,
    radius: int,
    f_low: float,
    delay_s: float,
) -> np.ndarray:
    """Smooth one complex spectrum after de-rotating a known bulk delay.

    Smoothing complex bins directly would attenuate any response whose group
    delay exceeds the inverse smoothing bandwidth, so the spectrum is first
    de-rotated by `delay_s`, smoothed on the log-frequency grid, and rotated
    back. The rotation is undone exactly, so absolute timing is preserved.
    """
    rotation = np.exp(2j * np.pi * freqs * delay_s)
    rotated = spectrum * rotation

    resampled = np.interp(log_f, freqs, rotated.real) + 1j * np.interp(
        log_f, freqs, rotated.imag
    )
    padded = np.pad(resampled, radius, mode="edge")
    filtered = np.convolve(padded, kernel, mode="valid")

    result = np.interp(freqs, log_f, filtered.real) + 1j * np.interp(
        freqs, log_f, filtered.imag
    )
    # Keep DC and any bins below the log grid unsmoothed.
    below = freqs < f_low
    result[below] = rotated[below]
    return result * np.conj(rotation)


def fractional_octave_complex_smooth(
    h_freq: np.ndarray,
    freqs: np.ndarray,
    room_irs: np.ndarray,
    sample_rate: int,
    fraction: float,
    *,
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> np.ndarray:
    """Apply fractional-octave complex smoothing to the room transfer matrix.

    Each measurement is de-rotated by its own direct-sound arrival time
    (estimated from the impulse peak) before smoothing, then rotated back, so
    relative phase between speakers and microphone positions is preserved.
    This is the frequency-domain equivalent of a frequency-dependent window
    of roughly `fraction` cycles centered on each IR's arrival.
    """
    if fraction <= 0.0:
        return h_freq

    num_freqs, num_mics, num_speakers = h_freq.shape
    if num_freqs < 4:
        return h_freq

    log_f, kernel, radius, f_low = _build_log_smoothing_grid(freqs, fraction)
    smoothed = np.array(h_freq, copy=True)
    total = num_mics * num_speakers
    for mic in range(num_mics):
        for speaker in range(num_speakers):
            _check_cancel(cancel)
            impulse = room_irs[mic, speaker]
            arrival_s = int(np.argmax(np.abs(impulse))) / float(sample_rate)
            smoothed[:, mic, speaker] = _smooth_complex_spectrum(
                h_freq[:, mic, speaker], freqs, log_f, kernel, radius, f_low, arrival_s
            )
            if progress is not None:
                progress((mic * num_speakers + speaker + 1) / total)

    smoothed[0, :, :] = np.real(smoothed[0, :, :])
    return smoothed


def smooth_solution_matrix(
    x_freq: np.ndarray,
    freqs: np.ndarray,
    fraction: float,
    delay_s: float,
    fft_size: int,
    *,
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> np.ndarray:
    """Apply fractional-octave complex smoothing to the solved FIR matrix.

    The solver introduces sharp spectral transitions at speaker band edges and
    regularization boundaries; those features ring for seconds in the time
    domain. Smoothing the solution (de-rotated by the bulk target delay)
    bounds the Q of every filter feature and forces a fast, Gaussian-shaped
    time decay, making the FIRs fit within the configured tap count.
    """
    if fraction <= 0.0:
        return x_freq

    num_freqs = x_freq.shape[0]
    if num_freqs < 4:
        return x_freq

    log_f, kernel, radius, f_low = _build_log_smoothing_grid(freqs, fraction)
    smoothed = np.array(x_freq, copy=True)
    total = x_freq.shape[1] * x_freq.shape[2]
    for out_ch in range(x_freq.shape[1]):
        for in_ch in range(x_freq.shape[2]):
            _check_cancel(cancel)
            smoothed[:, out_ch, in_ch] = _smooth_complex_spectrum(
                x_freq[:, out_ch, in_ch], freqs, log_f, kernel, radius, f_low, delay_s
            )
            if progress is not None:
                progress((out_ch * x_freq.shape[2] + in_ch + 1) / total)

    smoothed[0, :, :] = np.real(smoothed[0, :, :])
    if (num_freqs - 1) * 2 == fft_size:
        smoothed[-1, :, :] = np.real(smoothed[-1, :, :])
    return smoothed
