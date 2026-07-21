"""Canonical application defaults: the single source of truth for GUI field values.

The solver core keeps its own conservative *library* fallbacks (the defaults
documented in the README Configuration Reference, used when a config file omits
a key). The GUI ships an opinionated application preset on top: every Config
tab field takes its default from here so the field factories cannot drift from
the preset again. ``example_config()`` is that preset; ``_GUI_OVERRIDES``
covers keys whose GUI default must differ from the preset's pinned value, and
keys the preset does not contain at all.
"""

from __future__ import annotations

from typing import Any

from .cli import example_config

_GUI_OVERRIDES: dict[str, Any] = {
    # 0 means "auto" for these fields; the example preset pins explicit values
    # matching its own dimensions, which must not leak into other projects.
    "fft_size": 0,
    "ir_length_samples": 0,
    # Keys the example preset does not contain:
    "ir_crop_start_ms": 0.0,
    "camilladsp_filter_path_prefix": "",
    "camilladsp_absolute_paths": False,
    "target_curve_ir_smoothing_fraction": 6.0,
    "target_level_linear": 1.0,
}


def app_default(key: str, fallback: Any = None) -> Any:
    """Default value for ``key`` in the application preset.

    GUI field factories should call this instead of hard-coding a default, so
    the Config tab always agrees with ``example_config()``. ``fallback`` is
    only a last resort for keys unknown to both the preset and the overrides.
    """
    if key in _GUI_OVERRIDES:
        return _GUI_OVERRIDES[key]
    return example_config().get(key, fallback)
