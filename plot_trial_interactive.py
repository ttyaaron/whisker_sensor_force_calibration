#!/usr/bin/env python3
"""
Interactive matplotlib viewer for one experiment trial trace CSV.

Usage example:
  conda run -n whisker python plot_trial_interactive.py \
    --trace-csv experiment_data/0217_NO.2_Glue_on_center_displacement_20260219_180237/trace_trial_01.csv \
    --lowpass-cutoff-hz 10
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Dict, List, Tuple


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Interactive single-trial visualization")
    p.add_argument(
        "--trace-csv",
        type=Path,
        required=True,
        help="Path to trace CSV (e.g. trace_trial_01.csv)",
    )
    p.add_argument(
        "--lowpass-cutoff-hz",
        type=float,
        default=10.0,
        help="First-order low-pass cutoff in Hz (<=0 disables filtering).",
    )
    p.add_argument(
        "--gap-threshold-s",
        type=float,
        default=0.35,
        help="Break line when time gaps exceed this threshold.",
    )
    p.add_argument(
        "--show-target-lines",
        action="store_true",
        help="Show requested start/end X reference lines in stage subplot.",
    )
    return p.parse_args()


def _to_float(text: str) -> float:
    try:
        return float(text)
    except Exception:
        return float("nan")


def load_trace_csv(path: Path) -> Dict[str, List[float | str]]:
    elapsed_s: List[float] = []
    force_n: List[float] = []
    fbg_nm: List[float] = []
    x_mm: List[float] = []
    req_start_x_mm: List[float] = []
    req_end_x_mm: List[float] = []
    phases: List[str] = []

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            elapsed_s.append(_to_float(str(row.get("elapsed_s", ""))))
            force_n.append(_to_float(str(row.get("force_z_n", ""))))
            fbg_nm.append(_to_float(str(row.get("fbg1_nm", ""))))
            x_mm.append(_to_float(str(row.get("x_mm", ""))))
            req_start_x_mm.append(_to_float(str(row.get("requested_start_x_mm", ""))))
            req_end_x_mm.append(_to_float(str(row.get("requested_end_x_mm", ""))))
            phases.append(str(row.get("phase", "")).strip())

    return {
        "elapsed_s": elapsed_s,
        "force_n": force_n,
        "fbg_nm": fbg_nm,
        "x_mm": x_mm,
        "requested_start_x_mm": req_start_x_mm,
        "requested_end_x_mm": req_end_x_mm,
        "phase": phases,
    }


def lowpass_filter_series(
    t: List[float],
    y: List[float],
    *,
    cutoff_hz: float,
    reset_gap_s: float,
) -> List[float]:
    if cutoff_hz <= 0.0 or len(t) != len(y) or not t:
        return list(y)

    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    y_out: List[float] = []
    has_state = False
    prev_t = float("nan")
    prev_y = float("nan")

    for ti, yi in zip(t, y):
        if not math.isfinite(ti) or not math.isfinite(yi):
            y_out.append(float("nan"))
            has_state = False
            prev_t = float("nan")
            prev_y = float("nan")
            continue

        if not has_state:
            prev_t = float(ti)
            prev_y = float(yi)
            y_out.append(prev_y)
            has_state = True
            continue

        dt = float(ti) - float(prev_t)
        if (not math.isfinite(dt)) or dt <= 0.0 or dt > reset_gap_s:
            prev_t = float(ti)
            prev_y = float(yi)
            y_out.append(prev_y)
            continue

        alpha = dt / (rc + dt)
        prev_y = prev_y + alpha * (float(yi) - prev_y)
        prev_t = float(ti)
        y_out.append(prev_y)

    return y_out


def break_series_on_large_gaps(
    t: List[float],
    y: List[float],
    *,
    gap_threshold_s: float,
) -> Tuple[List[float], List[float]]:
    if len(t) != len(y) or len(t) < 2:
        return t, y

    tt: List[float] = []
    yy: List[float] = []
    for i in range(len(t) - 1):
        t0 = t[i]
        t1 = t[i + 1]
        y0 = y[i]
        tt.append(t0)
        yy.append(y0)
        if math.isfinite(t0) and math.isfinite(t1):
            dt = t1 - t0
            if dt > gap_threshold_s:
                tt.append(t0 + 0.5 * dt)
                yy.append(float("nan"))

    tt.append(t[-1])
    yy.append(y[-1])
    return tt, yy


def phase_change_markers(times: List[float], phases: List[str]) -> List[Tuple[float, str]]:
    marks: List[Tuple[float, str]] = []
    prev = None
    for t, ph in zip(times, phases):
        if not math.isfinite(t):
            continue
        if ph != prev:
            marks.append((t, ph))
            prev = ph
    return marks


def phase_intervals(changes: List[Tuple[float, str]], t_end: float) -> List[Tuple[float, float, str]]:
    intervals: List[Tuple[float, float, str]] = []
    for idx, (t0, phase) in enumerate(changes):
        t1 = changes[idx + 1][0] if (idx + 1) < len(changes) else t_end
        if math.isfinite(t0) and math.isfinite(t1) and t1 > t0:
            intervals.append((t0, t1, phase))
    return intervals


def averaging_intervals(changes: List[Tuple[float, str]], t_end: float) -> List[Tuple[float, float, str]]:
    keep = {"start_reached_avg_window", "end_reached_avg_window"}
    return [(t0, t1, ph) for (t0, t1, ph) in phase_intervals(changes, t_end) if ph in keep]


def _first_finite(values: List[float]) -> float:
    for v in values:
        if math.isfinite(v):
            return v
    return float("nan")


def main() -> int:
    args = parse_args()
    trace_path = args.trace_csv.resolve()
    if not trace_path.is_file():
        print(f"Trace CSV not found: {trace_path}")
        return 1

    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        print(f"matplotlib unavailable: {exc}")
        return 1

    data = load_trace_csv(trace_path)
    t = [float(v) for v in data["elapsed_s"]]  # type: ignore[arg-type]
    force = [float(v) for v in data["force_n"]]  # type: ignore[arg-type]
    fbg = [float(v) for v in data["fbg_nm"]]  # type: ignore[arg-type]
    x = [float(v) for v in data["x_mm"]]  # type: ignore[arg-type]
    req_start = [float(v) for v in data["requested_start_x_mm"]]  # type: ignore[arg-type]
    req_end = [float(v) for v in data["requested_end_x_mm"]]  # type: ignore[arg-type]
    phases = [str(v) for v in data["phase"]]  # type: ignore[arg-type]

    cutoff = max(0.0, float(args.lowpass_cutoff_hz))
    gap_threshold = max(0.01, float(args.gap_threshold_s))

    force_f = lowpass_filter_series(t, force, cutoff_hz=cutoff, reset_gap_s=gap_threshold)
    fbg_f = lowpass_filter_series(t, fbg, cutoff_hz=cutoff, reset_gap_s=gap_threshold)
    x_f = lowpass_filter_series(t, x, cutoff_hz=cutoff, reset_gap_s=gap_threshold)

    t_force, force_plot = break_series_on_large_gaps(t, force_f, gap_threshold_s=gap_threshold)
    t_fbg, fbg_plot = break_series_on_large_gaps(t, fbg_f, gap_threshold_s=gap_threshold)
    t_stage, x_plot = break_series_on_large_gaps(t, x_f, gap_threshold_s=gap_threshold)

    finite_t = [tv for tv in t if math.isfinite(tv)]
    t_max = max(finite_t) if finite_t else 1.0
    changes = phase_change_markers(t, phases)
    avg_intervals = averaging_intervals(changes, t_max)

    fig, axes = plt.subplots(
        3,
        1,
        figsize=(11, 8.2),
        sharex=True,
        gridspec_kw={"height_ratios": [1.15, 1.15, 1.0]},
    )
    ax_force, ax_fbg, ax_stage = axes

    for t0, t1, phase in avg_intervals:
        if phase == "start_reached_avg_window":
            color = "#90be6d"
            label_txt = "start avg"
        else:
            color = "#f9c74f"
            label_txt = "end avg"
        for ax in axes:
            ax.axvspan(t0, t1, color=color, alpha=0.18, lw=0)
        ax_stage.text(
            0.5 * (t0 + t1),
            1.02,
            label_txt,
            transform=ax_stage.get_xaxis_transform(),
            ha="center",
            va="bottom",
            fontsize=8,
            color="#333",
        )

    ax_force.plot(t_force, force_plot, color="#1f77b4", lw=1.5)
    ax_force.set_ylabel("Force (N)")
    ax_force.grid(True, alpha=0.25)

    ax_fbg.plot(t_fbg, fbg_plot, color="#d62728", lw=1.5)
    ax_fbg.set_ylabel("FBG1 (nm)")
    ax_fbg.grid(True, alpha=0.25)

    ax_stage.plot(t_stage, x_plot, color="#2a9d8f", lw=1.6, label="x_mm")
    if args.show_target_lines:
        rs = _first_finite(req_start)
        re = _first_finite(req_end)
        if math.isfinite(rs):
            ax_stage.axhline(rs, color="#264653", lw=1.1, ls="--", alpha=0.85, label="requested_start_x")
        if math.isfinite(re):
            ax_stage.axhline(re, color="#e76f51", lw=1.1, ls="--", alpha=0.85, label="requested_end_x")
    ax_stage.set_ylabel("Stage X (mm)")
    ax_stage.set_xlabel("Elapsed Time (s)")
    ax_stage.grid(True, alpha=0.25)
    ax_stage.legend(loc="upper left", fontsize=8, framealpha=0.85, ncol=3)

    fig.suptitle(
        f"{trace_path.parent.name} / {trace_path.name} | "
        f"LPF cutoff={cutoff:.2f} Hz"
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    print("Interactive window opened. Use toolbar zoom/pan.")
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

