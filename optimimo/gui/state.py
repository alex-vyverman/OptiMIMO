"""Session state and config helpers for the MIMO room-correction GUI.

The GUI is a single-user local app, so one module-level state object is used.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..cli import example_config, minimal_config
from ..core.pipeline import ExportPaths, SolveResult


@dataclass
class AppState:
    config: dict[str, Any] = field(default_factory=minimal_config)
    config_path: Optional[Path] = None
    result: Optional[SolveResult] = None
    export_paths: Optional[ExportPaths] = None

    running: bool = False
    progress_stage: str = ""
    progress_fraction: float = 0.0
    cancel_event: Optional[threading.Event] = None
    last_error: str = ""
    last_dims: Optional[tuple] = None

    # Track unsaved changes
    _config_hash: Optional[str] = field(default=None, repr=False)
    has_unsaved_changes: bool = False

    @property
    def base_dir(self) -> Path:
        """Directory measurements and output paths are resolved against."""
        if self.config_path is not None:
            return self.config_path.resolve().parent
        return Path.cwd()

    # ------------------------------------------------------------------
    # Unsaved-change tracking

    def _compute_config_hash(self) -> str:
        """Compute a hash of the current config for change detection."""
        import hashlib
        config_str = json.dumps(self.config, sort_keys=True, default=str)
        return hashlib.md5(config_str.encode()).hexdigest()

    def mark_config_changed(self) -> None:
        """Mark that the config has been modified."""
        self.has_unsaved_changes = True

    def mark_config_saved(self) -> None:
        """Mark that the config has been saved."""
        self.has_unsaved_changes = False
        self._config_hash = self._compute_config_hash()

    def check_for_external_changes(self) -> bool:
        """Check if config has changed since last save. Returns True if changed."""
        if self._config_hash is None:
            return False
        current_hash = self._compute_config_hash()
        return current_hash != self._config_hash

    # ------------------------------------------------------------------
    # Config file handling

    def load_config(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            raise ValueError("Configuration root must be a JSON object.")
        self.config = config
        self.config_path = path
        self.result = None
        self.export_paths = None
        self.normalize_config()

    def save_config(self, path: Path) -> None:
        self.normalize_config()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(self.config, handle, indent=2)
            handle.write("\n")
        self.config_path = path

    def new_from_example(self) -> None:
        self.config = example_config()
        self.config_path = None
        self.result = None
        self.export_paths = None
        self.normalize_config()

    def new_from_template(self) -> None:
        """Start a new project from the minimal 2.1 template."""
        self.config = minimal_config()
        self.config_path = None
        self.result = None
        self.export_paths = None
        self.normalize_config()

    # ------------------------------------------------------------------
    # Config structure helpers (keep nested structures sized correctly)

    def num_speakers(self) -> int:
        return max(1, _as_int(self.config.get("num_speakers", 1), 1))

    def num_mics(self) -> int:
        return max(1, _as_int(self.config.get("num_mic_positions", 1), 1))

    def num_inputs(self) -> int:
        return max(1, _as_int(self.config.get("num_inputs", self.num_speakers()), self.num_speakers()))

    def normalize_config(self) -> None:
        """Resize nested config structures after dimension changes.

        Keeps speaker profiles, routing, mic weights, and the measurement list
        consistent with num_speakers / num_mic_positions / num_inputs so the
        widgets always have valid structures to bind to.
        """
        config = self.config
        speakers = self.num_speakers()
        mics = self.num_mics()
        inputs = self.num_inputs()
        config["num_speakers"] = speakers
        config["num_mic_positions"] = mics
        config["num_inputs"] = inputs
        self.last_dims = (speakers, inputs, mics)

        # Speaker profiles: dict keyed by stringified index.
        raw_profiles = config.get("speaker_profiles") or {}
        profiles: dict[str, dict[str, Any]] = {}
        for index in range(speakers):
            entry = None
            if isinstance(raw_profiles, dict):
                entry = raw_profiles.get(str(index), raw_profiles.get(index))
            elif isinstance(raw_profiles, list) and index < len(raw_profiles):
                entry = raw_profiles[index]
            if not isinstance(entry, dict):
                entry = {}
            profiles[str(index)] = {
                "name": str(entry.get("name", f"Speaker {index}")),
                "min_hz": float(entry.get("min_hz", entry.get("low_hz", 20.0))),
                "max_hz": float(entry.get("max_hz", entry.get("high_hz", 20000.0))),
                "transition_hz": float(entry.get("transition_hz", 10.0)),
                "effort_penalty_db": float(entry.get("effort_penalty_db", 0.0)),
            }
        config["speaker_profiles"] = profiles

        # Mic weights.
        weights = list(config.get("mic_weights") or [])
        weights = [float(w) for w in weights[:mics]]
        weights += [1.0] * (mics - len(weights))
        config["mic_weights"] = weights

        # Mic position names (optional; usable in measurement_pattern as
        # {mic_name}). Empty entries fall back to a default label.
        names = list(config.get("mic_names") or [])
        names = [str(n) for n in names[:mics]]
        names += [""] * (mics - len(names))
        config["mic_names"] = names

        # Routing mask.
        raw_routing = config.get("input_speakers")
        if raw_routing is not None:
            routing: dict[str, list[int]] = {}
            for input_channel in range(inputs):
                entry = None
                if isinstance(raw_routing, dict):
                    entry = raw_routing.get(str(input_channel), raw_routing.get(input_channel))
                elif isinstance(raw_routing, list) and input_channel < len(raw_routing):
                    entry = raw_routing[input_channel]
                if entry is None:
                    entry = list(range(speakers))
                routing[str(input_channel)] = sorted(
                    {int(s) for s in entry if 0 <= int(s) < speakers}
                )
            config["input_speakers"] = routing

        # Primary speakers (anchored mode).
        raw_primary = config.get("input_primary_speaker")
        if raw_primary is not None:
            primary: dict[str, int] = {}
            for input_channel in range(inputs):
                entry = None
                if isinstance(raw_primary, dict):
                    entry = raw_primary.get(str(input_channel), raw_primary.get(input_channel))
                elif isinstance(raw_primary, list) and input_channel < len(raw_primary):
                    entry = raw_primary[input_channel]
                value = _as_int(entry, min(input_channel, speakers - 1))
                primary[str(input_channel)] = min(max(value, 0), speakers - 1)
            config["input_primary_speaker"] = primary

        # Target curve points.
        points = config.get("target_curve_points_db") or [[20.0, 0.0], [20000.0, 0.0]]
        config["target_curve_points_db"] = [
            [float(p[0]), float(p[1])] for p in points if len(p) >= 2
        ]

        # Target curve file/IR: strip empty strings.
        for key in ("target_curve_file", "target_curve_ir_file"):
            val = config.get(key)
            if val is not None and not str(val).strip():
                config.pop(key)
        ir_smooth = config.get("target_curve_ir_smoothing_fraction")
        if ir_smooth is not None:
            config["target_curve_ir_smoothing_fraction"] = float(ir_smooth)

        # Measurements: keep only in-range entries.
        if "measurements" in config:
            entries = config.get("measurements") or []
            kept = []
            for entry in entries:
                try:
                    speaker = int(entry["speaker"])
                    mic = int(entry["mic"])
                except (KeyError, TypeError, ValueError):
                    continue
                if 0 <= speaker < speakers and 0 <= mic < mics:
                    new_entry = {"speaker": speaker, "mic": mic, "path": str(entry.get("path", ""))}
                    arrival_ms = entry.get("arrival_ms")
                    if arrival_ms is not None:
                        try:
                            new_entry["arrival_ms"] = float(arrival_ms)
                        except (TypeError, ValueError):
                            pass
                    kept.append(new_entry)
            config["measurements"] = kept

    # ------------------------------------------------------------------
    # Measurement grid helpers

    def mic_name(self, mic: int) -> str:
        """Display name for a mic position, falling back to a default label."""
        names = self.config.get("mic_names") or []
        if 0 <= mic < len(names) and str(names[mic]).strip():
            return str(names[mic])
        return f"Mic {mic}"

    def measurement_grid(self) -> dict[tuple[int, int], str]:
        """Return {(mic, speaker): path} for explicit measurement entries."""
        grid: dict[tuple[int, int], str] = {}
        for entry in self.config.get("measurements", []) or []:
            grid[(int(entry["mic"]), int(entry["speaker"]))] = str(entry.get("path", ""))
        return grid

    def set_measurement(
        self, mic: int, speaker: int, path: str, arrival_ms: float | None = None
    ) -> None:
        entries = [
            entry
            for entry in self.config.get("measurements", []) or []
            if not (int(entry["mic"]) == mic and int(entry["speaker"]) == speaker)
        ]
        if path:
            entry: dict[str, Any] = {"speaker": speaker, "mic": mic, "path": path}
            if arrival_ms is not None:
                entry["arrival_ms"] = float(arrival_ms)
            entries.append(entry)
        entries.sort(key=lambda e: (int(e["mic"]), int(e["speaker"])))
        self.config["measurements"] = entries


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


STATE = AppState()
STATE.normalize_config()
