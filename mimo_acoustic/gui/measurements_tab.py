"""Measurements tab: speaker x mic file grid with validation.

Per-client class so refreshable targets never outlive their client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from nicegui import run, ui

from ..core.io import load_impulse_response, measurement_path_grid
from .file_picker import pick_directory, pick_file
from .state import STATE


class MeasurementsTab:
    def build(self) -> None:
        with ui.column().classes("w-full max-w-6xl gap-2"):
            self._mode_bar()
            self._grid_section()
            self._validation_section()

    def refresh(self) -> None:
        self._mode_bar.refresh()
        self._grid_section.refresh()
        self._validation_section.refresh()

    @ui.refreshable_method
    def _mode_bar(self) -> None:
        explicit = "measurements" in STATE.config or "measurement_pattern" not in STATE.config

        def set_mode(event) -> None:
            if event.value == "explicit":
                STATE.config.pop("measurement_pattern", None)
                STATE.config.setdefault("measurements", [])
            else:
                STATE.config.pop("measurements", None)
                STATE.config.setdefault(
                    "measurement_pattern", "measurements/spk_{speaker:02d}_mic_{mic:02d}.wav"
                )
            self.refresh()

        with ui.row().classes("items-center gap-4"):
            ui.select(
                {"explicit": "Explicit file list", "pattern": "Filename pattern"},
                value="explicit" if explicit else "pattern",
                label="Measurement source",
                on_change=set_mode,
            ).classes("w-56")
            ui.label(f"Files resolve relative to: {STATE.base_dir}").classes(
                "text-xs text-gray-500"
            )

    @ui.refreshable_method
    def _grid_section(self) -> None:
        if "measurement_pattern" in STATE.config and "measurements" not in STATE.config:
            ui.input(
                "Pattern (placeholders: {speaker} {mic} {speaker1} {mic1})",
                value=STATE.config.get("measurement_pattern", ""),
                on_change=lambda e: STATE.config.__setitem__("measurement_pattern", e.value),
            ).classes("w-full max-w-2xl")
            return

        STATE.config.setdefault("measurements", [])
        grid = STATE.measurement_grid()
        profiles = STATE.config["speaker_profiles"]
        speakers = STATE.num_speakers()
        mics = STATE.num_mics()

        async def fill_from_folder() -> None:
            folder = await pick_directory(
                STATE.base_dir, title="Folder containing measurement files"
            )
            if folder is None:
                return
            self._fill_dialog(Path(folder))

        ui.button("Assign from folder…", icon="folder", on_click=fill_from_folder).props("flat")

        with ui.grid(columns=speakers + 1).classes("gap-1 items-center w-full"):
            ui.label("")
            for speaker in range(speakers):
                ui.label(f"{speaker}: {profiles[str(speaker)]['name']}").classes(
                    "text-xs font-medium"
                )
            for mic in range(mics):
                ui.label(f"Mic {mic}").classes("text-xs font-medium")
                for speaker in range(speakers):
                    self._grid_cell(mic, speaker, grid.get((mic, speaker), ""))

    def _grid_cell(self, mic: int, speaker: int, value: str) -> None:
        async def browse() -> None:
            start = STATE.base_dir
            current = STATE.measurement_grid().get((mic, speaker), "")
            if current:
                candidate = (STATE.base_dir / current).parent
                if candidate.is_dir():
                    start = candidate
            path = await pick_file(
                start,
                title=f"IR for mic {mic}, speaker {speaker}",
                suffixes=[".wav", ".txt"],
            )
            if path is None:
                return
            try:
                relative = str(Path(path).relative_to(STATE.base_dir))
            except ValueError:
                relative = str(path)
            STATE.set_measurement(mic, speaker, relative)
            self._grid_section.refresh()

        def on_change(event) -> None:
            STATE.set_measurement(mic, speaker, str(event.value or ""))

        with ui.row().classes("items-center gap-0 no-wrap"):
            ui.input(value=value, placeholder="path…", on_change=on_change).props(
                "dense outlined"
            ).classes("grow text-xs")
            ui.button(icon="folder_open", on_click=browse).props("flat dense")

    def _fill_dialog(self, folder: Path) -> None:
        """Assign files from a folder: per speaker column, pick the per-mic files."""
        files = sorted(
            [p for p in folder.iterdir() if p.suffix.lower() in {".wav", ".txt"}],
            key=lambda p: p.name.lower(),
        )
        if not files:
            ui.notify("No .wav or .txt files in that folder", type="warning")
            return
        profiles = STATE.config["speaker_profiles"]
        mics = STATE.num_mics()
        names = [p.name for p in files]

        with ui.dialog(value=True) as dialog, ui.card().classes("w-[44rem] max-w-full"):
            ui.label(f"Assign files from {folder}").classes("font-medium")
            ui.label(
                "Pick the file for each speaker/mic pair. Fields left empty are not changed."
            ).classes("text-xs text-gray-500")
            selects: dict[tuple[int, int], Any] = {}
            with ui.scroll_area().classes("h-96 w-full"):
                for speaker in range(STATE.num_speakers()):
                    ui.label(f"Speaker {speaker}: {profiles[str(speaker)]['name']}").classes(
                        "font-medium mt-2"
                    )
                    for mic in range(mics):
                        selects[(mic, speaker)] = ui.select(
                            names, label=f"Mic {mic}", with_input=True, clearable=True
                        ).classes("w-full")

            def apply() -> None:
                for (mic, speaker), select in selects.items():
                    if select.value:
                        try:
                            relative = str((folder / select.value).relative_to(STATE.base_dir))
                        except ValueError:
                            relative = str(folder / select.value)
                        STATE.set_measurement(mic, speaker, relative)
                dialog.close()
                self._grid_section.refresh()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Apply", on_click=apply)

    # ------------------------------------------------------------------
    # Validation

    @ui.refreshable_method
    def _validation_section(self) -> None:
        with ui.row().classes("items-center gap-2"):
            ui.button("Validate measurements", icon="rule", on_click=self._run_validation)
        self._validation_results()

    @ui.refreshable_method
    def _validation_results(
        self, rows: Optional[list[dict[str, Any]]] = None, summary: str = ""
    ) -> None:
        if rows is None:
            return
        if summary:
            ui.label(summary).classes("font-medium")
        ui.table(
            columns=[
                {"name": "cell", "label": "Mic / Speaker", "field": "cell", "align": "left"},
                {"name": "path", "label": "File", "field": "path", "align": "left"},
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
            ],
            rows=rows,
            row_key="cell",
        ).classes("w-full text-xs")

    async def _run_validation(self) -> None:
        config = dict(STATE.config)
        base_dir = STATE.base_dir
        ui.notify("Validating…", type="info")
        rows, summary = await run.io_bound(_validate_files, config, base_dir)
        self._validation_results.refresh(rows, summary)


def _validate_files(config: dict[str, Any], base_dir: Path) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    try:
        grid = measurement_path_grid(config, base_dir)
    except (ValueError, KeyError) as exc:
        return [{"cell": "—", "path": "—", "status": f"GRID ERROR: {exc}"}], "Grid invalid"

    configured_rate = config.get("sample_rate")
    configured_rate = int(configured_rate) if configured_rate else None
    wav_channel = int(config.get("wav_channel", 0))
    sample_rate = configured_rate
    errors = 0
    for mic, row in enumerate(grid):
        for speaker, path in enumerate(row):
            cell = f"mic {mic} / spk {speaker}"
            try:
                fs, impulse = load_impulse_response(path, configured_rate, wav_channel)
                if sample_rate is None:
                    sample_rate = fs
                if fs != sample_rate:
                    raise ValueError(f"sample rate {fs} != {sample_rate}")
                import numpy as np

                if not np.all(np.isfinite(impulse)):
                    raise ValueError("non-finite samples")
                status = f"OK — {fs} Hz, {impulse.size} samples ({impulse.size / fs:.2f} s)"
            except FileNotFoundError:
                status = "MISSING"
                errors += 1
            except (ValueError, OSError) as exc:
                status = f"ERROR: {exc}"
                errors += 1
            rows.append({"cell": cell, "path": str(path), "status": status})
    total = len(rows)
    summary = (
        f"All {total} measurements OK"
        if errors == 0
        else f"{errors} of {total} measurements have problems"
    )
    return rows, summary
