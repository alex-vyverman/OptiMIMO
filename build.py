#!/usr/bin/env python3
"""Cross-platform build script for OptiMIMO binary releases.

Builds a standalone executable using PyInstaller. The executable bundles
Python, all dependencies, and the application into a single file.

Usage:
    python build.py              # Build using optiMIMO.spec
    python build.py --clean      # Clean build artifacts first
    python build.py --debug      # Build with debug output

Requirements:
    pip install -r requirements-build.txt
    pip install -e ".[gui]"
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def get_platform_name() -> str:
    """Return a normalized platform name for the output binary."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        arch = "arm64" if machine == "arm64" else "x86_64"
        return f"macos-{arch}"
    elif system == "windows":
        return "windows-x86_64"
    elif system == "linux":
        arch = "x86_64" if machine in ("x86_64", "amd64") else machine
        return f"linux-{arch}"
    return f"{system}-{machine}"


def get_exe_extension() -> str:
    """Return the executable extension for the current platform."""
    return ".exe" if platform.system() == "Windows" else ""


def clean_build() -> None:
    """Remove build artifacts."""
    for d in ["build", "dist"]:
        p = Path(d)
        if p.exists():
            print(f"Removing {p}/")
            shutil.rmtree(p)


def build(debug: bool = False) -> Path:
    """Run PyInstaller and return the path to the output binary."""
    spec_file = Path("optiMIMO.spec")
    if not spec_file.exists():
        print(f"ERROR: {spec_file} not found", file=sys.stderr)
        sys.exit(1)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        str(spec_file),
    ]
    if debug:
        cmd.append("--log-level=DEBUG")

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print("ERROR: PyInstaller failed", file=sys.stderr)
        sys.exit(result.returncode)

    # Find the output binary
    dist_dir = Path("dist")
    exe_name = f"OptiMIMO{get_exe_extension()}"
    binary = dist_dir / exe_name
    if not binary.exists():
        # On macOS it might be in a .app bundle
        app_bundle = dist_dir / "OptiMIMO.app"
        if app_bundle.exists():
            binary = app_bundle / "Contents" / "MacOS" / "OptiMIMO"

    if not binary.exists():
        print(f"ERROR: Output binary not found in {dist_dir}/", file=sys.stderr)
        # List what's there
        if dist_dir.exists():
            for item in dist_dir.iterdir():
                print(f"  Found: {item}")
        sys.exit(1)

    return binary


def package_release(binary: Path) -> Path:
    """Create a platform-specific release package.

    On macOS the build output is a `.app` bundle, which we ad-hoc codesign
    and zip with `ditto` so Finder sees one application file with bundle
    structure preserved. On Linux/Windows we just copy the single binary.
    """
    plat = get_platform_name()
    release_name = f"OptiMIMO-{plat}"
    release_dir = Path("releases")
    release_dir.mkdir(exist_ok=True)

    if platform.system() == "Darwin":
        app_bundle = Path("dist") / "OptiMIMO.app"
        if not app_bundle.is_dir():
            print(f"ERROR: Expected {app_bundle} bundle from PyInstaller", file=sys.stderr)
            sys.exit(1)

        # Ad-hoc codesign so Gatekeeper accepts the app via right-click → Open
        # instead of refusing it as "damaged" on Apple Silicon. No paid
        # Developer ID needed; users still see the unidentified-developer
        # prompt on first launch.
        print(f"Ad-hoc codesigning {app_bundle}")
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)],
            check=True,
        )

        output = release_dir / f"{release_name}.zip"
        if output.exists():
            output.unlink()
        print(f"Zipping {app_bundle} -> {output}")
        # ditto preserves resource forks, symlinks, and execute bits; the
        # plain `zip` utility silently corrupts macOS bundles.
        subprocess.run(
            [
                "ditto",
                "-c", "-k",
                "--sequesterRsrc",
                "--keepParent",
                str(app_bundle),
                str(output),
            ],
            check=True,
        )

        size_mb = output.stat().st_size / (1024 * 1024)
        print(f"Release: {output} ({size_mb:.1f} MB)")
        return output

    ext = get_exe_extension()
    output = release_dir / f"{release_name}{ext}"

    print(f"Copying {binary} -> {output}")
    shutil.copy2(binary, output)

    # Make executable on Unix
    if platform.system() != "Windows":
        output.chmod(0o755)

    # Print file size
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Release: {output} ({size_mb:.1f} MB)")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OptiMIMO binary release")
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts first")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--skip-package", action="store_true", help="Skip release packaging")
    args = parser.parse_args()

    if args.clean:
        clean_build()

    binary = build(debug=args.debug)

    if not args.skip_package:
        output = package_release(binary)
        print(f"\nBuild complete: {output}")
    else:
        print(f"\nBuild complete: {binary}")


if __name__ == "__main__":
    main()
