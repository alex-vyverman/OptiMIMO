"""MIMO room-correction FIR matrix solver for REW measurements and CamillaDSP.

This package builds a frequency-domain room transfer matrix from individual
speaker-to-mic impulse responses, solves a regularized MIMO inverse at every
FFT bin, transforms the result to time-domain FIR filters, and writes a
CamillaDSP branch/filter/sum YAML snippet.

Mathematical model at one frequency bin f:

    H_f: M x N room transfer matrix, microphones x speakers
    X_f: N x K FIR matrix to solve, speakers x input channels
    Y_f: M x K desired target matrix, microphones x input channels

The optimizer is:

    minimize_X || Wm^(1/2) (H_f X_f - Y_f) ||_F^2
             + || Gamma_f^(1/2) X_f ||_F^2

with the closed-form solution:

    X_f = (H_f^H Wm H_f + Gamma_f)^-1 H_f^H Wm Y_f

where Gamma_f is diagonal and frequency-dependent. Each speaker's diagonal
regularization term is increased outside its configured safe operating band
and when its measured acoustic authority is weak. A hard frequency-domain
boost cap is applied after solving as a final protection layer.

The default K is N, producing N x N FIR filters suitable for a matrix pipeline.
"""

from __future__ import annotations

from .core.io import (
    crop_or_pad_ir,
    load_impulse_response,
    load_measurement_matrix,
    load_target_curve_ir,
    load_target_curve_text,
    load_text_ir,
    load_wav_ir,
    measurement_path_grid,
    parse_numeric_text,
    read_config,
    resolve_path,
    resolve_target_curve,
)
from .core.pipeline import (
    ExportPaths,
    ProgressFn,
    SolveCancelled,
    SolveDiagnostics,
    SolveResult,
    export,
    solve,
    solve_from_room_irs,
    validate_config,
)
from .core.smoothing import (
    _build_log_smoothing_grid,
    _smooth_complex_spectrum,
    fractional_octave_complex_smooth,
    smooth_solution_matrix,
)
from .core.solver import (
    SpeakerProfile,
    apply_row_sum_cap,
    build_profile_weights,
    cap_complex_magnitude,
    compute_gain_diagnostics,
    enforce_final_fir_gain_cap,
    frequency_matrix_to_firs,
    parse_input_speaker_mask,
    parse_speaker_profiles,
    raised_sine_band_weight,
    solve_frequency_domain_filters,
)
from .core.targets import (
    build_anchored_target_matrix,
    build_target_matrix,
    estimate_reference_power,
    parse_input_primary_speakers,
    parse_target_mic_matrix,
    resolve_target_level,
    target_curve_amplitude,
)
from .export.camilladsp import generate_camilladsp_yaml, yaml_quote
from .export.firs import export_firs
from .util import (
    EPS,
    amplitude_to_db,
    db_to_amplitude,
    db_to_power,
    next_power_of_two,
)
