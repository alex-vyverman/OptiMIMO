#!/usr/bin/env bash
# Build a zip of the optimimo package for Pyodide to load.
# Excludes gui/, __pycache__, .DS_Store, assets/ (not needed for the solver core).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
zip -r spike/optimimo.zip optimimo/ \
  -x "optimimo/gui/*" \
  -x "optimimo/__pycache__/*" \
  -x "optimimo/**/__pycache__/*" \
  -x "optimimo/.DS_Store" \
  -x "optimimo/**/.DS_Store" \
  -x "optimimo/assets/*" \
  -x "optimimo/__main__.py"
echo "Built spike/optimimo.zip"
