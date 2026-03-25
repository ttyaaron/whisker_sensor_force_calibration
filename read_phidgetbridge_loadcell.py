#!/usr/bin/env python3
"""
Read/load-test a PhidgetBridge (1046_1) load cell channel using Phidget22.

Features:
- Auto-connect with attach/detach callbacks and retry loop.
- CSV streaming: t_host, bridge_value, force_N.
- Startup tare (--tare) and live tare hotkey ('t').
- Interactive multi-point calibration (--calibrate).
- Optional bridge gain and moving average force smoothing.
- Optional built-in live plotting (--plot).
"""

import argparse
import csv
import json
import math
import select
import signal
import sys
import termios
import threading
import time
import tty
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Deque, Dict, List, Optional, Tuple

try:
    from Phidget22.Devices.VoltageRatioInput import VoltageRatioInput
    try:
        from Phidget22.Devices.VoltageRatioInput import BridgeGain
    except Exception:
        BridgeGain = None
    from Phidget22.PhidgetException import PhidgetException
except Exception as exc:  # pragma: no cover - runtime dependency
    VoltageRatioInput = None
    BridgeGain = None

    class PhidgetException(Exception):
        """Fallback so type checks remain valid when Phidget22 is missing."""

    PHIDGET_IMPORT_ERROR = exc
else:
    PHIDGET_IMPORT_ERROR = None


GRAM_TO_NEWTON = 0.00980665
VALID_GAINS = (1, 8, 16, 32, 64, 128)


@dataclass
class Calibration:
    offset: float
    scale: float
    timestamp: str = ""
    channel: Optional[int] = None
    notes: str = ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_phidget_error(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    details = getattr(exc, "details", None)
    if code is not None and details:
        return f"code={code}, details={details}"
    return str(exc)


def parse_mass_list(text: Optional[str]) -> List[float]:
    if not text:
        return []
    values = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        values.append(float(token))
    return values


def fit_line(xs: List[float], ys: List[float]) -> Tuple[float, float, float]:
    n = len(xs)
    if n < 2:
        raise ValueError("Need at least two calibration points.")

    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-18:
        raise ValueError("Calibration points are degenerate; cannot fit line.")

    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n

    y_mean = sy / n
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0
    return slope, intercept, r2


class StdinKeyReader:
    def __init__(self) -> None:
        self.enabled = sys.stdin.isatty()
        self.fd: Optional[int] = None
        self._old_termios = None

    def __enter__(self) -> "StdinKeyReader":
        if self.enabled:
            self.fd = sys.stdin.fileno()
            self._old_termios = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.enabled and self.fd is not None and self._old_termios is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._old_termios)

    def poll(self) -> Optional[str]:
        if not self.enabled:
            return None
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if not ready:
            return None
        return sys.stdin.read(1)


class LivePlotter:
    def __init__(self, enabled: bool, window_s: float, fps: float, plot_raw: bool) -> None:
        self.enabled = enabled
        self.window_s = max(1.0, float(window_s))
        self.plot_raw = bool(plot_raw)
        self.closed = False
        self.last_refresh_t = 0.0
        self.min_refresh_period = 1.0 / max(1.0, float(fps))

        self.t_rel: Deque[float] = deque()
        self.force_vals: Deque[float] = deque()
        self.raw_vals: Deque[float] = deque()
        self.t0: Optional[float] = None

        self.plt = None
        self.fig = None
        self.ax_force = None
        self.ax_raw = None
        self.force_line = None
        self.raw_line = None

        if not self.enabled:
            return

        try:
            import matplotlib.pyplot as plt  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Plotting requested but matplotlib is not available. Install with: "
                "python3 -m pip install matplotlib"
            ) from exc

        self.plt = plt
        self.plt.ion()
        self.fig, self.ax_force = self.plt.subplots(figsize=(9.0, 4.8))
        (self.force_line,) = self.ax_force.plot([], [], lw=2.0, color="#1f77b4", label="Force (N)")
        self.ax_force.set_title("PhidgetBridge Load Cell Live Signal")
        self.ax_force.set_xlabel("Time (s)")
        self.ax_force.set_ylabel("Force (N)")
        self.ax_force.grid(True, alpha=0.25)

        if self.plot_raw:
            self.ax_raw = self.ax_force.twinx()
            (self.raw_line,) = self.ax_raw.plot([], [], lw=1.2, color="#d62728", alpha=0.85, label="Raw")
            self.ax_raw.set_ylabel("Bridge Value (raw)")

        lines = [self.force_line]
        labels = ["Force (N)"]
        if self.raw_line is not None:
            lines.append(self.raw_line)
            labels.append("Bridge Value (raw)")
        self.ax_force.legend(lines, labels, loc="upper left")
        self.fig.tight_layout()
        self.fig.canvas.mpl_connect("close_event", self._on_close)

    def _on_close(self, _event) -> None:
        self.closed = True

    def add_sample(self, t_host: float, raw: float, force_n: float) -> None:
        if not self.enabled:
            return

        if self.t0 is None:
            self.t0 = t_host
        t_rel = t_host - self.t0

        self.t_rel.append(t_rel)
        self.force_vals.append(force_n)
        self.raw_vals.append(raw)
        self._trim(t_rel)

    def _trim(self, t_now_rel: float) -> None:
        cutoff = max(0.0, t_now_rel - self.window_s)
        while self.t_rel and self.t_rel[0] < cutoff:
            self.t_rel.popleft()
            self.force_vals.popleft()
            self.raw_vals.popleft()

    def refresh(self) -> None:
        if not self.enabled or self.closed:
            return

        now = time.perf_counter()
        if now - self.last_refresh_t < self.min_refresh_period:
            return
        self.last_refresh_t = now

        if not self.t_rel:
            self.plt.pause(0.001)
            return

        xs = list(self.t_rel)
        ys_force = list(self.force_vals)
        self.force_line.set_data(xs, ys_force)

        x_max = xs[-1]
        x_min = max(0.0, x_max - self.window_s)
        self.ax_force.set_xlim(x_min, x_max if x_max > x_min else x_min + self.window_s)

        self.ax_force.relim()
        self.ax_force.autoscale_view(scalex=False, scaley=True)

        if self.raw_line is not None and self.ax_raw is not None:
            ys_raw = list(self.raw_vals)
            self.raw_line.set_data(xs, ys_raw)
            self.ax_raw.relim()
            self.ax_raw.autoscale_view(scalex=False, scaley=True)

        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()
        self.plt.pause(0.001)

    def close(self) -> None:
        if not self.enabled:
            return
        try:
            self.plt.ioff()
            if self.fig is not None:
                self.plt.close(self.fig)
        except Exception:
            pass


def gain_to_enum(gain: int):
    if BridgeGain is None:
        return gain

    candidates = (
        f"BRIDGE_GAIN_{gain}",
        f"PHIDBRIDGE_GAIN_{gain}",
    )
    for name in candidates:
        if hasattr(BridgeGain, name):
            return getattr(BridgeGain, name)
    return gain


class PhidgetLoadCell:
    def __init__(
        self,
        channel: int,
        interval_ms: int,
        gain: int,
        avg_window: int,
        serial_number: Optional[int],
        calibration: Optional[Calibration],
        stop_event: threading.Event,
    ) -> None:
        if VoltageRatioInput is None:
            raise RuntimeError(
                "Phidget22 is not installed. Install with: python3 -m pip install phidget22"
            )

        self.channel = channel
        self.interval_ms = int(interval_ms)
        self.gain = gain
        self.avg_window = max(1, int(avg_window))
        self.stop_event = stop_event

        self.scale = calibration.scale if calibration else None
        self.zero_offset = calibration.offset if calibration else 0.0

        self.device = VoltageRatioInput()
        self.device.setChannel(channel)
        if serial_number is not None:
            self.device.setDeviceSerialNumber(serial_number)

        self.lock = threading.Lock()
        self.attached = False
        self.actual_interval_ms = None

        self.latest_raw = float("nan")
        self.latest_force = float("nan")
        self.latest_ts = float("nan")

        self.samples: Deque[Tuple[float, float]] = deque(maxlen=50000)
        self.avg_buf: Deque[float] = deque(maxlen=self.avg_window)

        self.sample_counter = 0
        self.dropped_records = 0
        self.records: Queue[Tuple[float, float, float]] = Queue(maxsize=200000)

        self.device.setOnAttachHandler(self._on_attach)
        self.device.setOnDetachHandler(self._on_detach)
        self.device.setOnErrorHandler(self._on_error)
        self.device.setOnVoltageRatioChangeHandler(self._on_voltage_ratio_change)

    def open_with_retry(self, attach_timeout_s: float = 5.0) -> None:
        timeout_ms = max(100, int(attach_timeout_s * 1000))
        warned = False

        while not self.stop_event.is_set():
            try:
                self.device.openWaitForAttachment(timeout_ms)
                return
            except PhidgetException as exc:
                if not warned:
                    print(
                        f"[wait] PhidgetBridge channel {self.channel} not attached yet "
                        f"({format_phidget_error(exc)})."
                    )
                    print(
                        "[wait] Check USB cable, channel number, and Linux USB permissions (udev). "
                        "Retrying..."
                    )
                    warned = True
                time.sleep(0.2)

        raise RuntimeError("Stopped before Phidget attached.")

    def close(self) -> None:
        try:
            self.device.close()
        except Exception:
            pass

    def _on_attach(self, ph) -> None:
        self.attached = True
        serial = "?"
        try:
            serial = ph.getDeviceSerialNumber()
        except Exception:
            pass

        configured = []

        if hasattr(ph, "setBridgeEnabled"):
            try:
                ph.setBridgeEnabled(True)
                configured.append("bridge_enabled=True")
            except Exception:
                pass

        if hasattr(ph, "setVoltageRatioChangeTrigger"):
            try:
                ph.setVoltageRatioChangeTrigger(0.0)
                configured.append("trigger=0")
            except Exception:
                pass

        if hasattr(ph, "setBridgeGain"):
            try:
                ph.setBridgeGain(gain_to_enum(self.gain))
                configured.append(f"gain={self.gain}x")
            except Exception as exc:
                print(f"[warn] Failed to set gain={self.gain}x: {format_phidget_error(exc)}")

        actual_interval = self.interval_ms
        if hasattr(ph, "setDataInterval"):
            try:
                min_i = ph.getMinDataInterval()
                max_i = ph.getMaxDataInterval()
                requested = self.interval_ms
                clamped = max(min_i, min(max_i, requested))
                ph.setDataInterval(int(clamped))
                actual_interval = int(ph.getDataInterval())
                if requested != actual_interval:
                    print(
                        f"[info] Requested interval {requested} ms, using {actual_interval} ms "
                        f"(device min/max {min_i}/{max_i} ms)."
                    )
                configured.append(f"interval={actual_interval}ms")
            except Exception as exc:
                print(f"[warn] Failed to set data interval: {format_phidget_error(exc)}")

        self.actual_interval_ms = actual_interval
        print(
            f"[attach] Channel {self.channel} attached (serial {serial}). "
            f"Settings: {', '.join(configured) if configured else 'default'}"
        )

    def _on_detach(self, _ph) -> None:
        self.attached = False
        print("\n[detach] Phidget channel detached. Waiting for reattach...")

    def _on_error(self, _ph, code, description) -> None:
        print(f"\n[phidget-error] code={code} description={description}")

    def _on_voltage_ratio_change(self, _ph, voltage_ratio: float) -> None:
        t_host = time.perf_counter()

        with self.lock:
            self.samples.append((t_host, float(voltage_ratio)))
            self.avg_buf.append(float(voltage_ratio))
            filtered = sum(self.avg_buf) / len(self.avg_buf)

            if self.scale is not None:
                force_n = (filtered - self.zero_offset) * self.scale
            else:
                force_n = float("nan")

            self.latest_raw = float(voltage_ratio)
            self.latest_force = force_n
            self.latest_ts = t_host
            self.sample_counter += 1

        try:
            self.records.put_nowait((t_host, float(voltage_ratio), force_n))
        except Full:
            self.dropped_records += 1

    def tare(self, window_s: float) -> Tuple[float, int]:
        avg_raw, n = self.get_average_raw(window_s)
        with self.lock:
            self.zero_offset = avg_raw
        return avg_raw, n

    def get_average_raw(self, window_s: float, timeout_s: Optional[float] = None) -> Tuple[float, int]:
        if timeout_s is None:
            timeout_s = max(5.0, window_s + 3.0)

        deadline = time.perf_counter() + timeout_s
        min_samples = max(5, int(window_s * 20))

        while not self.stop_event.is_set():
            now = time.perf_counter()
            cutoff = now - window_s
            with self.lock:
                vals = [v for (t, v) in self.samples if t >= cutoff]

            if len(vals) >= min_samples:
                return sum(vals) / len(vals), len(vals)

            if now >= deadline:
                raise RuntimeError(
                    f"Timed out waiting for enough samples ({len(vals)}/{min_samples}) for averaging."
                )
            time.sleep(0.02)

        raise RuntimeError("Stopped before average could be computed.")

    def get_status(self) -> Dict[str, float]:
        with self.lock:
            return {
                "attached": self.attached,
                "latest_raw": self.latest_raw,
                "latest_force": self.latest_force,
                "latest_ts": self.latest_ts,
                "sample_counter": self.sample_counter,
                "zero_offset": self.zero_offset,
            }


def load_calibration(path: str) -> Calibration:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "offset" not in data or "scale" not in data:
        raise ValueError(f"Calibration file missing required keys 'offset'/'scale': {path}")

    return Calibration(
        offset=float(data["offset"]),
        scale=float(data["scale"]),
        timestamp=str(data.get("timestamp", "")),
        channel=data.get("channel"),
        notes=str(data.get("notes", "")),
    )


def save_calibration(path: str, payload: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def run_calibration(args, stop_event: threading.Event) -> int:
    interval_ms = compute_interval_ms(args.rate, args.interval_ms)
    sensor = PhidgetLoadCell(
        channel=args.channel,
        interval_ms=interval_ms,
        gain=args.gain,
        avg_window=max(1, args.avg_window),
        serial_number=args.serial,
        calibration=None,
        stop_event=stop_event,
    )

    try:
        sensor.open_with_retry(args.attach_timeout)

        print("\nCalibration mode")
        print("- Enter known masses in grams one by one.")
        print("- Press Enter on a blank line when finished.")
        print(f"- Capture window per point: {args.capture_window:.2f} s")

        preset_masses = parse_mass_list(args.masses_g)
        points: List[Dict[str, float]] = []

        if preset_masses:
            mass_inputs = [str(x) for x in preset_masses]
            print(f"Using --masses-g: {preset_masses}")
        else:
            mass_inputs = []

        while not stop_event.is_set():
            if mass_inputs:
                mass_text = mass_inputs.pop(0)
                print(f"\nMass: {mass_text} g")
            else:
                mass_text = input("\nEnter mass in grams (blank to finish): ").strip()

            if mass_text == "":
                break

            try:
                mass_g = float(mass_text)
            except ValueError:
                print("[warn] Invalid number. Try again.")
                continue

            input(f"Place {mass_g:g} g and press Enter to capture...")
            avg_raw, n = sensor.get_average_raw(args.capture_window)
            force_n = mass_g * GRAM_TO_NEWTON

            points.append(
                {
                    "mass_g": mass_g,
                    "force_N": force_n,
                    "bridge_value": avg_raw,
                    "samples": n,
                }
            )
            print(
                f"[captured] mass={mass_g:g} g force={force_n:.6f} N "
                f"raw_avg={avg_raw:.12g} samples={n}"
            )

        if len(points) < 2:
            print("[error] Need at least 2 calibration points.")
            return 2

        xs = [p["bridge_value"] for p in points]
        ys = [p["force_N"] for p in points]
        slope, intercept, r2 = fit_line(xs, ys)
        if abs(slope) < 1e-18:
            print("[error] Fitted slope is near zero; cannot compute scale/offset.")
            return 2

        scale = slope
        offset = -intercept / slope

        payload = {
            "offset": offset,
            "scale": scale,
            "timestamp": utc_now_iso(),
            "channel": args.channel,
            "notes": args.notes or "",
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

        out_path = args.out or "calibration.json"
        save_calibration(out_path, payload)

        print("\nCalibration complete")
        print(f"Saved: {out_path}")
        print(f"scale (N/bridge_unit): {scale:.12g}")
        print(f"offset (bridge_unit):  {offset:.12g}")
        print(f"R^2: {r2:.6f}")
        return 0

    finally:
        sensor.close()


def run_stream(args, stop_event: threading.Event) -> int:
    cal: Optional[Calibration] = None
    cal_path = args.cal
    auto_cal = False
    if not cal_path:
        default_cal = Path("calibration.json")
        if default_cal.is_file():
            cal_path = str(default_cal)
            auto_cal = True

    if cal_path:
        cal = load_calibration(cal_path)
        print(
            f"Loaded calibration from {cal_path}: "
            f"offset={cal.offset:.12g}, scale={cal.scale:.12g}"
        )
        if auto_cal:
            print("[info] Auto-applied local calibration.json (override with --cal <path>).")
        if cal.channel is not None and int(cal.channel) != int(args.channel):
            print(
                f"[warn] Calibration channel ({cal.channel}) does not match --channel ({args.channel})."
            )

    if args.force_only and cal is None:
        raise RuntimeError(
            "Force-only mode requires calibration. Provide --cal or place calibration.json in this folder."
        )

    interval_ms = compute_interval_ms(args.rate, args.interval_ms)
    plot_raw_enabled = bool(args.plot_raw and not args.force_only)
    if args.plot_raw and args.force_only:
        print("[info] --force-only is set, so raw overlay is disabled.")

    sensor = PhidgetLoadCell(
        channel=args.channel,
        interval_ms=interval_ms,
        gain=args.gain,
        avg_window=max(1, args.avg_window),
        serial_number=args.serial,
        calibration=cal,
        stop_event=stop_event,
    )

    out_path = args.out or "force.csv"
    plotter = LivePlotter(
        enabled=args.plot,
        window_s=args.plot_window,
        fps=args.plot_fps,
        plot_raw=plot_raw_enabled,
    )

    csv_file = None
    try:
        sensor.open_with_retry(args.attach_timeout)

        if args.tare:
            print(f"Running startup tare over {args.tare_window:.2f}s...")
            avg_raw, n = sensor.tare(args.tare_window)
            print(f"[tare] zero_offset={avg_raw:.12g} ({n} samples)")

        try:
            csv_file = open(out_path, "w", newline="", encoding="utf-8")
        except PermissionError as exc:
            if args.out:
                raise RuntimeError(
                    f"Cannot write output CSV: {out_path}. "
                    "Check file/folder permissions or choose another path with --out."
                ) from exc
            fallback_path = f"force_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            print(
                f"[warn] Cannot write default CSV '{out_path}' (permission denied). "
                f"Using '{fallback_path}' instead."
            )
            out_path = fallback_path
            csv_file = open(out_path, "w", newline="", encoding="utf-8")
        writer = csv.writer(csv_file)
        if args.force_only:
            writer.writerow(["t_host", "force_N"])
        else:
            writer.writerow(["t_host", "bridge_value", "force_N"])
        csv_file.flush()

        print("\nStreaming started")
        print(f"Writing CSV: {out_path}")
        print("Hotkeys: 't' = tare, 'q' = quit, Ctrl+C = quit")
        if args.plot:
            print(
                f"Live plot enabled (window={args.plot_window:.1f}s, "
                f"fps={args.plot_fps:.1f}, raw_overlay={plot_raw_enabled})"
            )
            if cal is None:
                print("[info] No calibration loaded: force_N will be NaN. Use --cal for force plot.")

        last_status_t = time.perf_counter()
        last_flush_t = last_status_t
        last_counter = 0

        with StdinKeyReader() as key_reader:
            while not stop_event.is_set():
                wrote = 0
                while True:
                    try:
                        t_host, raw, force_n = sensor.records.get_nowait()
                    except Empty:
                        break

                    force_field = "nan" if math.isnan(force_n) else f"{force_n:.12g}"
                    if args.force_only:
                        writer.writerow([f"{t_host:.9f}", force_field])
                    else:
                        writer.writerow([f"{t_host:.9f}", f"{raw:.12g}", force_field])
                    plotter.add_sample(t_host, raw, force_n)
                    wrote += 1

                now = time.perf_counter()
                plotter.refresh()
                if plotter.closed:
                    stop_event.set()

                key = key_reader.poll()
                if key:
                    k = key.lower()
                    if k == "t":
                        try:
                            print(f"\nTare requested ({args.tare_window:.2f}s window)...")
                            avg_raw, n = sensor.tare(args.tare_window)
                            print(f"[tare] zero_offset={avg_raw:.12g} ({n} samples)")
                        except Exception as exc:
                            print(f"[warn] Tare failed: {exc}")
                    elif k == "q":
                        stop_event.set()

                if now - last_status_t >= 1.0:
                    status = sensor.get_status()
                    counter = int(status["sample_counter"])
                    dt = now - last_status_t
                    sr = (counter - last_counter) / dt if dt > 0 else float("nan")
                    last_counter = counter
                    last_status_t = now

                    raw = status["latest_raw"]
                    force = status["latest_force"]
                    attached_flag = "A" if status["attached"] else "D"
                    force_txt = "nan" if math.isnan(force) else f"{force:.6f}"
                    raw_txt = "nan" if math.isnan(raw) else f"{raw:.9f}"
                    interval_txt = (
                        "?" if sensor.actual_interval_ms is None else str(sensor.actual_interval_ms)
                    )
                    if args.force_only:
                        line = (
                            f"[{attached_flag}] force_N={force_txt}  "
                            f"rate={sr:7.1f} Hz  interval={interval_txt} ms  "
                            f"dropped={sensor.dropped_records}"
                        )
                    else:
                        line = (
                            f"[{attached_flag}] raw={raw_txt}  force_N={force_txt}  "
                            f"rate={sr:7.1f} Hz  interval={interval_txt} ms  "
                            f"dropped={sensor.dropped_records}"
                        )
                    print(f"\r{line:<120}", end="", flush=True)

                if wrote or (now - last_flush_t >= 1.0):
                    csv_file.flush()
                    last_flush_t = now

                time.sleep(0.005)

        print("\nStopping stream...")
        return 0

    finally:
        if csv_file is not None and not csv_file.closed:
            csv_file.flush()
            csv_file.close()
        plotter.close()
        sensor.close()


def compute_interval_ms(rate_hz: float, interval_ms: Optional[float]) -> int:
    if interval_ms is not None:
        return max(1, int(round(interval_ms)))
    if rate_hz <= 0:
        raise ValueError("--rate must be > 0")
    return max(1, int(round(1000.0 / rate_hz)))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PhidgetBridge (1046_1) load cell stream/calibration tool"
    )

    parser.add_argument("--channel", type=int, default=0, choices=[0, 1, 2, 3], help="Bridge channel (0-3)")
    parser.add_argument("--serial", type=int, default=None, help="Optional device serial number")

    parser.add_argument("--rate", type=float, default=200.0, help="Target sample rate in Hz (default: 200)")
    parser.add_argument(
        "--interval-ms",
        type=float,
        default=None,
        help="Data interval in ms (overrides --rate). Example: 5 for 200Hz",
    )
    parser.add_argument("--gain", type=int, default=128, choices=VALID_GAINS, help="Bridge gain")
    parser.add_argument(
        "--avg-window",
        type=int,
        default=1,
        help="Moving average window (samples) used for force stability",
    )
    parser.add_argument(
        "--attach-timeout",
        type=float,
        default=5.0,
        help="Seconds per attachment attempt before retrying",
    )
    parser.add_argument("--plot", action="store_true", help="Show live plot while streaming")
    parser.add_argument(
        "--plot-window",
        type=float,
        default=20.0,
        help="Live plot rolling window in seconds",
    )
    parser.add_argument(
        "--plot-fps",
        type=float,
        default=20.0,
        help="Live plot refresh rate",
    )
    parser.add_argument(
        "--plot-raw",
        action="store_true",
        help="Overlay raw bridge value on a secondary axis in live plot",
    )
    parser.add_argument(
        "--force-only",
        action="store_true",
        help="Calibrated-only output: force plot/status and CSV without bridge_value",
    )

    parser.add_argument("--out", default=None, help="Output path (CSV for stream, JSON for --calibrate)")

    parser.add_argument("--cal", default=None, help="Calibration JSON to load for force conversion")
    parser.add_argument("--tare", action="store_true", help="Run tare at startup")
    parser.add_argument(
        "--tare-window",
        type=float,
        default=1.5,
        help="Seconds to average when taring",
    )

    parser.add_argument("--calibrate", action="store_true", help="Interactive calibration mode")
    parser.add_argument(
        "--capture-window",
        type=float,
        default=1.5,
        help="Seconds to average for each calibration point",
    )
    parser.add_argument(
        "--masses-g",
        default=None,
        help="Optional comma-separated masses in grams for calibration (e.g. 0,5,10,20)",
    )
    parser.add_argument("--notes", default="", help="Optional notes saved into calibration JSON")

    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if PHIDGET_IMPORT_ERROR is not None:
        print(
            "Phidget22 import failed. Install dependencies first:\n"
            "  sudo apt install libusb-1.0-0\n"
            "  python3 -m pip install phidget22\n"
            f"Import error: {PHIDGET_IMPORT_ERROR}",
            file=sys.stderr,
        )
        return 2

    stop_event = threading.Event()

    def _handle_signal(_sig, _frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        if args.calibrate:
            return run_calibration(args, stop_event)
        return run_stream(args, stop_event)
    except KeyboardInterrupt:
        stop_event.set()
        return 130
    except Exception as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
