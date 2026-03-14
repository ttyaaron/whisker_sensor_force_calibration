#!/usr/bin/env python3
"""
Re-zero an existing load-cell calibration while keeping the current slope/scale unchanged.

This updates only:
  - offset  (new zero raw average)
  - timestamp
and preserves scale from the input calibration.
"""

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from read_phidgetbridge_loadcell import (
    PHIDGET_IMPORT_ERROR,
    PhidgetLoadCell,
    compute_interval_ms,
    load_calibration,
    utc_now_iso,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Keep calibration scale unchanged and redefine offset using a new zero point."
    )
    p.add_argument("--cal-in", default="calibration.json", help="Input calibration JSON")
    p.add_argument(
        "--cal-out",
        default=None,
        help="Output calibration JSON (default: overwrite --cal-in)",
    )
    p.add_argument("--channel", type=int, default=None, choices=[0, 1, 2, 3], help="Override channel")
    p.add_argument("--serial", type=int, default=None, help="Optional serial number")
    p.add_argument(
        "--gain",
        type=int,
        default=None,
        choices=[1, 8, 16, 32, 64, 128],
        help="Override gain (default: from calibration or 128)",
    )
    p.add_argument("--rate", type=float, default=200.0, help="Target sample rate in Hz")
    p.add_argument("--interval-ms", type=float, default=None, help="Data interval ms (overrides --rate)")
    p.add_argument(
        "--capture-window",
        type=float,
        default=2.0,
        help="Zero-point averaging window in seconds",
    )
    p.add_argument(
        "--attach-timeout",
        type=float,
        default=5.0,
        help="Seconds per attach attempt before retrying",
    )
    p.add_argument("--notes", default="", help="Optional note appended to calibration notes")
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

    cal_in = Path(args.cal_in)
    if not cal_in.is_file():
        print(f"Calibration file not found: {cal_in}", file=sys.stderr)
        return 2

    with cal_in.open("r", encoding="utf-8") as f:
        cal_obj = json.load(f)

    base_cal = load_calibration(str(cal_in))

    channel = args.channel if args.channel is not None else int(cal_obj.get("channel", 0))
    gain = args.gain if args.gain is not None else int(cal_obj.get("gain", 128))
    interval_ms = compute_interval_ms(args.rate, args.interval_ms)

    stop_event = threading.Event()

    def _handle_signal(_sig, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    sensor = PhidgetLoadCell(
        channel=channel,
        interval_ms=interval_ms,
        gain=gain,
        avg_window=1,
        serial_number=args.serial,
        calibration=None,
        stop_event=stop_event,
    )

    try:
        sensor.open_with_retry(args.attach_timeout)
        print("\nRe-zero calibration")
        print(f"- Input calibration: {cal_in}")
        print(f"- Keep scale unchanged: {base_cal.scale:.12g}")
        print(f"- Previous offset:      {base_cal.offset:.12g}")
        print(f"- Channel: {channel}, gain: {gain}")
        print(f"- Capture window: {args.capture_window:.2f}s")

        input("\nRemove all load (0 g), keep stable, then press Enter to capture zero... ")
        new_offset, samples = sensor.get_average_raw(args.capture_window)

        out_path = Path(args.cal_out) if args.cal_out else cal_in

        previous_offset = float(cal_obj.get("offset", base_cal.offset))
        cal_obj["offset"] = float(new_offset)
        cal_obj["scale"] = float(base_cal.scale)  # explicitly preserve slope
        cal_obj["timestamp"] = utc_now_iso()
        cal_obj["channel"] = channel
        cal_obj["gain"] = gain
        if sensor.actual_interval_ms is not None:
            cal_obj["interval_ms"] = int(sensor.actual_interval_ms)

        existing_notes = str(cal_obj.get("notes", "")).strip()
        if args.notes.strip():
            combined = f"{existing_notes} | {args.notes.strip()}" if existing_notes else args.notes.strip()
            cal_obj["notes"] = combined

        cal_obj["rezero"] = {
            "previous_offset": previous_offset,
            "new_offset": float(new_offset),
            "samples": int(samples),
            "capture_window_s": float(args.capture_window),
            "kept_scale": float(base_cal.scale),
            "timestamp": utc_now_iso(),
        }

        with out_path.open("w", encoding="utf-8") as f:
            json.dump(cal_obj, f, indent=2)
            f.write("\n")

        print("\nRe-zero complete")
        print(f"Saved: {out_path}")
        print(f"Scale (unchanged): {base_cal.scale:.12g}")
        print(f"Offset old -> new: {previous_offset:.12g} -> {new_offset:.12g}")
        return 0

    finally:
        sensor.close()


if __name__ == "__main__":
    raise SystemExit(main())
