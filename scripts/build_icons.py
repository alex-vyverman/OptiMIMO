#!/usr/bin/env python3
"""Generate app icons (.icns, .ico, and a square PNG master) from the
full OptiMIMO logo lockup.

This crops the glyph (the square + arrow symbol on the left of the
horizontal logo) to a centered square with breathing room, then emits
the multi-size formats PyInstaller and the README consume.

Usage:
    python scripts/build_icons.py

Requires Pillow. On macOS, uses the built-in `iconutil` to assemble the
.icns from a generated .iconset folder.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "images" / "optimimo-FULL.png"
IMAGES_DIR = REPO / "images"

# macOS .icns input sizes (each "@2x" variant is double the base size).
ICNS_SIZES = [
    ("16x16", 16),
    ("16x16@2x", 32),
    ("32x32", 32),
    ("32x32@2x", 64),
    ("128x128", 128),
    ("128x128@2x", 256),
    ("256x256", 256),
    ("256x256@2x", 512),
    ("512x512", 512),
    ("512x512@2x", 1024),
]

# Multi-size .ico sizes that Windows and Quasar/web-favicon consumers want.
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def detect_glyph_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """Locate the bounding box of just the glyph (left of the wordmark).

    Finds all "content" pixels (anything noticeably darker than the
    off-white background), then splits glyph from wordmark at the first
    large gap of empty columns. Returns (left, top, right, bottom).
    """
    arr = np.array(img)
    luma = arr[..., :3].astype(int).mean(axis=2)
    alpha = arr[..., 3]
    content = (luma < 220) & (alpha > 200)

    cols_with_content = np.where(content.any(axis=0))[0]
    diffs = np.diff(cols_with_content)
    big_gaps = np.where(diffs > 30)[0]
    if len(big_gaps):
        right_edge = int(cols_with_content[big_gaps[0]])
    else:
        right_edge = int(cols_with_content.max())
    left_edge = int(cols_with_content.min())

    glyph_mask = content[:, left_edge : right_edge + 1]
    rows_with_content = np.where(glyph_mask.any(axis=1))[0]
    top = int(rows_with_content.min())
    bottom = int(rows_with_content.max())
    return left_edge, top, right_edge, bottom


def crop_glyph_master() -> Image.Image:
    img = Image.open(SOURCE).convert("RGBA")
    left, top, right, bottom = detect_glyph_bbox(img)
    glyph_w = right - left
    glyph_h = bottom - top
    side = max(glyph_w, glyph_h)
    # 8% padding on every side keeps the icon from feeling cramped without
    # pushing the crop into the wordmark.
    side = int(round(side * 1.16))
    cx = (left + right) // 2
    cy = (top + bottom) // 2
    half = side // 2

    # Clamp so we never bleed into the wordmark (right of `right_edge`)
    # or past the image bounds. If clamping shrinks the box on one axis,
    # shrink the other axis to match so the result stays square.
    img_w, img_h = img.size
    # Wordmark starts at the first content column > right_edge; treat the
    # column after the glyph + half the original gap as the safe boundary.
    arr = np.array(img)
    luma = arr[..., :3].astype(int).mean(axis=2)
    content_cols = np.where(((luma < 220) & (arr[..., 3] > 200)).any(axis=0))[0]
    next_after_glyph = content_cols[content_cols > right]
    safe_right = int(next_after_glyph.min()) - 1 if len(next_after_glyph) else img_w - 1

    max_right = min(cx + half, safe_right)
    max_left = max(cx - half, 0)
    max_bottom = min(cy + half, img_h - 1)
    max_top = max(cy - half, 0)
    # Re-square the box around (cx, cy)
    side = 2 * min(cx - max_left, max_right - cx, cy - max_top, max_bottom - cy)
    half = side // 2
    box = (cx - half, cy - half, cx + half, cy + half)
    return img.crop(box)


def write_png(img: Image.Image, path: Path) -> None:
    img.save(path, format="PNG", optimize=True)
    print(f"  wrote {path.relative_to(REPO)} ({path.stat().st_size // 1024} KB)")


def build_icns(master: Image.Image, out_path: Path) -> None:
    if sys.platform != "darwin":
        print("  skipping .icns (iconutil is macOS-only)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "optimimo.iconset"
        iconset.mkdir()
        for name, size in ICNS_SIZES:
            resized = master.resize((size, size), Image.LANCZOS)
            resized.save(iconset / f"icon_{name}.png", format="PNG", optimize=True)
        subprocess.run(
            ["iconutil", "--convert", "icns", "--output", str(out_path), str(iconset)],
            check=True,
        )
    print(f"  wrote {out_path.relative_to(REPO)} ({out_path.stat().st_size // 1024} KB)")


def build_ico(master: Image.Image, out_path: Path) -> None:
    # Pillow writes multi-size .ico when given a list of sizes.
    master.save(
        out_path,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
    )
    print(f"  wrote {out_path.relative_to(REPO)} ({out_path.stat().st_size // 1024} KB)")


def main() -> None:
    if not SOURCE.is_file():
        sys.exit(f"source image not found: {SOURCE}")
    print(f"Cropping glyph from {SOURCE.relative_to(REPO)}")
    master = crop_glyph_master()

    glyph_png = IMAGES_DIR / "optimimo-glyph.png"
    write_png(master, glyph_png)

    build_icns(master, IMAGES_DIR / "optimimo.icns")
    build_ico(master, IMAGES_DIR / "optimimo.ico")


if __name__ == "__main__":
    main()
