"""Analysis tab: measured responses, predicted result vs target, filters."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import plotly.graph_objects as go
from nicegui import ui
from plotly.subplots import make_subplots

from ..core.pipeline import SolveResult
from . import plots
from .state import STATE

_SUBPLOT_LAYOUT = dict(
    margin=dict(l=50, r=20, t=30, b=40),
    height=780,
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
    template="plotly_white",
)

_SINGLE_LAYOUT = dict(
    margin=dict(l=50, r=20, t=30, b=40),
    height=420,
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
    template="plotly_white",
)

_achieved_cache: dict[str, Any] = {"result_id": None, "achieved": None}
_ref_mag_cache: dict[str, Any] = {"result_id": None, "ref_mag": None}

_axis_limits: dict[str, Optional[float]] = {
    "mag_min": -30.0,
    "mag_max": 15.0,
    "gd_min": -5.0,
    "gd_max": 50.0,
}


def _achieved(result: SolveResult) -> np.ndarray:
    if _achieved_cache["result_id"] != id(result):
        _achieved_cache["achieved"] = plots.achieved_response(result)
        _achieved_cache["result_id"] = id(result)
    return _achieved_cache["achieved"]


def _target_reference_magnitude(result: SolveResult) -> np.ndarray:
    """Compute reference magnitude from target matrix for 0 dB normalization.
    
    Returns median magnitude in the reference band (default 20-200 Hz) from
    the target matrix y_freq, shape (F,).
    """
    if _ref_mag_cache["result_id"] != id(result):
        config = result.config
        ref_band = config.get("reference_band_hz", [20.0, 200.0])
        low, high = float(ref_band[0]), float(ref_band[1])
        mask = (result.freqs >= low) & (result.freqs <= high)
        if not np.any(mask):
            mask = result.freqs > 0.0
        
        # Compute median magnitude across all mics and inputs in reference band
        y_mag = np.abs(result.y_freq[mask])
        ref_mag = np.median(y_mag) if y_mag.size > 0 else 1.0
        
        # Broadcast to full frequency axis
        _ref_mag_cache["ref_mag"] = np.full(result.freqs.shape, ref_mag)
        _ref_mag_cache["result_id"] = id(result)
    return _ref_mag_cache["ref_mag"]


def _make_freq_subplots() -> go.Figure:
    return make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        subplot_titles=("Magnitude", "Phase", "Group delay"),
        row_heights=[0.4, 0.3, 0.3],
    )


def _apply_axis_limits(figure: go.Figure) -> None:
    mag_range = None
    if _axis_limits["mag_min"] is not None and _axis_limits["mag_max"] is not None:
        mag_range = [_axis_limits["mag_min"], _axis_limits["mag_max"]]
    gd_range = None
    if _axis_limits["gd_min"] is not None and _axis_limits["gd_max"] is not None:
        gd_range = [_axis_limits["gd_min"], _axis_limits["gd_max"]]
    if mag_range is not None:
        figure.update_yaxes(range=mag_range, row=1, col=1)
    if gd_range is not None:
        figure.update_yaxes(range=gd_range, row=3, col=1)


def _style_freq_subplots(figure: go.Figure) -> None:
    figure.update_layout(**_SUBPLOT_LAYOUT)
    figure.update_xaxes(type="log", range=[np.log10(15), np.log10(22000)])
    figure.update_xaxes(title_text="Frequency (Hz)", row=3, col=1)
    figure.update_yaxes(title_text="dB (re target)", row=1, col=1)
    figure.update_yaxes(title_text="Phase (deg)", row=2, col=1)
    figure.update_yaxes(title_text="Group delay (ms)", row=3, col=1)
    _apply_axis_limits(figure)


def _add_freq_traces(
    figure: go.Figure,
    freqs: np.ndarray,
    indices: np.ndarray,
    spectrum: np.ndarray,
    name: str,
    *,
    full_freqs: Optional[np.ndarray] = None,
    ref_magnitude: Optional[np.ndarray] = None,
    dash: Optional[str] = None,
    legendgroup: Optional[str] = None,
    showlegend: bool = True,
) -> None:
    line_kw = dict(dash=dash) if dash else {}
    
    # Normalize magnitude relative to target if reference provided
    if ref_magnitude is not None:
        mag_values = plots.magnitude_db(spectrum[indices] / ref_magnitude[indices])
    else:
        mag_values = plots.magnitude_db(spectrum[indices])
    
    figure.add_trace(
        go.Scatter(
            x=freqs, y=mag_values,
            name=name, mode="lines", line=line_kw,
            legendgroup=legendgroup, showlegend=showlegend,
        ),
        row=1, col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=freqs, y=plots.phase_deg(spectrum[indices]),
            name=name, mode="lines", line=line_kw,
            legendgroup=legendgroup, showlegend=False,
        ),
        row=2, col=1,
    )
    gd_freqs = full_freqs if full_freqs is not None else freqs
    gd = plots.group_delay_ms(spectrum, gd_freqs)
    figure.add_trace(
        go.Scatter(
            x=freqs, y=gd[indices],
            name=name, mode="lines", line=line_kw,
            legendgroup=legendgroup, showlegend=False,
        ),
        row=3, col=1,
    )


def _axis_controls(plot_ref: list, figure_fn) -> None:
    def on_mag_min(event) -> None:
        _axis_limits["mag_min"] = event.value
        plot_ref[0].update_figure(figure_fn())

    def on_mag_max(event) -> None:
        _axis_limits["mag_max"] = event.value
        plot_ref[0].update_figure(figure_fn())

    def on_gd_min(event) -> None:
        _axis_limits["gd_min"] = event.value
        plot_ref[0].update_figure(figure_fn())

    def on_gd_max(event) -> None:
        _axis_limits["gd_max"] = event.value
        plot_ref[0].update_figure(figure_fn())

    with ui.row().classes("items-end gap-3"):
        ui.number("Mag min dB", value=_axis_limits["mag_min"], format="%.1f",
                   on_change=on_mag_min).classes("w-24")
        ui.number("Mag max dB", value=_axis_limits["mag_max"], format="%.1f",
                   on_change=on_mag_max).classes("w-24")
        ui.number("GD min ms", value=_axis_limits["gd_min"], format="%.1f",
                   on_change=on_gd_min).classes("w-24")
        ui.number("GD max ms", value=_axis_limits["gd_max"], format="%.1f",
                   on_change=on_gd_max).classes("w-24")


class AnalysisTab:
    def build(self) -> None:
        with ui.column().classes("w-full max-w-6xl gap-2"):
            with ui.row().classes("items-center gap-3"):
                ui.button("Refresh plots", icon="refresh", on_click=self.refresh)
                ui.label(
                    "Plots are computed from the most recent solve on the Run tab."
                ).classes("text-xs text-gray-500")
            self._analysis_content()

    def refresh(self) -> None:
        self._analysis_content.refresh()

    @ui.refreshable_method
    def _analysis_content(self) -> None:
        result = STATE.result
        if result is None:
            ui.label("No solve results yet — run the solver first.").classes(
                "text-sm text-gray-500"
            )
            return

        with ui.tabs().classes("w-full") as subtabs:
            measured = ui.tab("Measured")
            predicted = ui.tab("Predicted")
            filters = ui.tab("Filters")
        with ui.tab_panels(subtabs, value=predicted).classes("w-full"):
            with ui.tab_panel(measured):
                _measured_panel(result)
            with ui.tab_panel(predicted):
                _predicted_panel(result)
            with ui.tab_panel(filters):
                _filters_panel(result)


# ----------------------------------------------------------------------
# Measured


def _measured_panel(result: SolveResult) -> None:
    names = plots.speaker_names(result)
    options = {index: f"{index}: {name}" for index, name in enumerate(names)}
    state: dict[str, Any] = {"speaker": 0}

    def figure_fn():
        return _measured_figure(result, state["speaker"])

    plot_ref = [ui.plotly(figure_fn()).classes("w-full")]

    def update(event) -> None:
        state["speaker"] = int(event.value)
        plot_ref[0].update_figure(figure_fn())

    with ui.row().classes("items-end gap-4"):
        ui.select(options, value=0, label="Speaker", on_change=update).classes("w-64")
    _axis_controls(plot_ref, figure_fn)
    ui.label(
        "Measured response of the selected speaker at every mic position "
        "(after H smoothing)."
    ).classes("text-xs text-gray-500")


def _measured_figure(result: SolveResult, speaker: int) -> go.Figure:
    indices = plots.log_frequency_indices(result.freqs)
    freqs = result.freqs[indices]
    ref_mag = _target_reference_magnitude(result)
    figure = _make_freq_subplots()
    for mic in range(result.h_freq.shape[1]):
        _add_freq_traces(
            figure, freqs, indices,
            result.h_freq[:, mic, speaker],
            name=f"mic {mic}",
            full_freqs=result.freqs,
            ref_magnitude=ref_mag,
            legendgroup=f"mic{mic}",
        )
    _style_freq_subplots(figure)
    return figure


# ----------------------------------------------------------------------
# Predicted


def _predicted_panel(result: SolveResult) -> None:
    num_inputs = result.y_freq.shape[2]
    num_mics = result.h_freq.shape[1]
    state: dict[str, Any] = {"input": 0, "mics": [0]}

    def figure_fn():
        return _predicted_figure(result, state["input"], state["mics"])

    plot_ref = [ui.plotly(figure_fn()).classes("w-full")]

    def update() -> None:
        plot_ref[0].update_figure(figure_fn())

    def on_input(event) -> None:
        state["input"] = int(event.value)
        update()

    def on_mics(event) -> None:
        values = event.value if isinstance(event.value, list) else [event.value]
        state["mics"] = sorted(int(v) for v in values) or [0]
        update()

    with ui.row().classes("items-end gap-4"):
        ui.select(
            {k: f"input {k}" for k in range(num_inputs)},
            value=0,
            label="Input",
            on_change=on_input,
        ).classes("w-40")
        ui.select(
            {m: f"mic {m}" for m in range(num_mics)},
            value=[0],
            multiple=True,
            label="Mic positions",
            on_change=on_mics,
        ).classes("w-72")

    _axis_controls(plot_ref, figure_fn)

    ui.label("Residual error ||HX - Y||² / ||Y||² (mic-weighted)").classes(
        "font-medium mt-2"
    )
    rows = plots.residual_table(result, achieved=_achieved(result))
    columns = [{"name": "band", "label": "Band", "field": "band", "align": "left"}]
    for input_channel in range(num_inputs):
        columns.append(
            {
                "name": f"input_{input_channel}",
                "label": f"Input {input_channel}",
                "field": f"input_{input_channel}",
                "align": "right",
            }
        )
    ui.table(columns=columns, rows=rows, row_key="band").classes("w-96 text-xs")


def _predicted_figure(result: SolveResult, input_channel: int, mics: list[int]) -> go.Figure:
    achieved = _achieved(result)
    indices = plots.log_frequency_indices(result.freqs)
    freqs = result.freqs[indices]
    ref_mag = _target_reference_magnitude(result)
    figure = _make_freq_subplots()
    for mic in mics:
        _add_freq_traces(
            figure, freqs, indices,
            achieved[:, mic, input_channel],
            name=f"mic {mic} corrected",
            full_freqs=result.freqs,
            ref_magnitude=ref_mag,
            legendgroup=f"mic{mic}",
        )
        _add_freq_traces(
            figure, freqs, indices,
            result.y_freq[:, mic, input_channel],
            name=f"mic {mic} target",
            full_freqs=result.freqs,
            ref_magnitude=ref_mag,
            dash="dash",
            legendgroup=f"mic{mic}",
            showlegend=True,
        )
    _style_freq_subplots(figure)
    return figure


# ----------------------------------------------------------------------
# Filters


def _filters_panel(result: SolveResult) -> None:
    names = plots.speaker_names(result)
    num_inputs = result.firs.shape[2]
    state: dict[str, Any] = {"input": 0, "crosspoint": None}

    def figure_fn():
        return _filters_figure(result, state["input"])

    plot_ref = [ui.plotly(figure_fn()).classes("w-full")]

    def on_input(event) -> None:
        state["input"] = int(event.value)
        plot_ref[0].update_figure(figure_fn())

    ui.select(
        {k: f"input {k}" for k in range(num_inputs)},
        value=0,
        label="Input",
        on_change=on_input,
    ).classes("w-40")

    _axis_controls(plot_ref, figure_fn)

    ui.separator()
    ui.label("Impulse envelope").classes("font-medium")

    crosspoints = {
        f"{output}:{input_channel}": f"{names[output]} ← input {input_channel}"
        for output in range(result.firs.shape[1])
        for input_channel in range(num_inputs)
        if plots.filter_is_active(result.firs[:, output, input_channel])
    }
    if not crosspoints:
        ui.label("All filters are zero.").classes("text-sm text-gray-500")
        return
    first_key = next(iter(crosspoints))
    impulse_plot = ui.plotly(_impulse_figure(result, first_key)).classes("w-full")
    metrics_label = ui.label(_impulse_metrics(result, first_key)).classes(
        "text-xs text-gray-600"
    )

    def on_crosspoint(event) -> None:
        impulse_plot.update_figure(_impulse_figure(result, event.value))
        metrics_label.set_text(_impulse_metrics(result, event.value))

    ui.select(
        crosspoints, value=first_key, label="Filter", on_change=on_crosspoint
    ).classes("w-72")


def _filters_figure(result: SolveResult, input_channel: int) -> go.Figure:
    names = plots.speaker_names(result)
    indices = plots.log_frequency_indices(result.freqs)
    freqs = result.freqs[indices]
    ref_mag = _target_reference_magnitude(result)
    figure = _make_freq_subplots()
    for output in range(result.x_freq.shape[1]):
        if not plots.filter_is_active(result.firs[:, output, input_channel]):
            continue
        _add_freq_traces(
            figure, freqs, indices,
            result.x_freq[:, output, input_channel],
            name=names[output],
            full_freqs=result.freqs,
            ref_magnitude=ref_mag,
            legendgroup=f"spk{output}",
        )
    _style_freq_subplots(figure)
    return figure


def _parse_crosspoint(key: str) -> tuple[int, int]:
    output, input_channel = key.split(":")
    return int(output), int(input_channel)


def _impulse_figure(result: SolveResult, key: str) -> go.Figure:
    output, input_channel = _parse_crosspoint(key)
    fir = result.firs[:, output, input_channel]
    time_s, envelope = plots.impulse_envelope(fir, result.sample_rate)
    delay_s = float(result.config.get("target_delay_ms", 40.0)) / 1000.0
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=time_s * 1000.0, y=envelope, mode="lines", name="|FIR| envelope"))
    figure.add_vline(
        x=delay_s * 1000.0,
        line_dash="dash",
        line_color="gray",
        annotation_text="target delay",
    )
    figure.update_layout(
        **_SINGLE_LAYOUT,
        xaxis=dict(title="Time (ms)"),
        yaxis=dict(title="Envelope (dB)"),
    )
    return figure


def _impulse_metrics(result: SolveResult, key: str) -> str:
    output, input_channel = _parse_crosspoint(key)
    fir = result.firs[:, output, input_channel]
    delay_s = float(result.config.get("target_delay_ms", 40.0)) / 1000.0
    pre = plots.pre_delay_energy_ratio_db(fir, result.sample_rate, delay_s)
    return (
        f"Pre-delay energy (before 90% of target delay): {pre:.1f} dB of total. "
        "Values above about -30 dB suggest audible pre-ringing."
    )
