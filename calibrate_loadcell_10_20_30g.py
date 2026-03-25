#!/usr/bin/env python3
"""
Quick calibration helper for PhidgetBridge load cells using known masses
(default: 0, 2, 51.8, 144.3, 137.1, 186.9 g).

This script computes a linear mapping:
    force_N = (bridge_value - offset) * scale
and writes calibration JSON compatible with read_phidgetbridge_loadcell.py (--cal).
"""

import argparse
import json
import signal
import sys
import threading
from typing import Dict, List

from read_phidgetbridge_loadcell import (
    GRAM_TO_NEWTON,
    PHIDGET_IMPORT_ERROR,
    PhidgetLoadCell,
    compute_interval_ms,
    fit_line,
    utc_now_iso,
)


def parse_masses(text: str) -> List[float]:
    out: List[float] = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        out.append(float(token))
    return out


def save_json(path: str, payload: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Calibrate PhidgetBridge load cell with known masses "
            "(default 0,2,51.8,144.3,137.1,186.9 g)."
        )
    )
    p.add_argument("--channel", type=int, default=0, choices=[0, 1, 2, 3], help="Bridge channel")
    p.add_argument("--serial", type=int, default=None, help="Optional Phidget serial number")
    p.add_argument("--gain", type=int, default=128, choices=[1, 8, 16, 32, 64, 128], help="Bridge gain")
    p.add_argument("--rate", type=float, default=200.0, help="Sample rate in Hz")
    p.add_argument("--interval-ms", type=float, default=None, help="Override data interval in ms")
    p.add_argument("--capture-window", type=float, default=2.0, help="Average window per mass (seconds)")
    p.add_argument(
        "--masses-g",
        default="0,2,51.8,144.3,137.1,186.9",
        help="Comma-separated masses in grams, e.g. 0,2,51.8,144.3,137.1,186.9",
    )
    p.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of repeated captures per mass (fit uses per-mass averaged raw value).",
    )
    p.add_argument(
        "--with-zero",
        action="store_true",
        help="Also capture an initial 0 g point before the listed masses",
    )
    p.add_argument("--out", default="calibration.json", help="Output calibration JSON")
    p.add_argument("--notes", default="", help="Notes saved in calibration file")
    p.add_argument("--attach-timeout", type=float, default=5.0, help="Seconds per attach attempt")
    return p


def main() -> int:
    args = build_parser().parse_args()

    if PHIDGET_IMPORT_ERROR is not None:
        print(
            "Phidget22 import failed. Install dependencies first:\n"
            "  sudo apt install libusb-1.0-0\n"
            "  python3 -m pip install phidget22\n"
            f"Import error: {PHIDGET_IMPORT_ERROR}",
            file=sys.stderr,
        )
        return 2

    masses = parse_masses(args.masses_g)
    if args.with_zero and (not masses or masses[0] != 0.0):
        masses = [0.0] + masses

    if len(masses) < 2:
        print("Need at least 2 masses for linear calibration.", file=sys.stderr)
        return 2

    if int(args.repeats) < 1:
        print("--repeats must be >= 1.", file=sys.stderr)
        return 2

    interval_ms = compute_interval_ms(args.rate, args.interval_ms)

    stop_event = threading.Event()

    def _handle_signal(_sig, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    sensor = PhidgetLoadCell(
        channel=args.channel,
        interval_ms=interval_ms,
        gain=args.gain,
        avg_window=1,
        serial_number=args.serial,
        calibration=None,
        stop_event=stop_event,
    )

    points = []

    try:
        sensor.open_with_retry(args.attach_timeout)

        print("Calibration sequence")
        print(f"- Channel: {args.channel}")
        print(f"- Masses (g): {masses}")
        print(f"- Repeats per mass: {args.repeats}")
        print(f"- Capture window: {args.capture_window:.2f} s")
        print("- Keep setup stable before each capture")

        for mass_g in masses:
            if stop_event.is_set():
                break

            repeat_raws = []
            repeat_samples = []
            for rep_idx in range(1, args.repeats + 1):
                if mass_g == 0.0:
                    prompt = f"Remove all load (0 g), then press Enter to capture ({rep_idx}/{args.repeats})..."
                else:
                    prompt = f"Place {mass_g:g} g, then press Enter to capture ({rep_idx}/{args.repeats})..."
                input(f"\n{prompt}")

                avg_raw_rep, n_rep = sensor.get_average_raw(args.capture_window)
                repeat_raws.append(float(avg_raw_rep))
                repeat_samples.append(int(n_rep))
                print(
                    f"  [rep {rep_idx}/{args.repeats}] raw_avg={avg_raw_rep:.12g} samples={n_rep}"
                )

            avg_raw = sum(repeat_raws) / float(len(repeat_raws))
            n = sum(repeat_samples)
            if len(repeat_raws) > 1:
                mean_rep = avg_raw
                var_rep = sum((v - mean_rep) ** 2 for v in repeat_raws) / float(len(repeat_raws) - 1)
                std_rep = var_rep ** 0.5
            else:
                std_rep = 0.0

            force_n = mass_g * GRAM_TO_NEWTON
            points.append(
                {
                    "mass_g": mass_g,
                    "force_N": force_n,
                    "bridge_value": avg_raw,
                    "samples": n,
                    "repeat_count": int(args.repeats),
                    "repeat_bridge_values": repeat_raws,
                    "repeat_samples": repeat_samples,
                    "repeat_std_bridge_value": std_rep,
                }
            )
            print(
                f"[captured] mass={mass_g:g} g force={force_n:.6f} N "
                f"raw_avg={avg_raw:.12g} samples={n} std={std_rep:.3g}"
            )

        if len(points) < 2:
            print("Calibration aborted or insufficient points.", file=sys.stderr)
            return 2

        xs = [p["bridge_value"] for p in points]
        ys = [p["force_N"] for p in points]
        slope, intercept, r2 = fit_line(xs, ys)
        if abs(slope) < 1e-18:
            print("Fitted slope is near zero; cannot compute calibration.", file=sys.stderr)
            return 2

        scale = slope
        offset = -intercept / slope

        payload = {
            "offset": offset,
            "scale": scale,
            "timestamp": utc_now_iso(),
            "channel": args.channel,
            "notes": args.notes,
            "gain": args.gain,
            "interval_ms": sensor.actual_interval_ms if sensor.actual_interval_ms is not None else interval_ms,
            "fit": {
                "slope": slope,
                "intercept": intercept,
                "r2": r2,
                "equation": "force_N = slope * bridge_value + intercept",
                "equivalent": "force_N = (bridge_value - offset) * scale",
            },
            "points": points,
        }

        save_json(args.out, payload)
        print("\nCalibration complete")
        print(f"Saved: {args.out}")
        print(f"scale (N/bridge_unit): {scale:.12g}")
        print(f"offset (bridge_unit):  {offset:.12g}")
        print(f"R^2: {r2:.6f}")
        return 0

    finally:
        sensor.close()


if __name__ == "__main__":
    raise SystemExit(main())
