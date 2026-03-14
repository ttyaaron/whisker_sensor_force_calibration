#!/usr/bin/env python3
"""
Batch-plot trial traces from experiment_data.

For each trace CSV, generate one figure with:
- force_z_n vs elapsed_s
- fbg1_nm vs elapsed_s
- x_mm vs elapsed_s (stage position)

Also generates an index.html gallery for quick browsing.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_ROOT = ROOT_DIR / "experiment_data"
DEFAULT_OUT_DIR = ROOT_DIR / "experiment_plots"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Visualize all trial traces (force+FBG) under experiment_data")
    p.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help=f"Root directory containing run folders (default: {DEFAULT_DATA_ROOT})",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory for PNG/HTML plots (default: {DEFAULT_OUT_DIR})",
    )
    p.add_argument(
        "--glob",
        default="trace_trial_*.csv",
        help="Glob pattern for trial traces (default: trace_trial_*.csv)",
    )
    p.add_argument(
        "--include-trace-csv",
        action="store_true",
        help="Also include plain trace.csv files in addition to --glob matches.",
    )
    p.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="PNG output DPI",
    )
    p.add_argument(
        "--max-plots",
        type=int,
        default=0,
        help="Optional limit on number of plots (0 = all)",
    )
    p.add_argument(
        "--show",
        action="store_true",
        help="Show figures interactively while generating",
    )
    p.add_argument(
        "--show-target-lines",
        action="store_true",
        help="Show requested_start_x and requested_end_x horizontal reference lines in stage subplot.",
    )
    p.add_argument(
        "--gap-threshold-s",
        type=float,
        default=0.35,
        help="Break lines when consecutive elapsed_s samples exceed this threshold.",
    )
    p.add_argument(
        "--lowpass-cutoff-hz",
        type=float,
        default=10.0,
        help="First-order low-pass cutoff for plotting (Hz). Set <=0 to disable.",
    )
    return p.parse_args()


def _to_float(text: str) -> float:
    try:
        value = float(text)
    except Exception:
        return float("nan")
    return value


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
                # Insert NaN to break visual interpolation across true sampling gaps.
                tt.append(t0 + 0.5 * dt)
                yy.append(float("nan"))

    tt.append(t[-1])
    yy.append(y[-1])
    return tt, yy


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


def make_plot(
    trace_path: Path,
    data: Dict[str, List[float | str]],
    out_png: Path,
    *,
    dpi: int,
    show: bool,
    show_target_lines: bool,
    gap_threshold_s: float,
    lowpass_cutoff_hz: float,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        raise RuntimeError(
            "matplotlib is required. Install with: python -m pip install matplotlib"
        ) from exc

    t = [float(v) for v in data["elapsed_s"]]  # type: ignore[arg-type]
    f = [float(v) for v in data["force_n"]]  # type: ignore[arg-type]
    b = [float(v) for v in data["fbg_nm"]]  # type: ignore[arg-type]
    x = [float(v) for v in data["x_mm"]]  # type: ignore[arg-type]
    req_start = [float(v) for v in data["requested_start_x_mm"]]  # type: ignore[arg-type]
    req_end = [float(v) for v in data["requested_end_x_mm"]]  # type: ignore[arg-type]
    p = [str(v) for v in data["phase"]]  # type: ignore[arg-type]

    f_src = lowpass_filter_series(
        t,
        f,
        cutoff_hz=max(0.0, float(lowpass_cutoff_hz)),
        reset_gap_s=max(0.01, float(gap_threshold_s)),
    )
    b_src = lowpass_filter_series(
        t,
        b,
        cutoff_hz=max(0.0, float(lowpass_cutoff_hz)),
        reset_gap_s=max(0.01, float(gap_threshold_s)),
    )
    x_src = lowpass_filter_series(
        t,
        x,
        cutoff_hz=max(0.0, float(lowpass_cutoff_hz)),
        reset_gap_s=max(0.01, float(gap_threshold_s)),
    )

    t_force, f_plot = break_series_on_large_gaps(t, f_src, gap_threshold_s=gap_threshold_s)
    t_fbg, b_plot = break_series_on_large_gaps(t, b_src, gap_threshold_s=gap_threshold_s)
    t_stage, x_plot = break_series_on_large_gaps(t, x_src, gap_threshold_s=gap_threshold_s)

    fig, axes = plt.subplots(
        3,
        1,
        figsize=(10.5, 8.2),
        sharex=True,
        gridspec_kw={"height_ratios": [1.15, 1.15, 1.0]},
    )
    ax_force, ax_fbg, ax_stage = axes

    changes = phase_change_markers(t, p)
    finite_t = [tv for tv in t if math.isfinite(tv)]
    if finite_t:
        t_min = min(finite_t)
        t_max = max(finite_t)
        t_span = max(1e-9, t_max - t_min)
    else:
        t_min = 0.0
        t_max = 1.0
        t_span = 1.0

    avg_intervals = averaging_intervals(changes, t_max)
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
            bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "edgecolor": "none", "alpha": 0.7},
        )

    ax_force.plot(t_force, f_plot, color="#1f77b4", lw=1.5)
    ax_force.set_ylabel("Force (N)")
    ax_force.grid(True, alpha=0.25)

    ax_fbg.plot(t_fbg, b_plot, color="#d62728", lw=1.5)
    ax_fbg.set_ylabel("FBG1 (nm)")
    ax_fbg.grid(True, alpha=0.25)

    ax_stage.plot(t_stage, x_plot, color="#2a9d8f", lw=1.6, label="x_mm")
    rs = _first_finite(req_start)
    re = _first_finite(req_end)
    if show_target_lines and math.isfinite(rs):
        ax_stage.axhline(rs, color="#264653", lw=1.1, ls="--", alpha=0.85, label="requested_start_x")
    if show_target_lines and math.isfinite(re):
        ax_stage.axhline(re, color="#e76f51", lw=1.1, ls="--", alpha=0.85, label="requested_end_x")
    ax_stage.set_ylabel("Stage X (mm)")
    ax_stage.set_xlabel("Elapsed Time (s)")
    ax_stage.grid(True, alpha=0.25)
    ax_stage.legend(loc="upper left", fontsize=8, framealpha=0.85, ncol=3)

    fig.suptitle(f"{trace_path.parent.name} / {trace_path.name}")
    fig.tight_layout(rect=(0, 0, 1, 0.95))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=dpi)

    if show:
        plt.show(block=False)
        plt.pause(0.001)
    plt.close(fig)


def write_index(
    out_dir: Path,
    run_to_pngs: Dict[str, List[Path]],
    *,
    lowpass_cutoff_hz: float,
) -> Path:
    index_path = out_dir / "index.html"

    lines: List[str] = []
    lines.append("<!doctype html>")
    lines.append("<html><head><meta charset='utf-8'><title>Trial Plots</title>")
    lines.append("<style>")
    lines.append("body{font-family:Arial,sans-serif;margin:20px;background:#f7f7f7;color:#222}")
    lines.append("h1{margin-bottom:6px}")
    lines.append("h2{margin-top:28px;border-bottom:1px solid #ccc;padding-bottom:4px}")
    lines.append(".card{background:white;border:1px solid #ddd;border-radius:8px;padding:12px;margin:12px 0}")
    lines.append("img{max-width:100%;height:auto;border:1px solid #ddd;border-radius:4px}")
    lines.append(".meta{font-size:13px;color:#555;margin-bottom:8px}")
    lines.append("</style></head><body>")
    lines.append("<h1>Experiment Trial Plots</h1>")
    lines.append("<div class='meta'>Each figure: Force (top), FBG1 (middle), Stage X (bottom). Only the two averaging windows are highlighted: start avg (green) and end avg (yellow).</div>")
    lines.append("<div class='meta'>Large sampling gaps (e.g., during blocking stage moves) are shown as breaks, not straight bridges.</div>")
    if lowpass_cutoff_hz > 0:
        lines.append(
            f"<div class='meta'>Low-pass filter applied for plotting: cutoff = {lowpass_cutoff_hz:.2f} Hz.</div>"
        )
    else:
        lines.append("<div class='meta'>Low-pass filter disabled.</div>")

    for run_name in sorted(run_to_pngs.keys()):
        lines.append(f"<h2>{run_name}</h2>")
        for png in sorted(run_to_pngs[run_name]):
            rel = png.relative_to(out_dir)
            lines.append("<div class='card'>")
            lines.append(f"<div class='meta'>{rel.as_posix()}</div>")
            lines.append(f"<img src='{rel.as_posix()}' alt='{rel.as_posix()}'>")
            lines.append("</div>")

    lines.append("</body></html>")
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def main() -> int:
    args = parse_args()

    data_root = args.data_root.resolve()
    out_dir = args.out_dir.resolve()

    if not data_root.is_dir():
        print(f"Data root not found: {data_root}")
        return 1

    trace_set = {p.resolve() for p in data_root.rglob(args.glob)}
    if args.include_trace_csv:
        trace_set.update(p.resolve() for p in data_root.rglob("trace.csv"))
    trace_paths = sorted(trace_set)
    if not trace_paths:
        print(f"No files matched '{args.glob}' under {data_root}")
        return 1

    if args.max_plots > 0:
        trace_paths = trace_paths[: args.max_plots]

    run_to_pngs: Dict[str, List[Path]] = defaultdict(list)

    for i, trace_path in enumerate(trace_paths, start=1):
        rel_parent = trace_path.parent.relative_to(data_root)
        out_png = out_dir / rel_parent / f"{trace_path.stem}.png"

        data = load_trace_csv(trace_path)
        make_plot(
            trace_path,
            data,
            out_png,
            dpi=args.dpi,
            show=args.show,
            show_target_lines=args.show_target_lines,
            gap_threshold_s=max(0.01, float(args.gap_threshold_s)),
            lowpass_cutoff_hz=float(args.lowpass_cutoff_hz),
        )

        run_key = rel_parent.as_posix() if rel_parent.as_posix() != "." else trace_path.parent.name
        run_to_pngs[run_key].append(out_png)
        print(f"[{i}/{len(trace_paths)}] {trace_path} -> {out_png}")

    index_path = write_index(
        out_dir,
        run_to_pngs,
        lowpass_cutoff_hz=float(args.lowpass_cutoff_hz),
    )
    print(f"\nDone. Wrote {len(trace_paths)} plots.")
    print(f"Index: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
