"""Measurements tab: speaker x mic file grid with validation.

Per-client class so refreshable targets never outlive their client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from nicegui import run, ui

from ..core.io import load_impulse_response, measurement_path_grid
from ..core.rew import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    RewError,
    download_measurements_to_wavs,
    fetch_measurements,
)
from .file_picker import pick_directory, pick_file
from .state import STATE


class MeasurementsTab:
    def build(self) -> None:
        with ui.column().classes("w-full max-w-6xl gap-3"):
            self._mode_bar()
            self._validation_section()
            self._grid_section()

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

        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-3 mb-3"):
                ui.icon("mic").classes("text-xl").style("color: #00E5FF; filter: drop-shadow(0 0 4px rgba(0, 229, 255, 0.4));")
                ui.label("Measurement source").classes("text-lg font-medium")
            with ui.row().classes("items-center gap-4"):
                ui.select(
                    {"explicit": "Explicit file list", "pattern": "Filename pattern"},
                    value="explicit" if explicit else "pattern",
                    label="Source type",
                    on_change=set_mode,
                ).classes("w-56")
                ui.label(f"Files resolve relative to: {STATE.base_dir}").classes(
                    "text-xs text-gray-500"
                )

    @ui.refreshable_method
    def _grid_section(self) -> None:
        if "measurement_pattern" in STATE.config and "measurements" not in STATE.config:
            with ui.card().classes("w-full"):
                ui.input(
                    "Pattern",
                    value=STATE.config.get("measurement_pattern", ""),
                    on_change=lambda e: STATE.config.__setitem__("measurement_pattern", e.value),
                ).classes("w-full max-w-2xl")
                ui.label(
                    "Placeholders: {speaker} {mic} (0-based), {speaker1} {mic1} (1-based), "
                    "{speaker_name} (from the speaker profile), {mic_name} (from the mic "
                    "position name set on the Config tab)."
                ).classes("text-xs text-gray-500 mt-1")
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

        with ui.card().classes("w-full"):
            with ui.row().classes("items-center justify-between mb-3"):
                with ui.row().classes("items-center gap-3"):
                    ui.icon("grid_view").classes("text-xl").style("color: #00E5FF; filter: drop-shadow(0 0 4px rgba(0, 229, 255, 0.4));")
                    ui.label("Measurement grid").classes("text-lg font-medium")
                with ui.row().classes("items-center gap-2"):
                    ui.button("Import from REW…", icon="cloud_download", on_click=self._rew_import_dialog)
                    ui.button("Assign from folder…", icon="folder", on_click=fill_from_folder)

            with ui.grid(columns=speakers + 1).classes("gap-2 items-center w-full"):
                ui.label("")
                for speaker in range(speakers):
                    ui.label(f"{speaker}: {profiles[str(speaker)]['name']}").classes(
                        "text-xs font-medium text-center"
                    )
                for mic in range(mics):
                    ui.label(STATE.mic_name(mic)).classes("text-xs font-medium")
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
        speakers = STATE.num_speakers()
        mics = STATE.num_mics()
        names = [p.name for p in files]

        cells = [
            (mic, speaker, STATE.mic_name(mic), profiles[str(speaker)]["name"])
            for speaker in range(speakers)
            for mic in range(mics)
        ]
        # Filenames are both value and label here.
        candidates = [(n, n) for n in names]
        preselected = _auto_assign(candidates, cells)
        total_cells = speakers * mics

        with ui.dialog(value=True) as dialog, ui.card().classes("w-[44rem] max-w-full"):
            ui.label(f"Assign files from {folder}").classes("text-lg font-medium")
            ui.label(
                "Pick the file for each speaker/mic pair. Fields left empty are not changed."
            ).classes("text-xs text-gray-500")
            ui.label(
                f"Auto-assigned {len(preselected)} of {total_cells} cells "
                "based on matching speaker/mic names in filenames."
            ).classes("text-xs text-cyan-600 mb-3")
            selects: dict[tuple[int, int], Any] = {}

            def update_options() -> None:
                taken = {s.value for s in selects.values() if s.value}
                for select in selects.values():
                    current = select.value
                    new_options = [
                        n for n in names if n == current or n not in taken
                    ]
                    if list(select.options) != new_options:
                        select.options = new_options
                        select.update()

            def on_select_change() -> None:
                update_options()

            with ui.scroll_area().classes("h-96 w-full"):
                for speaker in range(speakers):
                    ui.label(
                        f"Speaker {speaker}: {profiles[str(speaker)]['name']}"
                    ).classes("font-medium mt-3 mb-1")
                    for mic in range(mics):
                        selects[(mic, speaker)] = ui.select(
                            list(names),
                            value=preselected.get((mic, speaker)),
                            label=STATE.mic_name(mic),
                            with_input=True,
                            clearable=True,
                            on_change=lambda _e: on_select_change(),
                        ).classes("w-full")
            update_options()

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

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Apply", icon="check", on_click=apply)

    def _rew_import_dialog(self) -> None:
        """Pull measurements from a running REW instance over its HTTP API.

        Connect to REW, assign loaded measurements to speaker/mic pairs (same
        layout as the folder-assign dialog), then download the impulse responses
        as timing-aligned WAVs and fold them into the measurement grid.
        """
        profiles = STATE.config["speaker_profiles"]
        speakers = STATE.num_speakers()
        mics = STATE.num_mics()
        host_default = str(STATE.config.get("rew_host", DEFAULT_HOST))
        port_default = int(STATE.config.get("rew_port", DEFAULT_PORT) or DEFAULT_PORT)

        selects: dict[tuple[int, int], Any] = {}
        by_uuid: dict[str, dict[str, Any]] = {}

        with ui.dialog(value=True) as dialog, ui.card().classes("w-[44rem] max-w-full"):
            ui.label("Import measurements from REW").classes("text-lg font-medium")
            ui.label(
                "Requires REW V5.40+ with the HTTP API started (Preferences → API). "
                "Measurements must have been captured with a timing reference; the "
                "import preserves their relative timing."
            ).classes("text-xs text-gray-500 mb-2")

            assign_area = ui.column().classes("w-full")

            async def apply_import() -> None:
                assignments: list[dict[str, Any]] = []
                for (mic, speaker), select in selects.items():
                    uuid = select.value
                    if not uuid:
                        continue
                    meas = by_uuid.get(uuid, {})
                    assignments.append(
                        {
                            "mic": mic,
                            "speaker": speaker,
                            "meas_id": uuid,
                            "title": meas.get("title", uuid),
                            "sample_rate": meas.get("sample_rate"),
                        }
                    )
                if not assignments:
                    ui.notify("Nothing selected to import", type="warning")
                    return
                host = (host_input.value or DEFAULT_HOST).strip()
                try:
                    port = int(port_input.value or DEFAULT_PORT)
                except (TypeError, ValueError):
                    ui.notify("Port must be a number", type="negative")
                    return
                out_dir = STATE.base_dir / "rew_import"
                ui.notify(
                    f"Downloading {len(assignments)} measurement(s) from REW…", type="info"
                )
                try:
                    written = await run.io_bound(
                        download_measurements_to_wavs, host, port, assignments, out_dir
                    )
                except RewError as exc:
                    ui.notify(str(exc), type="negative")
                    return
                for entry in written:
                    path = Path(entry["path"])
                    try:
                        relative = str(path.relative_to(STATE.base_dir))
                    except ValueError:
                        relative = str(path)
                    STATE.set_measurement(entry["mic"], entry["speaker"], relative)
                dialog.close()
                self._grid_section.refresh()
                ui.notify(
                    f"Imported {len(written)} measurement(s) into rew_import/", type="positive"
                )

            def build_assignment(measurements: list[dict[str, Any]]) -> None:
                assign_area.clear()
                selects.clear()
                by_uuid.clear()
                with_ir = [m for m in measurements if m["has_ir"]]
                if not with_ir:
                    with assign_area:
                        ui.label(
                            "No measurements with an impulse response found in REW."
                        ).classes("text-orange-500 text-sm mt-2")
                    return
                by_uuid.update({m["uuid"]: m for m in with_ir})
                options = {m["uuid"]: f"{m['index']}: {m['title']}" for m in with_ir}

                cells = [
                    (mic, speaker, STATE.mic_name(mic), profiles[str(speaker)]["name"])
                    for speaker in range(speakers)
                    for mic in range(mics)
                ]
                # Match against the title only (not the index prefix); the index
                # is just a stable display ordinal from REW.
                candidates = [(m["uuid"], m["title"]) for m in with_ir]
                preselected = _auto_assign(candidates, cells)
                total_cells = speakers * mics

                def update_options() -> None:
                    taken = {s.value for s in selects.values() if s.value}
                    for select in selects.values():
                        current = select.value
                        new_options = {
                            uuid: label
                            for uuid, label in options.items()
                            if uuid == current or uuid not in taken
                        }
                        if dict(select.options) != new_options:
                            select.options = new_options
                            select.update()

                def on_select_change() -> None:
                    update_options()

                with assign_area:
                    ui.label(
                        f"{len(with_ir)} measurement(s) available. "
                        "Pick the measurement for each speaker/mic pair; "
                        "fields left empty are skipped."
                    ).classes("text-xs text-gray-500 mt-2")
                    ui.label(
                        f"Auto-assigned {len(preselected)} of {total_cells} cells "
                        "based on matching speaker/mic names in measurement titles."
                    ).classes("text-xs text-cyan-600 mb-1")
                    with ui.scroll_area().classes("h-80 w-full"):
                        for speaker in range(speakers):
                            ui.label(
                                f"Speaker {speaker}: {profiles[str(speaker)]['name']}"
                            ).classes("font-medium mt-3 mb-1")
                            for mic in range(mics):
                                selects[(mic, speaker)] = ui.select(
                                    dict(options),
                                    value=preselected.get((mic, speaker)),
                                    label=STATE.mic_name(mic),
                                    with_input=True,
                                    clearable=True,
                                    on_change=lambda _e: on_select_change(),
                                ).classes("w-full")
                    update_options()
                    with ui.row().classes("w-full justify-end gap-2 mt-3"):
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                        ui.button(
                            "Import selected", icon="cloud_download", on_click=apply_import
                        )

            async def connect() -> None:
                host = (host_input.value or DEFAULT_HOST).strip()
                try:
                    port = int(port_input.value or DEFAULT_PORT)
                except (TypeError, ValueError):
                    ui.notify("Port must be a number", type="negative")
                    return
                STATE.config["rew_host"] = host
                STATE.config["rew_port"] = port
                ui.notify("Connecting to REW…", type="info")
                try:
                    measurements = await run.io_bound(fetch_measurements, host, port)
                except RewError as exc:
                    ui.notify(str(exc), type="negative")
                    return
                build_assignment(measurements)

            with ui.row().classes("items-end gap-2"):
                host_input = ui.input("Host", value=host_default).props(
                    "dense outlined"
                ).classes("w-48")
                port_input = ui.input("Port", value=str(port_default)).props(
                    "dense outlined"
                ).classes("w-28")
                ui.button("Connect", icon="link", on_click=connect)

    # ------------------------------------------------------------------
    # Validation

    @ui.refreshable_method
    def _validation_section(self) -> None:
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-3 mb-3"):
                ui.icon("verified").classes("text-xl").style("color: #00E5FF; filter: drop-shadow(0 0 4px rgba(0, 229, 255, 0.4));")
                ui.label("Validation").classes("text-lg font-medium")
            ui.button("Validate measurements", icon="check_circle", on_click=self._run_validation)
            self._validation_results()

    @ui.refreshable_method
    def _validation_results(
        self, rows: Optional[list[dict[str, Any]]] = None, summary: str = ""
    ) -> None:
        if rows is None:
            return
        if summary:
            ui.label(summary).classes("font-medium mt-3")
        ui.table(
            columns=[
                {"name": "cell", "label": "Mic / Speaker", "field": "cell", "align": "left"},
                {"name": "path", "label": "File", "field": "path", "align": "left"},
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
            ],
            rows=rows,
            row_key="cell",
        ).classes("w-full text-xs mt-2")

    async def _run_validation(self) -> None:
        config = dict(STATE.config)
        base_dir = STATE.base_dir
        ui.notify("Validating…", type="info")
        rows, summary = await run.io_bound(_validate_files, config, base_dir)
        self._validation_results.refresh(rows, summary)


def _normalize_for_match(text: str) -> str:
    """Lowercase and replace common separators with spaces so "Sub L",
    "sub_l", and "sub-l" all compare equal as substrings."""
    out = text.lower()
    for sep in ("_", "-", ".", "/", "\\"):
        out = out.replace(sep, " ")
    return " ".join(out.split())  # collapse runs of whitespace


def _contains_as_words(label_norm: str, query_norm: str) -> bool:
    """Whole-word substring check: query matches only when it appears at
    token boundaries in the label, not as a prefix of a longer token.
    Without this, "Sub M" would match "3sub_MLP" (which normalizes to
    "3sub mlp"; the bare substring "sub m" is its prefix). Padding both
    sides with spaces forces the match to start and end at a separator."""
    return f" {query_norm} " in f" {label_norm} "


def _auto_assign(
    candidates: list[tuple[str, str]],
    cells: list[tuple[int, int, str, str]],
) -> dict[tuple[int, int], str]:
    """Greedy 1:1 name-based matching of candidates to (mic, speaker) cells.

    A candidate matches a cell when its label contains both the cell's
    mic name and speaker name as whole-word substrings, comparing
    case-insensitively and treating `_`, `-`, `.`, `/`, `\\` as
    separator-equivalent to whitespace. "Whole-word" matters: "Sub M"
    must not match "3sub_MLP" just because the latter starts with
    "sub m" once underscores are normalized to spaces. Cells with fewer
    matching candidates are resolved first so that a distinctively named
    file (e.g. "Front L_MLP.wav") wins its only home before a more
    ambiguously named one (e.g. "L_MLP.wav") can steal it.

    `candidates`: [(value, label_for_matching)] — value is what gets stored
    (filename, REW uuid); label is what we search.
    `cells`: [(mic, speaker, mic_name, speaker_name)] from the GUI grid.

    Returns {(mic, speaker): value} only for cells that got a match.
    """
    norm_candidates = [(value, _normalize_for_match(label)) for value, label in candidates]
    scored: list[tuple[int, tuple[int, int], list[str]]] = []
    for mic, speaker, mic_name, speaker_name in cells:
        m_n = _normalize_for_match(mic_name)
        s_n = _normalize_for_match(speaker_name)
        if not m_n or not s_n:
            continue
        hits = [
            value
            for value, norm_label in norm_candidates
            if _contains_as_words(norm_label, m_n)
            and _contains_as_words(norm_label, s_n)
        ]
        if hits:
            scored.append((len(hits), (mic, speaker), hits))

    scored.sort(key=lambda t: t[0])

    result: dict[tuple[int, int], str] = {}
    used: set[str] = set()
    for _count, cell, hits in scored:
        for value in hits:
            if value not in used:
                result[cell] = value
                used.add(value)
                break
    return result


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
