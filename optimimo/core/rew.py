"""Import measurements from Room EQ Wizard (REW) over its HTTP API.

REW V5.40+ exposes a local, unauthenticated HTTP API (default
``http://127.0.0.1:4735``, loopback only) that can return a measurement's
impulse response together with its start time on the shared timing-reference
axis.  This module is a thin client over that API plus a writer that bakes the
relative inter-measurement timing into per-crosspoint WAV files, so the rest of
the pipeline can consume them exactly like any other measurement files.

Timing is the whole point: the MIMO solve sums speakers at each mic position, so
the relative time-of-flight between measurements must be physically correct.
The API hands us each IR's ``startTime`` (seconds); we left-pad every IR by
``round((startTime + pad) * fs)`` zero samples so that sample 0 of every written
WAV corresponds to the *same* absolute reference time.  This preserves all
time-of-flight differences and, because the placement is anchored to REW's
absolute axis rather than a per-batch minimum, stays consistent across separate
import batches.

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
from scipy.io import wavfile

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4735
# Fixed pre-roll added ahead of the earliest possible IR start so absolute
# placement never needs a negative sample offset.  ~20 ms is a couple thousand
# samples at typical rates — negligible, and comfortably covers the small
# negative start times REW reports for left-windowed IRs.
DEFAULT_PAD_MS = 20.0


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


def _placement_offsets(
    start_times: Sequence[float], sample_rate: int, *, pad_ms: float = DEFAULT_PAD_MS
) -> list[int]:
    """Leading zero-sample offset that places each IR on a common time axis.

    Anchored to REW's absolute axis (offset = ``round((start + pad) * fs)``) so the
    result is independent of which measurements happen to be in this batch.  A
    measurement whose start is earlier than ``-pad`` is clamped to 0 rather than
    shifting the whole set, to keep cross-batch placement stable.
    """
    pad_seconds = pad_ms / 1000.0
    return [
        max(0, int(round((float(start) + pad_seconds) * sample_rate))) for start in start_times
    ]


def download_measurements_to_wavs(
    host: str,
    port: int,
    assignments: Sequence[Mapping[str, Any]],
    out_dir: Path | str,
    *,
    pad_ms: float = DEFAULT_PAD_MS,
    normalised: bool = False,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Download assigned REW measurements and write timing-aligned WAV files.

    ``assignments`` is a sequence of ``{"mic", "speaker", "meas_id", "title",
    "sample_rate"?}`` mappings.  Writes one 32-bit float mono WAV per assignment
    to ``out_dir/spk{speaker:02d}_mic{mic:02d}.wav`` and returns the written
    ``{"mic", "speaker", "path"}`` entries.  Raises ``RewError`` if the assigned
    measurements do not all share one sample rate.
    """
    if not assignments:
        return []
    out_dir = Path(out_dir)

    fetched: list[tuple[Mapping[str, Any], np.ndarray, float]] = []
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
        fetched.append((item, samples, start_time))

    offsets = _placement_offsets([entry[2] for entry in fetched], int(sample_rate), pad_ms=pad_ms)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    for (item, samples, _start), offset in zip(fetched, offsets):
        placed = np.concatenate(
            [np.zeros(offset, dtype=np.float32), samples.astype(np.float32)]
        )
        path = out_dir / f"spk{int(item['speaker']):02d}_mic{int(item['mic']):02d}.wav"
        wavfile.write(str(path), int(sample_rate), placed)
        written.append(
            {"mic": int(item["mic"]), "speaker": int(item["speaker"]), "path": str(path)}
        )
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
