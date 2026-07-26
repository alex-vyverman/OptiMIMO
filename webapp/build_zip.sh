#!/usr/bin/env bash
# Build the webapp's two zip artifacts:
#   webapp/optimimo.zip   — the optimimo package for Pyodide to load
#   webapp/extension.zip  — the REW Bridge extension, offered as a download
#                           on the Measurements page (dev-mode stopgap until
#                           the Chrome Web Store listing exists)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
zip -r webapp/optimimo.zip optimimo/ \
  -x "optimimo/gui/*" \
  -x "optimimo/__pycache__/*" \
  -x "optimimo/**/__pycache__/*" \
  -x "optimimo/.DS_Store" \
  -x "optimimo/**/.DS_Store" \
  -x "optimimo/assets/*" \
  -x "optimimo/__main__.py"
echo "Built webapp/optimimo.zip"
# extension.zip holds the extension at the zip root so users can unzip and
# point "Load unpacked" straight at the folder. Docs (*.md) and the dev
# manifest are excluded (store zip ships manifest.json only).
(cd webapp/extension && zip -r ../extension.zip . -x ".DS_Store" "./.DS_Store" "*.md" "manifest.dev.json")
echo "Built webapp/extension.zip"

