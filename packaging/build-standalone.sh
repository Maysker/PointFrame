#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python -m PyInstaller \
  --noconfirm \
  --clean \
  packaging/PointFrame.spec

echo
echo "Built: dist/PointFrame/PointFrame"
du -sh dist/PointFrame
