"""Run tab: validate, solve with progress and cancel, export results.

Per-client class so refreshable targets never outlive their client.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

from nicegui import run, ui

from ..core.io import load_measurement_matrix, resolve_target_curve
from ..core.pipeline import SolveCancelled, export, solve, validate_config
from .config_tab import apply_pending_fields
from .state import STATE

if TYPE_CHECKING:
    from .analysis_tab import AnalysisTab


class RunTab:
    def __init__(self, analysis: Optional["AnalysisTab"] = None) -> None:
        self.analysis = analysis
        self.progress_bar = None
        self.stage_label = None
        self.timer = None

    def build(self) -> None:
        with ui.column().classes("w-full max-w-5xl gap-4"):
            self._validate_section()
            self._run_section()
            self._results_section()
            # Start the progress polling timer outside the refreshable section
            self._start_progress_timer()

    # ------------------------------------------------------------------
    # Validation

    @ui.refreshable_method
    def _validate_section(self) -> None:
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-3 mb-3"):
                ui.icon("rule").classes("text-xl").style("color: #00E5FF; filter: drop-shadow(0 0 4px rgba(0, 229, 255, 0.4));")
                ui.label("Validate").classes("text-lg font-medium")
            ui.button("Validate config", icon="check_circle", on_click=self._do_validate)
            self._validate_results()

    @ui.refreshable_method
    def _validate_results(self, issues: Optional[list[tuple[str, str]]] = None) -> None:
        if issues is None:
            return
        if not issues:
            with ui.row().classes("items-center gap-2 mt-3"):
                ui.icon("check_circle").classes("text-positive")
                ui.label("Config is valid.").classes("text-positive font-medium")
            return
        with ui.column().classes("gap-1 mt-3"):
            for field, message in issues:
                with ui.row().classes("items-start gap-2"):
                    ui.icon("error").classes("text-negative text-sm mt-0.5")
                    ui.label(f"{field}: {message}").classes("text-negative text-sm")

    def _do_validate(self) -> None:
        apply_pending_fields()
        issues = validate_config(STATE.config)
        self._validate_results.refresh(issues)
        if issues:
            ui.notify(f"{len(issues)} issue(s) found", type="negative")
        else:
            ui.notify("Config is valid", type="positive")

    # ------------------------------------------------------------------
    # Solve

    @ui.refreshable_method
    def _run_section(self) -> None:
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-3 mb-3"):
                ui.icon("play_circle").classes("text-xl").style("color: #00E5FF; filter: drop-shadow(0 0 4px rgba(0, 229, 255, 0.4));")
                ui.label("Solve").classes("text-lg font-medium")
            with ui.row().classes("items-center gap-3"):
                run_button = ui.button("Run solver", icon="play_arrow", on_click=self._do_solve)
                cancel_button = ui.button("Cancel", icon="stop", on_click=self._do_cancel).props(
                    "flat"
                )
                run_button.bind_enabled_from(STATE, "running", backward=lambda r: not r)
                cancel_button.bind_enabled_from(STATE, "running")

            # Create progress elements and store references
            if self.progress_bar is None:
                self.progress_bar = ui.linear_progress(value=0.0, show_value=False).classes("w-full mt-3")
                self.stage_label = ui.label("").classes("text-sm text-gray-500 mt-1")

    def _start_progress_timer(self) -> None:
        """Start the progress polling timer outside the refreshable section."""
        def poll() -> None:
            try:
                if self.progress_bar is not None:
                    self.progress_bar.set_value(STATE.progress_fraction)
                if self.stage_label is not None:
                    if STATE.running:
                        self.stage_label.set_text(
                            f"{STATE.progress_stage} — {STATE.progress_fraction * 100.0:.0f}%"
                        )
                    elif STATE.last_error:
                        self.stage_label.set_text(STATE.last_error)
                    elif STATE.result is not None:
                        self.stage_label.set_text("Done.")
            except RuntimeError:
                if self.timer is not None:
                    self.timer.cancel()
                    self.timer = None

        if self.timer is None:
            self.timer = ui.timer(0.2, poll)

    @staticmethod
    def _do_cancel() -> None:
        if STATE.cancel_event is not None:
            STATE.cancel_event.set()

    async def _do_solve(self) -> None:
        if STATE.running:
            return
        apply_pending_fields()
        issues = validate_config(STATE.config)
        self._validate_results.refresh(issues)
        if issues:
            ui.notify("Fix config issues before running", type="negative")
            return

        STATE.running = True
        STATE.last_error = ""
        STATE.result = None
        STATE.export_paths = None
        STATE.progress_stage = "loading measurements"
        STATE.progress_fraction = 0.0
        STATE.cancel_event = threading.Event()
        config = dict(STATE.config)
        base_dir = STATE.base_dir

        def progress(stage: str, fraction: float) -> None:
            STATE.progress_stage = stage
            STATE.progress_fraction = fraction

        def work():
            sample_rate, room_irs = load_measurement_matrix(config, base_dir)
            resolve_target_curve(config, base_dir, sample_rate)
            return solve(
                room_irs,
                sample_rate,
                config,
                progress=progress,
                cancel=STATE.cancel_event,
            )

        try:
            STATE.result = await run.io_bound(work)
            ui.notify("Solve complete", type="positive")
        except SolveCancelled:
            STATE.last_error = "Cancelled."
            ui.notify("Solve cancelled", type="warning")
        except (ValueError, FileNotFoundError, OSError, KeyError) as exc:
            STATE.last_error = f"Error: {exc}"
            ui.notify(f"Solve failed: {exc}", type="negative")
        finally:
            STATE.running = False
            STATE.cancel_event = None
        self._results_section.refresh()
        if self.analysis is not None:
            self.analysis.refresh()

    # ------------------------------------------------------------------
    # Results and export

    @ui.refreshable_method
    def _results_section(self) -> None:
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-3 mb-3"):
                ui.icon("analytics").classes("text-xl").style("color: #00E5FF; filter: drop-shadow(0 0 4px rgba(0, 229, 255, 0.4));")
                ui.label("Results and export").classes("text-lg font-medium")
            result = STATE.result
            if result is None:
                ui.label("Run the solver to see diagnostics.").classes("text-sm text-gray-500")
                return

            diagnostics = result.diagnostics
            with ui.grid(columns=2).classes("gap-x-8 gap-y-2 text-sm mb-3"):
                ui.label("Sample rate").classes("text-gray-500")
                ui.label(f"{diagnostics.sample_rate} Hz").classes("font-medium")
                ui.label("FFT size / taps").classes("text-gray-500")
                ui.label(f"{diagnostics.fft_size} / {diagnostics.filter_taps}").classes("font-medium")
                ui.label("Peak individual FIR gain").classes("text-gray-500")
                ui.label(f"{diagnostics.max_filter_gain_db:.2f} dB").classes("font-medium")
                ui.label("Peak speaker row-sum gain").classes("text-gray-500")
                ui.label(f"{diagnostics.max_row_sum_gain_db:.2f} dB").classes("font-medium")
                ui.label("Filters").classes("text-gray-500")
                ui.label(
                    f"{result.firs.shape[1]} outputs × {result.firs.shape[2]} inputs, "
                    f"{result.firs.shape[0]} taps"
                ).classes("font-medium")

            if diagnostics.warnings:
                with ui.column().classes("gap-1"):
                    for warning in diagnostics.warnings:
                        with ui.row().classes("items-start gap-2"):
                            ui.icon("warning").classes("text-orange-500 text-sm mt-0.5")
                            ui.label(warning).classes("text-orange-500 text-sm")
            else:
                with ui.row().classes("items-center gap-2"):
                    ui.icon("check_circle").classes("text-positive text-sm")
                    ui.label("No warnings.").classes("text-positive text-sm")

            ui.separator()
            with ui.row().classes("items-center gap-3 mt-3"):
                ui.button("Export FIRs + YAML", icon="save", on_click=self._do_export)
                if STATE.export_paths is not None:
                    ui.button(
                        "Download YAML",
                        icon="download",
                        on_click=lambda: ui.download(STATE.export_paths.yaml_path),
                    ).props("flat")
            if STATE.export_paths is not None:
                paths = STATE.export_paths
                with ui.column().classes("gap-1 mt-3"):
                    ui.label(
                        f"Wrote {len(paths.filter_paths)} FIR filter(s) to {paths.output_dir}"
                    ).classes("text-sm text-gray-500")
                    ui.label(f"CamillaDSP snippet: {paths.yaml_path}").classes("text-sm text-gray-500")

    async def _do_export(self) -> None:
        if STATE.result is None:
            return
        try:
            STATE.export_paths = await run.io_bound(export, STATE.result, STATE.base_dir)
            ui.notify(f"Exported to {STATE.export_paths.output_dir}", type="positive")
        except (ValueError, OSError) as exc:
            ui.notify(f"Export failed: {exc}", type="negative")
        self._results_section.refresh()
