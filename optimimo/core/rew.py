"""Import measurements from Room EQ Wizard (REW) over its HTTP API.

REW V5.40+ exposes a local, unauthenticated HTTP API (default
``http://127.0.0.1:4735``, loopback only) that can return a measurement's
impulse response together with its start time on the shared timing-reference
axis.  This module is a thin client over that API plus a writer that bakes the
relative inter-measurement timing into per-crosspoint WAV files, so the rest of
the pipeline can consume them exactly like any other measurement files.

Timing is the whole point: the MIMO solve sums speakers at each mic position, so
the relative time-of-flight between measurements must be physically correct.
REW returns each IR with roughly one second of pre-peak lead-in, so its data
``startTime`` sits about a second *before* the real arrival. We therefore anchor
placement on each IR's absolute direct-peak time (REW's robust
``timeOfIRPeakSeconds`` where available, else the impulse peak) and position the
peak at a small fixed pre-roll plus that IR's arrival relative to the earliest
measurement — trimming the long lead-in while preserving every inter-measurement
time-of-flight difference. The earliest arrival anchors the batch, so a whole
grid imported together is internally consistent.

Note that the API can only return correct relative timing if the measurements
were captured with an acoustic (or loopback) timing reference in REW; it cannot
recover timing that was never measured.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
from . import wav

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4735
# Where the earliest-arriving IR's direct peak is placed in the written WAVs.
# Later arrivals sit further in by their relative time-of-flight. A small value
# keeps the IRs causal-looking (a short lead-in before the peak) for the solver.
DEFAULT_PRE_ROLL_MS = 20.0


class RewError(Exception):
    """Raised for any REW API or import failure, with a user-facing message."""


def rew_base_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    return f"http://{host}:{int(port)}"


# ----------------------------------------------------------------------
# HTTP


def _http_get_json(url: str, *, timeout: float = 10.0) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise RewError(f"REW returned HTTP {exc.code} for {url}: {exc.reason}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RewError(
            f"Could not reach REW at {url}. Is REW running with the API enabled "
            "(Preferences → API → Start)?"
        ) from exc
    try:
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RewError(f"REW returned a response that was not valid JSON from {url}.") from exc


# ----------------------------------------------------------------------
# Listing / detail


def _summary_has_ir(summary: Mapping[str, Any]) -> bool:
    """Best-effort check for whether a measurement carries an impulse response."""
    return any(
        key in summary
        for key in ("timeOfIRStartSeconds", "timeOfIRPeakSeconds", "cumulativeIRShiftSeconds")
    )


def fetch_measurements(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *, timeout: float = 10.0
) -> list[dict[str, Any]]:
    """List measurements currently loaded in REW.

    ``GET /measurements`` returns a JSON object keyed by 1-based index strings;
    this flattens it to a list of summaries sorted by index.  Each entry carries
    ``index``, ``uuid`` (the stable id used to address the measurement), ``title``,
    ``sample_rate`` and a best-effort ``has_ir`` flag.
    """
    data = _http_get_json(f"{rew_base_url(host, port)}/measurements", timeout=timeout)
    if not isinstance(data, dict):
        raise RewError("Unexpected /measurements response from REW (expected a JSON object).")

    measurements: list[dict[str, Any]] = []
    for index, summary in data.items():
        if not isinstance(summary, dict):
            continue
        uuid = summary.get("uuid")
        if not uuid:
            continue
        measurements.append(
            {
                "index": str(index),
                "uuid": str(uuid),
                "title": str(summary.get("title", f"Measurement {index}")),
                "sample_rate": _maybe_int(summary.get("sampleRate")),
                "has_ir": _summary_has_ir(summary),
                # REW's own, robust IR timing on the timing-reference axis.
                # Far more reliable than argmax for subwoofers (no sharp peak).
                "peak_time": _maybe_float(summary.get("timeOfIRPeakSeconds")),
                "start_time": _maybe_float(summary.get("timeOfIRStartSeconds")),
            }
        )
    measurements.sort(key=lambda m: _maybe_int(m["index"]) or 0)
    return measurements


# ----------------------------------------------------------------------
# Impulse response


def _decode_ir_data(encoded: str) -> np.ndarray:
    """Decode REW's Base64 big-endian float32 IR blob to a float64 array."""
    try:
        raw_bytes = base64.b64decode(encoded)
    except (ValueError, TypeError) as exc:
        raise RewError("Could not decode the REW impulse-response data.") from exc
    samples = np.frombuffer(raw_bytes, dtype=">f4").astype(np.float64)
    if samples.size == 0:
        raise RewError("REW returned an empty impulse response.")
    return samples


def fetch_impulse_response(
    host: str,
    port: int,
    meas_id: str,
    *,
    normalised: bool = False,
    timeout: float = 30.0,
) -> tuple[np.ndarray, Optional[int], float]:
    """Fetch one measurement's impulse response.

    Returns ``(samples, sample_rate, start_time_seconds)``.  ``normalised`` is
    left False so relative levels between measurements are preserved (the solver's
    auto target level and acoustic-authority logic depend on them).  JSON keys are
    read defensively because exact camelCase can drift between REW beta builds.
    """
    base = rew_base_url(host, port)
    flag = "true" if normalised else "false"
    url = f"{base}/measurements/{urllib.parse.quote(str(meas_id), safe='')}/impulse-response?normalised={flag}"
    data = _http_get_json(url, timeout=timeout)
    if not isinstance(data, dict):
        raise RewError(f"Unexpected impulse-response response for measurement {meas_id}.")

    encoded = _first_present(data, ("data", "Data"))
    if encoded is None:
        raise RewError(f"REW measurement {meas_id} has no impulse-response data.")
    samples = _decode_ir_data(encoded)

    sample_rate = _maybe_int(_first_present(data, ("sampleRate", "sample_rate", "fs")))
    start_time = _maybe_float(
        _first_present(data, ("startTime", "start_time", "timeOfIRStartSeconds"))
    )
    return samples, sample_rate, start_time if start_time is not None else 0.0


# ----------------------------------------------------------------------
# Timing-aware WAV export


def _placement_plan(
    peak_times: Sequence[float],
    peak_indices: Sequence[int],
    sample_rate: int,
    pre_roll_samples: int,
) -> list[int]:
    """Per-IR leading pad (>=0) or trim (<0) to align IRs by direct-peak time.

    Each IR's direct peak is positioned at ``pre_roll_samples`` plus that IR's
    arrival relative to the earliest in the batch. ``peak_times`` are absolute
    peak times on the timing-reference axis (seconds); ``peak_indices`` are where
    that peak sits inside each returned IR. A positive result prepends zeros; a
    negative result trims that many leading samples — stripping REW's ~1 s
    pre-peak lead-in while preserving every inter-measurement time-of-flight.
    """
    if not peak_times:
        return []
    min_peak = min(float(t) for t in peak_times)
    plan: list[int] = []
    for peak_time, peak_index in zip(peak_times, peak_indices):
        target = pre_roll_samples + int(round((float(peak_time) - min_peak) * sample_rate))
        plan.append(target - int(peak_index))
    return plan


def download_measurements_to_wavs(
    host: str,
    port: int,
    assignments: Sequence[Mapping[str, Any]],
    out_dir: Path | str,
    *,
    pre_roll_ms: float = DEFAULT_PRE_ROLL_MS,
    normalised: bool = False,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Download assigned REW measurements and write timing-aligned WAV files.

    ``assignments`` is a sequence of ``{"mic", "speaker", "meas_id", "title",
    "sample_rate"?, "peak_time"?}`` mappings. Writes one 32-bit float mono WAV
    per assignment to ``out_dir/spk{speaker:02d}_mic{mic:02d}.wav``, with each
    IR's direct peak placed at a small pre-roll plus its arrival relative to the
    earliest measurement (REW's long pre-peak lead-in trimmed). Returns the
    written ``{"mic", "speaker", "path", "arrival_ms"?}`` entries; ``arrival_ms``
    (the peak position in the WAV) is included when REW reported a peak time.
    Raises ``RewError`` if the measurements do not all share one sample rate.
    """
    if not assignments:
        return []
    out_dir = Path(out_dir)

    fetched: list[tuple[Mapping[str, Any], np.ndarray, float, Optional[float]]] = []
    sample_rate: Optional[int] = None
    for item in assignments:
        samples, fs, start_time = fetch_impulse_response(
            host, port, item["meas_id"], normalised=normalised, timeout=timeout
        )
        if fs is None:
            fs = _maybe_int(item.get("sample_rate"))
        if fs is None:
            raise RewError(
                f"REW did not report a sample rate for '{item.get('title', item['meas_id'])}'."
            )
        if sample_rate is None:
            sample_rate = fs
        elif fs != sample_rate:
            raise RewError(
                f"Sample rate mismatch: '{item.get('title')}' is {fs} Hz but others are "
                f"{sample_rate} Hz. All measurements in the grid must share one sample rate."
            )
        fetched.append((item, samples, start_time, _maybe_float(item.get("peak_time"))))

    fs = int(sample_rate)
    pre_roll = max(0, int(round(pre_roll_ms / 1000.0 * fs)))

    # Locate each IR's direct peak (REW's robust value where given, else argmax)
    # and its absolute time on the timing-reference axis.
    peak_indices: list[int] = []
    abs_peaks: list[float] = []
    for _item, samples, start_time, peak_time in fetched:
        if peak_time is not None:
            index = int(round((float(peak_time) - float(start_time)) * fs))
            index = min(max(index, 0), max(samples.size - 1, 0))
            abs_peaks.append(float(peak_time))
        else:
            index = int(np.argmax(np.abs(samples)))
            abs_peaks.append(float(start_time) + index / float(fs))
        peak_indices.append(index)

    plan = _placement_plan(abs_peaks, peak_indices, fs, pre_roll)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    for (item, samples, _start, peak_time), peak_index, lead in zip(
        fetched, peak_indices, plan
    ):
        if lead >= 0:
            placed = np.concatenate(
                [np.zeros(lead, dtype=np.float32), samples.astype(np.float32)]
            )
        else:
            placed = np.ascontiguousarray(samples[-lead:], dtype=np.float32)
        path = out_dir / f"spk{int(item['speaker']):02d}_mic{int(item['mic']):02d}.wav"
        wav.write(str(path), fs, placed)
        entry: dict[str, Any] = {
            "mic": int(item["mic"]),
            "speaker": int(item["speaker"]),
            "path": str(path),
        }
        if peak_time is not None:
            # The peak now sits at (lead + peak_index) in the written WAV.
            entry["arrival_ms"] = max(0.0, (lead + peak_index) / float(fs) * 1000.0)
        written.append(entry)
    return written


# ----------------------------------------------------------------------
# Small helpers


def _first_present(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _maybe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
