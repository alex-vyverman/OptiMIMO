"""FIR coefficient file export."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from ..core import wav


def export_firs(
    firs: np.ndarray,
    sample_rate: int,
    output_dir: Path,
    output_format: str,
) -> dict[tuple[int, int], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_format = output_format.lower()
    if output_format not in {"wav", "txt", "both"}:
        raise ValueError("output_format must be wav, txt, or both.")

    paths: dict[tuple[int, int], dict[str, Path]] = {}
    num_outputs = firs.shape[1]
    num_inputs = firs.shape[2]
    for output_channel in range(num_outputs):
        for input_channel in range(num_inputs):
            stem = f"fir_o{output_channel:02d}_i{input_channel:02d}"
            coeffs = np.asarray(firs[:, output_channel, input_channel], dtype=np.float64)
            item: dict[str, Path] = {}
            if output_format in {"wav", "both"}:
                wav_path = output_dir / f"{stem}.wav"
                wav.write(wav_path, sample_rate, coeffs.astype(np.float32))
                item["wav"] = wav_path
            if output_format in {"txt", "both"}:
                txt_path = output_dir / f"{stem}.txt"
                np.savetxt(txt_path, coeffs, fmt="%.12e")
                item["txt"] = txt_path
            paths[(output_channel, input_channel)] = item
    return paths
