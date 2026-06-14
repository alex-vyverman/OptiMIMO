"""Config tab: all solver parameters, grouped like the README reference.

Built as a per-client class: every browser connection gets its own instance,
so `ui.refreshable_method` targets never outlive their client (see NiceGUI
issue #3028).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from nicegui import ui

from .file_picker import pick_file
from .state import STATE


def _info(text: str) -> None:
    with ui.icon("info", size="xs").classes("text-gray-400 cursor-help"):
        ui.tooltip(text).props('max-width="320px"').classes("text-xs")


def _num(label: str, key: str, default: float, *, fmt: str = "%.6g", tooltip: str | None = None, **kwargs) -> ui.number:
    """Number input for a config key (no persistent binding to avoid client deletion issues)."""
    if key not in STATE.config:
        STATE.config[key] = default
    
    def on_change(e):
        STATE.config[key] = e.value
    
    field = ui.number(label, value=STATE.config[key], format=fmt, on_change=on_change, **kwargs)
    field.classes("w-40")
    if tooltip:
        _info(tooltip)
    return field


def _toggle(label: str, key: str, default: bool, *, tooltip: str | None = None) -> ui.switch:
    if key not in STATE.config:
        STATE.config[key] = default
    
    def on_change(e):
        STATE.config[key] = e.value
    
    field = ui.switch(label, value=STATE.config[key], on_change=on_change)
    if tooltip:
        _info(tooltip)
    return field


def apply_pending_fields() -> None:
    """Fold GUI-only helper fields back into the real config keys."""
    config = STATE.config
    if "_ref_low" in config or "_ref_high" in config:
        low = float(config.pop("_ref_low", config.get("reference_band_hz", [20.0, 200.0])[0]))
        high = float(config.pop("_ref_high", config.get("reference_band_hz", [20.0, 200.0])[1]))
        config["reference_band_hz"] = [low, high]
    for key in ("num_speakers", "num_inputs", "num_mic_positions", "sample_rate",
                "filter_taps", "fade_out_samples"):
        if key in config and config[key] is not None:
            config[key] = int(config[key])
    # Optional integer fields where 0 means "auto": drop them.
    for key in ("fft_size", "ir_length_samples"):
        if key in config:
            value = int(config[key] or 0)
            if value <= 0:
                config.pop(key)
            else:
                config[key] = value


def _sync_reference_band() -> None:
    """Mirror the two helper fields into reference_band_hz."""
    band = STATE.config.get("reference_band_hz", [20.0, 200.0])
    STATE.config.setdefault("_ref_low", float(band[0]))
    STATE.config.setdefault("_ref_high", float(band[1]))


class ConfigTab:
    def __init__(self, on_config_replaced: Optional[Callable[[], None]] = None) -> None:
        self.on_config_replaced = on_config_replaced

    def build(self) -> None:
        with ui.column().classes("w-full max-w-5xl gap-2"):
            self._file_bar()
            self._dimensions_section()
            self._profiles_section()
            self._routing_section()
            self._target_section()
            self._filter_section()
            self._smoothing_section()
            self._output_section()

    def refresh_all(self) -> None:
        self._file_bar.refresh()
        self._dimensions_section.refresh()
        self._profiles_section.refresh()
        self._routing_section.refresh()
        self._target_section.refresh()
        self._filter_section.refresh()
        self._smoothing_section.refresh()
        self._output_section.refresh()
        if self.on_config_replaced is not None:
            self.on_config_replaced()

    def _refresh_dependent_sections(self) -> None:
        """Refresh only sections that depend on speaker/input/mic dimensions."""
        self._profiles_section.refresh()
        self._routing_section.refresh()
        self._target_section.refresh()
        if self.on_config_replaced is not None:
            self.on_config_replaced()

    # ------------------------------------------------------------------
    # File handling

    @ui.refreshable_method
    def _file_bar(self) -> None:
        with ui.row().classes("w-full items-center gap-2"):
            ui.label("Config:").classes("font-medium")
            ui.label(str(STATE.config_path) if STATE.config_path else "(unsaved)").classes(
                "text-sm text-gray-600 break-all"
            )
            ui.space()

            async def load() -> None:
                path = await pick_file(STATE.base_dir, title="Load config", suffixes=[".json"])
                if path is None:
                    return
                try:
                    STATE.load_config(Path(path))
                except (OSError, ValueError) as exc:
                    ui.notify(f"Load failed: {exc}", type="negative")
                    return
                ui.notify(f"Loaded {path}", type="positive")
                self.refresh_all()

            async def save() -> None:
                if STATE.config_path is None:
                    await save_as()
                    return
                STATE.save_config(STATE.config_path)
                ui.notify(f"Saved {STATE.config_path}", type="positive")

            async def save_as() -> None:
                with ui.dialog() as dialog, ui.card():
                    ui.label("Save config as")
                    name = ui.input(
                        "Path", value=str(STATE.config_path or STATE.base_dir / "config.json")
                    ).classes("w-96")
                    with ui.row():
                        ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
                        ui.button("Save", on_click=lambda: dialog.submit(name.value))
                value = await dialog
                if not value:
                    return
                STATE.save_config(Path(value).expanduser())
                ui.notify(f"Saved {value}", type="positive")
                self._file_bar.refresh()

            def new() -> None:
                STATE.new_from_example()
                ui.notify("New config from example", type="info")
                self.refresh_all()

            ui.button("Load", on_click=load).props("flat")
            ui.button("Save", on_click=save).props("flat")
            ui.button("Save as", on_click=save_as).props("flat")
            ui.button("New from example", on_click=new).props("flat")

    # ------------------------------------------------------------------
    # Sections

    def _on_dimensions_change(self) -> None:
        dims = (STATE.num_speakers(), STATE.num_inputs(), STATE.num_mics())
        if dims == STATE.last_dims:
            return
        STATE.last_dims = dims
        STATE.normalize_config()
        self._refresh_dependent_sections()

    @ui.refreshable_method
    def _dimensions_section(self) -> None:
        on_change = lambda: self._on_dimensions_change()  # noqa: E731
        with ui.expansion("Dimensions", value=True).classes("w-full"):
            with ui.row().classes("items-end gap-4"):
                n = ui.number("Speakers", value=STATE.config.get("num_speakers", 5), min=1, max=32, format="%d",
                         on_change=lambda e: (STATE.config.__setitem__("num_speakers", e.value), on_change())).classes("w-28")
                _info("Number of physical output channels (N). Speaker indices used everywhere else refer to this ordering.")
                i = ui.number("Inputs", value=STATE.config.get("num_inputs", 2), min=1, max=32, format="%d",
                         on_change=lambda e: (STATE.config.__setitem__("num_inputs", e.value), on_change())).classes("w-28")
                _info("Number of input channels (K), e.g. 2 for stereo sources. Produces N×K FIR filters.")
                m = ui.number("Mic positions", value=STATE.config.get("num_mic_positions", 12), min=1, max=64, format="%d",
                         on_change=lambda e: (STATE.config.__setitem__("num_mic_positions", e.value), on_change())).classes("w-28")
                _info("Number of microphone positions (M) in the measurement grid.")
                _num("Sample rate", "sample_rate", 96000, fmt="%d",
                     tooltip="Expected sample rate in Hz. Read from WAV files; mismatches are rejected. Required for text IRs without a time column.")

    @ui.refreshable_method
    def _profiles_section(self) -> None:
        with ui.expansion("Speaker profiles", value=True).classes("w-full"):
            ui.label(
                "Safe operating band per speaker; the solver removes a speaker from the "
                "optimization outside its band."
            ).classes("text-xs text-gray-500")
            profiles = STATE.config["speaker_profiles"]
            for index in range(STATE.num_speakers()):
                entry = profiles[str(index)]
                with ui.row().classes("items-end gap-3"):
                    ui.label(str(index)).classes("w-4 text-gray-500 pb-2")
                    
                    name_field = ui.input("Name", value=entry.get("name", f"Speaker {index}"),
                            on_change=lambda e, entry=entry: entry.__setitem__("name", e.value)).classes("w-36")
                    _info("Display name for this speaker.")
                    
                    min_field = ui.number("Min Hz", value=entry.get("min_hz", 20.0), min=0, format="%.6g",
                             on_change=lambda e, entry=entry: entry.__setitem__("min_hz", e.value)).classes("w-24")
                    _info("Lower bound of the speaker's safe operating band. The solver removes this speaker from the optimization below this frequency.")
                    
                    max_field = ui.number("Max Hz", value=entry.get("max_hz", 20000.0), min=0, format="%.6g",
                             on_change=lambda e, entry=entry: entry.__setitem__("max_hz", e.value)).classes("w-24")
                    _info("Upper bound of the speaker's safe operating band. The solver removes this speaker from the optimization above this frequency.")
                    
                    trans_field = ui.number("Transition Hz", value=entry.get("transition_hz", 10.0), min=0, format="%.6g",
                             on_change=lambda e, entry=entry: entry.__setitem__("transition_hz", e.value)).classes("w-28")
                    _info("Raised-sine ramp width inside the band edges. Creates a smooth rolloff transition instead of a hard cutoff.")
                    
                    effort_field = ui.number("Effort penalty dB", value=entry.get("effort_penalty_db", 0.0), min=0, format="%.6g",
                             on_change=lambda e, entry=entry: entry.__setitem__("effort_penalty_db", e.value)).classes("w-32")
                    _info("Extra regularization to make the solver prefer other speakers over this one. Higher values reduce this speaker's contribution.")

            ui.separator()
            ui.label("Mic position weights").classes("font-medium")
            _info("Relative importance of each mic position in the least-squares error. Set the listening position highest for best results there.")
            weights = STATE.config["mic_weights"]
            with ui.row().classes("gap-2 flex-wrap"):
                for mic in range(STATE.num_mics()):

                    def on_weight(event, mic=mic) -> None:
                        try:
                            weights[mic] = float(event.value)
                        except (TypeError, ValueError):
                            pass

                    ui.number(
                        f"Mic {mic}", value=weights[mic], min=0, format="%.6g", on_change=on_weight
                    ).classes("w-24")

    @ui.refreshable_method
    def _routing_section(self) -> None:
        with ui.expansion("Input routing", value=True).classes("w-full"):
            enabled = "input_speakers" in STATE.config

            def toggle_routing(event) -> None:
                if event.value and "input_speakers" not in STATE.config:
                    STATE.config["input_speakers"] = {
                        str(i): list(range(STATE.num_speakers())) for i in range(STATE.num_inputs())
                    }
                elif not event.value:
                    STATE.config.pop("input_speakers", None)
                STATE.normalize_config()
                self._routing_section.refresh()

            routing_switch = ui.switch(
                "Restrict which speakers may reproduce each input",
                value=enabled,
                on_change=toggle_routing,
            )
            _info("Allowed speakers per input. Blocked pairs are removed from the optimization and exported as all-zero FIRs. Use this for stereo bass management.")
            if not enabled:
                ui.label("All speakers serve all inputs (full matrix).").classes(
                    "text-xs text-gray-500"
                )
                return

            routing = STATE.config["input_speakers"]
            profiles = STATE.config["speaker_profiles"]
            with ui.grid(columns=STATE.num_speakers() + 1).classes("gap-1 items-center"):
                ui.label("")
                for speaker in range(STATE.num_speakers()):
                    ui.label(profiles[str(speaker)]["name"]).classes("text-xs font-medium")
                for input_channel in range(STATE.num_inputs()):
                    ui.label(f"Input {input_channel}").classes("text-xs font-medium")
                    allowed = routing[str(input_channel)]
                    for speaker in range(STATE.num_speakers()):

                        def on_check(event, input_channel=input_channel, speaker=speaker) -> None:
                            entry = set(routing[str(input_channel)])
                            if event.value:
                                entry.add(speaker)
                            else:
                                entry.discard(speaker)
                            routing[str(input_channel)] = sorted(entry)

                        ui.checkbox(value=speaker in allowed, on_change=on_check)

    @ui.refreshable_method
    def _target_section(self) -> None:
        with ui.expansion("Target", value=True).classes("w-full"):
            if "target_mode" not in STATE.config:
                STATE.config["target_mode"] = "flat"

            def on_mode(event) -> None:
                STATE.config["target_mode"] = event.value
                if event.value == "anchored" and "input_primary_speaker" not in STATE.config:
                    STATE.config["input_primary_speaker"] = {
                        str(i): min(i, STATE.num_speakers() - 1) for i in range(STATE.num_inputs())
                    }
                    STATE.normalize_config()
                self._target_section.refresh()

            with ui.row().classes("items-end gap-4"):
                mode_select = ui.select(
                    {
                        "flat": "flat (house curve, pure delay)",
                        "anchored": "anchored (primary speaker)",
                    },
                    value=STATE.config.get("target_mode", "flat"),
                    label="Target mode",
                    on_change=on_mode,
                ).classes("w-72")
                _info("flat: identical house-curve target at all mics. anchored: target derived from each input's primary speaker, preserving natural arrival time and geometry.")
                _num("Target delay ms", "target_delay_ms", 100.0,
                     tooltip="Bulk delay built into the target so the inverse can be causal. Becomes system latency. Flat mode needs ~180ms; anchored tolerates ~80-100ms.")
                _toggle("Auto target level", "auto_target_level", True,
                        tooltip="Scale the target from the median measured in-band response power, so results don't depend on absolute REW export level.")

            if str(STATE.config.get("target_mode", "flat")).lower() == "anchored":
                profiles = STATE.config["speaker_profiles"]
                primary = STATE.config.get("input_primary_speaker", {})
                options = {
                    s: f"{s}: {profiles[str(s)]['name']}" for s in range(STATE.num_speakers())
                }
                with ui.row().classes("items-end gap-4"):
                    for input_channel in range(STATE.num_inputs()):
                        primary_select = ui.select(
                            options,
                            value=primary.get(str(input_channel), 0),
                            label=f"Primary for input {input_channel}",
                            on_change=lambda e, ic=input_channel: primary.__setitem__(str(ic), e.value),
                        ).classes("w-48")
                        _info("The speaker each input belongs to. The target keeps this speaker's natural arrival time and broad phase.")
                with ui.row().classes("items-end gap-4"):
                    _num("Anchor phase smoothing (1/N oct)", "anchor_phase_smoothing_fraction", 1.0,
                         tooltip="Fractional-octave complex smoothing of the primary's response before extracting target phase. Heavy on purpose — keeps geometry, excludes defects.")
                    _num("Anchor level floor dB", "anchor_level_floor_db", -30.0,
                         tooltip="Below this level (relative to primary's in-band average) the target magnitude shrinks toward zero. Prevents demanding output where the primary has no authority.")

            ui.separator()
            curve_source = "breakpoints"
            if "target_curve_file" in STATE.config:
                curve_source = "text_file"
            elif "target_curve_ir_file" in STATE.config:
                curve_source = "ir_file"

            def on_curve_source(event) -> None:
                source = event.value
                if source == "breakpoints":
                    STATE.config.pop("target_curve_file", None)
                    STATE.config.pop("target_curve_ir_file", None)
                    STATE.config.pop("target_curve_ir_smoothing_fraction", None)
                elif source == "text_file":
                    STATE.config.pop("target_curve_ir_file", None)
                    STATE.config.pop("target_curve_ir_smoothing_fraction", None)
                    if "target_curve_file" not in STATE.config:
                        STATE.config["target_curve_file"] = ""
                elif source == "ir_file":
                    STATE.config.pop("target_curve_file", None)
                    if "target_curve_ir_file" not in STATE.config:
                        STATE.config["target_curve_ir_file"] = ""
                    STATE.config.setdefault("target_curve_ir_smoothing_fraction", 6.0)
                self._target_section.refresh()

            with ui.row().classes("items-end gap-4"):
                ui.select(
                    {
                        "breakpoints": "Breakpoints (manual)",
                        "text_file": "Text file (freq, dB)",
                        "ir_file": "Impulse response",
                    },
                    value=curve_source,
                    label="House curve source",
                    on_change=on_curve_source,
                ).classes("w-72")
                _info("Where to read the target house curve from. Breakpoints are edited manually; text files need freq_hz and dB columns; an impulse response is FFT'd and smoothed.")

            if curve_source == "text_file":
                with ui.row().classes("items-end gap-2"):
                    file_value = STATE.config.get("target_curve_file", "")
                    file_field = ui.input(
                        "Target curve file",
                        value=file_value,
                        on_change=lambda e: STATE.config.__setitem__("target_curve_file", e.value),
                    ).classes("w-96")
                    _info("Path to a text file with freq_hz and dB columns. Comments (#) and comma separators are accepted.")

                    async def browse_curve_file() -> None:
                        path = await pick_file(STATE.base_dir, title="Select target curve file", suffixes=[".txt", ".csv", ".dat"])
                        if path is not None:
                            try:
                                rel = path.resolve().relative_to(STATE.base_dir.resolve())
                                STATE.config["target_curve_file"] = str(rel)
                            except ValueError:
                                STATE.config["target_curve_file"] = str(path)
                            self._target_section.refresh()

                    ui.button(icon="folder_open", on_click=browse_curve_file).props("flat")

            elif curve_source == "ir_file":
                with ui.row().classes("items-end gap-2"):
                    ir_value = STATE.config.get("target_curve_ir_file", "")
                    ir_field = ui.input(
                        "Target curve impulse response",
                        value=ir_value,
                        on_change=lambda e: STATE.config.__setitem__("target_curve_ir_file", e.value),
                    ).classes("w-96")
                    _info("Path to a WAV or text impulse response. Its magnitude response is used as the house curve shape, normalised to 0 dB in the reference band.")

                    async def browse_curve_ir() -> None:
                        path = await pick_file(STATE.base_dir, title="Select target curve IR", suffixes=[".wav", ".txt", ".csv", ".dat"])
                        if path is not None:
                            try:
                                rel = path.resolve().relative_to(STATE.base_dir.resolve())
                                STATE.config["target_curve_ir_file"] = str(rel)
                            except ValueError:
                                STATE.config["target_curve_ir_file"] = str(path)
                            self._target_section.refresh()

                    ui.button(icon="folder_open", on_click=browse_curve_ir).props("flat")

                with ui.row().classes("items-end gap-4"):
                    _num("IR curve smoothing (1/N oct, 0 = off)", "target_curve_ir_smoothing_fraction", 6.0,
                         tooltip="Fractional-octave magnitude smoothing applied to the IR-derived house curve. 6.0 = 1/6 octave. Use 0 for raw response.")

            else:
                curve_label = ui.label("House curve points (Hz, dB)").classes("font-medium")
                _info("Target house curve as [freq_hz, dB] breakpoints, interpolated on a log-frequency axis.")
                points = STATE.config["target_curve_points_db"]
                for index, point in enumerate(points):
                    with ui.row().classes("items-end gap-2"):

                        def on_freq(event, point=point) -> None:
                            try:
                                point[0] = float(event.value)
                            except (TypeError, ValueError):
                                pass

                        def on_db(event, point=point) -> None:
                            try:
                                point[1] = float(event.value)
                            except (TypeError, ValueError):
                                pass

                        def remove(index=index) -> None:
                            points.pop(index)
                            self._target_section.refresh()

                        ui.number("Hz", value=point[0], min=1, format="%.6g", on_change=on_freq).classes(
                            "w-28"
                        )
                        ui.number("dB", value=point[1], format="%.6g", on_change=on_db).classes("w-24")
                        ui.button(icon="delete", on_click=remove).props("flat dense")

                def add_point() -> None:
                    points.append([1000.0, 0.0])
                    self._target_section.refresh()

                ui.button("Add point", icon="add", on_click=add_point).props("flat")

            _sync_reference_band()
            with ui.row().classes("items-end gap-4"):
                _num("Reference band low Hz", "_ref_low", 20.0,
                     tooltip="Lower bound of the reference band used for auto target level, regularization reference power, and anchored-mode level estimation.")
                _num("Reference band high Hz", "_ref_high", 200.0,
                     tooltip="Upper bound of the reference band used for auto target level, regularization reference power, and anchored-mode level estimation.")

    @ui.refreshable_method
    def _filter_section(self) -> None:
        with ui.expansion("Filter dimensions and protection").classes("w-full"):
            with ui.row().classes("items-end gap-4"):
                _num("Filter taps", "filter_taps", 65536, fmt="%d",
                     tooltip="Length of the exported FIR filters. Determines how long a correction can ring. 65536 taps at 96 kHz is 683 ms.")
                _num("FFT size (0 = auto)", "fft_size", 0, fmt="%d",
                     tooltip="Solve resolution. Must be at least ir_length + filter_taps - 1. Auto picks the next power of two. Larger values give the inverse more time to decay before the circular wrap point.")
                _num("IR length samples (0 = auto)", "ir_length_samples", 0, fmt="%d",
                     tooltip="Length to which all IRs are cropped/zero-padded. Sets the low-frequency resolution of the measurement data.")
                _num("Fade-out samples", "fade_out_samples", 2048, fmt="%d",
                     tooltip="Hann fade applied to the FIR tail to avoid a truncation discontinuity.")
            with ui.row().classes("items-end gap-4"):
                _num("Max boost dB", "max_boost_db", 9.0,
                     tooltip="Hard cap on filter gain, applied per crosspoint and optionally per speaker row sum, plus once more after FIR truncation.")
                _num("Max cut dB", "max_cut_db", 18.0,
                     tooltip="Floor for the diagonal filter magnitude. Only enforced when enforce_diagonal_cut_floor is true.")
                _toggle("Row-sum gain cap", "enforce_row_sum_gain_cap", True,
                        tooltip="Cap the summed drive each physical speaker can receive across all inputs, not just each individual filter.")
                _toggle("Diagonal cut floor", "enforce_diagonal_cut_floor", False,
                        tooltip="Prevent the direct input-to-primary path from being cut below max_cut_db.")

    @ui.refreshable_method
    def _smoothing_section(self) -> None:
        with ui.expansion("Smoothing and regularization").classes("w-full"):
            with ui.row().classes("items-end gap-4"):
                _num("H smoothing (1/N oct, 0 = off)", "h_smoothing_fraction", 6.0,
                     tooltip="Fractional-octave complex smoothing of the measured room matrix H(f) before solving. Each measurement is de-rotated by its direct-sound arrival time before smoothing, so relative phase is preserved. Equivalent to REW's frequency-dependent window.")
                _num("X smoothing (1/N oct, 0 = off)", "x_smoothing_fraction", 6.0,
                     tooltip="Same smoothing applied to the solved filters X(f). Bounds the Q of every filter feature so FIRs decay well within filter_taps. Enable if diagnostics warn about wrap-point energy.")
            with ui.row().classes("items-end gap-4"):
                _num("Authority floor dB", "authority_floor_db", -30.0,
                     tooltip="Speakers whose measured in-band response falls below this relative level get progressively stronger regularization instead of being boosted into inaudibility.")
                _num("Profile disable threshold", "profile_disable_threshold", 1.0e-4, fmt="%.2e",
                     tooltip="Profile weight below which a speaker counts as fully disabled at that frequency.")

    @ui.refreshable_method
    def _output_section(self) -> None:
        with ui.expansion("Output").classes("w-full"):
            with ui.row().classes("items-end gap-4"):
                if "output_dir" not in STATE.config:
                    STATE.config["output_dir"] = "output_firs"
                dir_field = ui.input("Output directory", value=STATE.config.get("output_dir", "output_firs"),
                        on_change=lambda e: STATE.config.__setitem__("output_dir", e.value)).classes("w-64")
                _info("Destination for FIRs, YAML snippet and diagnostics.json.")
                
                if "output_format" not in STATE.config:
                    STATE.config["output_format"] = "both"
                _valid_formats = ("wav", "txt", "both")
                _fmt_value = STATE.config.get("output_format", "both")
                _fmt_options = list(_valid_formats) if _fmt_value in _valid_formats else [_fmt_value, *_valid_formats]
                fmt_select = ui.select(
                    _fmt_options,
                    value=_fmt_value,
                    label="Output format",
                    on_change=lambda e: STATE.config.__setitem__("output_format", e.value),
                ).classes("w-32")
                _info("wav, txt, or both. WAV files are smaller and faster to load.")
                
                if "camilladsp_conv_type" not in STATE.config:
                    STATE.config["camilladsp_conv_type"] = "raw"
                _valid_conv = ("wav", "raw")
                _conv_value = STATE.config.get("camilladsp_conv_type", "raw")
                _conv_options = {"wav": "wav (Conv/Wav)", "raw": "raw (Conv/Raw TEXT, GUI-import safe)"}
                if _conv_value not in _valid_conv:
                    _conv_options = {_conv_value: f"{_conv_value} (invalid)", **_conv_options}
                conv_select = ui.select(
                    _conv_options,
                    value=_conv_value,
                    label="CamillaDSP conv type",
                    on_change=lambda e: STATE.config.__setitem__("camilladsp_conv_type", e.value),
                ).classes("w-72")
                _info("wav = Conv/Wav filters. raw = Conv/Raw with format: TEXT (workaround for camillagui import bug, needs txt output).")
            
            with ui.row().classes("items-end gap-4"):
                if "camilladsp_filter_path_prefix" not in STATE.config:
                    STATE.config["camilladsp_filter_path_prefix"] = ""
                prefix_field = ui.input(
                    "CamillaDSP filter path prefix",
                    value=STATE.config.get("camilladsp_filter_path_prefix", ""),
                    on_change=lambda e: STATE.config.__setitem__("camilladsp_filter_path_prefix", e.value),
                ).classes("w-64")
                _info("Prepended to coefficient filenames in the YAML. Set to the coefficient directory on the DSP host.")
                
                _toggle("Absolute paths in YAML", "camilladsp_absolute_paths", False,
                        tooltip="Reference coefficients by absolute local path instead of relative paths.")
