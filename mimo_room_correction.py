#!/usr/bin/env python3
"""Backwards-compatible entry point for the MIMO room-correction solver.

The implementation lives in the `mimo_acoustic` package; this module re-exports
the public API so existing imports (`import mimo_room_correction`) and the
original command line keep working:

    python3 mimo_room_correction.py --config config_ir.json

See `mimo_acoustic/__init__.py` for the mathematical model description.
"""

from __future__ import annotations

from mimo_acoustic import (  # noqa: F401
    EPS,
    ExportPaths,
    ProgressFn,
    SolveCancelled,
    SolveDiagnostics,
    SolveResult,
    SpeakerProfile,
    _build_log_smoothing_grid,
    _smooth_complex_spectrum,
    amplitude_to_db,
    apply_row_sum_cap,
    build_anchored_target_matrix,
    build_profile_weights,
    build_target_matrix,
    cap_complex_magnitude,
    compute_gain_diagnostics,
    crop_or_pad_ir,
    db_to_amplitude,
    db_to_power,
    enforce_final_fir_gain_cap,
    estimate_reference_power,
    export,
    export_firs,
    fractional_octave_complex_smooth,
    frequency_matrix_to_firs,
    generate_camilladsp_yaml,
    load_impulse_response,
    load_measurement_matrix,
    load_text_ir,
    load_wav_ir,
    measurement_path_grid,
    next_power_of_two,
    parse_input_primary_speakers,
    parse_input_speaker_mask,
    parse_numeric_text,
    parse_speaker_profiles,
    parse_target_mic_matrix,
    raised_sine_band_weight,
    read_config,
    resolve_path,
    resolve_target_level,
    smooth_solution_matrix,
    solve,
    solve_from_room_irs,
    solve_frequency_domain_filters,
    target_curve_amplitude,
    validate_config,
    yaml_quote,
)
from mimo_acoustic.cli import (  # noqa: F401
    build_arg_parser,
    main,
    run_pipeline,
    run_smoke_test,
    synthetic_room_irs,
    write_example_config,
)

if __name__ == "__main__":
    raise SystemExit(main())
