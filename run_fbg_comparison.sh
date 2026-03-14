#!/usr/bin/env bash
# Quick launcher for FBG1 vs FBG2 comparison visualization

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Use whisker conda environment
if command -v conda >/dev/null 2>&1; then
    echo "Using whisker conda environment..."
    conda run -n whisker bash -lc 'export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:$LD_LIBRARY_PATH"; python fbg/visualize_fbg_comparison.py'
else
    echo "Conda not found, using system Python..."
    python fbg/visualize_fbg_comparison.py
fi
