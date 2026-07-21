"""Minimal WAV reader/writer using only stdlib and NumPy.

Replaces ``scipy.io.wavfile`` for the subset OptiMIMO needs:

* **Reading**: PCM 16/24/32-bit integer and IEEE float32 WAV files.
  Integer samples are returned in their native dtype (24-bit is stored
  in ``int32``, left-shifted by 8 to fill the range — matching SciPy's
  behaviour).  Float samples are returned as ``float32``.
* **Writing**: IEEE float32 WAV files (mono or multi-channel).

The parser handles standard RIFF/WAVE files with ``fmt `` and ``data``
chunks.  Exotic chunks (``LIST``, ``fact``, ``JUNK``, etc.) are skipped.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Union

import numpy as np

_WAVE_FORMAT_PCM = 1
_WAVE_FORMAT_IEEE_FLOAT = 3


def read(path: Union[str, Path]) -> tuple[int, np.ndarray]:
    """Read a WAV file and return ``(sample_rate, data)``.

    ``data`` is a 1-D array for mono files or a 2-D ``(samples, channels)``
    array for multi-channel files.  The dtype is ``int16``, ``int32``, or
    ``float32`` depending on the WAV encoding.
    """
    with open(path, "rb") as fh:
        riff_id = fh.read(4)
        if riff_id != b"RIFF":
            raise ValueError(f"Not a RIFF file: {path}")
        _chunk_size = struct.unpack("<I", fh.read(4))[0]
        wave_id = fh.read(4)
        if wave_id != b"WAVE":
            raise ValueError(f"Not a WAVE file: {path}")

        format_tag = None
        channels = None
        sample_rate = None
        bits_per_sample = None
        data_bytes = None

        while True:
            chunk_header = fh.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id = chunk_header[:4]
            chunk_len = struct.unpack("<I", chunk_header[4:])[0]

            if chunk_id == b"fmt ":
                fmt_data = fh.read(chunk_len)
                if len(fmt_data) < 16:
                    raise ValueError(f"Truncated fmt chunk in {path}")
                format_tag, channels, sample_rate, _byte_rate, _block_align, bits_per_sample = (
                    struct.unpack("<HHIIHH", fmt_data[:16])
                )
                if chunk_len > 16:
                    fh.read(chunk_len - 16)
            elif chunk_id == b"data":
                data_bytes = fh.read(chunk_len)
                break
            else:
                fh.seek(chunk_len, 1)

    if format_tag is None or channels is None or sample_rate is None or bits_per_sample is None:
        raise ValueError(f"Missing fmt chunk in {path}")
    if data_bytes is None:
        raise ValueError(f"Missing data chunk in {path}")

    if format_tag == _WAVE_FORMAT_PCM:
        if bits_per_sample == 16:
            dtype = np.int16
            samples = np.frombuffer(data_bytes, dtype="<i2")
        elif bits_per_sample == 24:
            n_samples = len(data_bytes) // 3
            raw = np.frombuffer(data_bytes, dtype=np.uint8).reshape(n_samples, 3)
            samples = np.empty(n_samples, dtype=np.int32)
            for i in range(n_samples):
                b0, b1, b2 = int(raw[i, 0]), int(raw[i, 1]), int(raw[i, 2])
                value = b0 | (b1 << 8) | (b2 << 16)
                if value >= 0x800000:
                    value -= 0x1000000
                samples[i] = value << 8
        elif bits_per_sample == 32:
            dtype = np.int32
            samples = np.frombuffer(data_bytes, dtype="<i4")
        else:
            raise ValueError(f"Unsupported PCM bit depth {bits_per_sample} in {path}")
    elif format_tag == _WAVE_FORMAT_IEEE_FLOAT:
        if bits_per_sample != 32:
            raise ValueError(f"Unsupported float bit depth {bits_per_sample} in {path}")
        dtype = np.float32
        samples = np.frombuffer(data_bytes, dtype="<f4")
    else:
        raise ValueError(f"Unsupported WAVE format tag {format_tag} in {path}")

    if channels > 1:
        samples = samples.reshape(-1, channels)

    return int(sample_rate), samples


def write(path: Union[str, Path], sample_rate: int, data: np.ndarray) -> None:
    """Write a float32 WAV file.

    ``data`` may be 1-D (mono) or 2-D ``(samples, channels)``.
    """
    data = np.asarray(data, dtype=np.float32)
    if data.ndim == 1:
        channels = 1
        n_samples = data.shape[0]
    elif data.ndim == 2:
        n_samples, channels = data.shape
    else:
        raise ValueError(f"Unsupported data shape {data.shape}")

    bits_per_sample = 32
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_bytes = data.tobytes()
    data_size = len(data_bytes)

    fmt_chunk = struct.pack(
        "<HHIIHH",
        _WAVE_FORMAT_IEEE_FLOAT,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )

    with open(path, "wb") as fh:
        fh.write(b"RIFF")
        fh.write(struct.pack("<I", 4 + 8 + len(fmt_chunk) + 8 + data_size))
        fh.write(b"WAVE")
        fh.write(b"fmt ")
        fh.write(struct.pack("<I", len(fmt_chunk)))
        fh.write(fmt_chunk)
        fh.write(b"data")
        fh.write(struct.pack("<I", data_size))
        fh.write(data_bytes)
