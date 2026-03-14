#!/usr/bin/env python3
"""Plot a load-cell calibration JSON as fit + residuals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plot calibration.json fit and residuals")
    p.add_argument("calibration", type=Path, nargs="?", default=Path("calibration.json"))
    p.add_argument("--out-prefix", default="calibration_fit_current", help="Output file prefix")
    p.add_argument("--dpi", type=int, default=160)
    return p.parse_args()


def main() -> int:
    args = parse_args()

    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        raise SystemExit("matplotlib is required: python -m pip install matplotlib") from exc

    obj = json.loads(args.calibration.read_text())
    pts = obj.get("points", [])
    if len(pts) < 2:
        raise SystemExit("Calibration file does not contain enough points.")

    scale = float(obj["scale"])
    offset = float(obj["offset"])
    r2 = float(obj.get("fit", {}).get("r2", float("nan")))

    x = np.array([float(p["bridge_value"]) for p in pts], dtype=float)
    y = np.array([float(p["force_N"]) for p in pts], dtype=float)
    m = np.array([float(p.get("mass_g", np.nan)) for p in pts], dtype=float)
    yhat = (x - offset) * scale
    res = y - yhat

    order = np.argsort(x)
    xs = x[order]
    ys = y[order]
    yhs = yhat[order]
    ms = m[order]
    rs = res[order]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.5, 7.0), gridspec_kw={"height_ratios": [3, 1]})

    ax1.scatter(xs, ys, s=50, color="#1f77b4", label="Measured points", zorder=3)
    ax1.plot(xs, yhs, color="#d62728", lw=2, label="Linear fit", zorder=2)
    for xi, yi, mi in zip(xs, ys, ms):
        ax1.annotate(f"{mi:g} g", (xi, yi), textcoords="offset points", xytext=(5, 5), fontsize=8)
    ax1.set_title(f"Load Cell Calibration (R^2={r2:.4f})")
    ax1.set_xlabel("Bridge value (raw)")
    ax1.set_ylabel("Force (N)")
    ax1.grid(alpha=0.25)
    ax1.legend(loc="upper left")

    text = (
        f"scale = {scale:.6f} N/bridge_unit\n"
        f"offset = {offset:.12f}\n"
        f"max |residual| = {np.max(np.abs(res)):.6f} N\n"
        f"mean |residual| = {np.mean(np.abs(res)):.6f} N"
    )
    ax1.text(
        0.02,
        0.98,
        text,
        transform=ax1.transAxes,
        va="top",
        ha="left",
        fontsize=9,
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.85, "edgecolor": "0.75"},
    )

    ax2.axhline(0.0, color="k", lw=1)
    ax2.scatter(xs, rs, s=40, color="#2ca02c")
    ax2.set_xlabel("Bridge value (raw)")
    ax2.set_ylabel("Residual (N)")
    ax2.grid(alpha=0.25)

    fig.tight_layout()

    out_png = Path(f"{args.out_prefix}.png")
    out_svg = Path(f"{args.out_prefix}.svg")
    fig.savefig(out_png, dpi=args.dpi)
    fig.savefig(out_svg)
    print(out_png)
    print(out_svg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
