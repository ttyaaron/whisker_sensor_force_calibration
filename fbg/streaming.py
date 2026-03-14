from __future__ import annotations

import threading
import time
from collections import deque
from itertools import islice
from typing import Dict, List, Tuple

import numpy as np

from .config import InterrogatorSettings
from .interrogator import Interrogator


class FBGStreamReader(threading.Thread):
    """Background reader that streams wavelength data from the interrogator."""

    def __init__(
        self,
        interr_cfg: InterrogatorSettings,
        history_seconds: float,
    ) -> None:
        super().__init__(daemon=True)
        self._interr_cfg = interr_cfg
        self._history_seconds = history_seconds

        self.interrogator: Interrogator | None = None
        self.sensor_names: List[str] = [sensor.name for sensor in interr_cfg.sensors]
        self.nominal_wavelengths: Dict[str, float] = {
            sensor.name: sensor.nominal_wavelength for sensor in interr_cfg.sensors
        }

        self._estimated_rate = 2000.0 / max(1.0, float(interr_cfg.data_interleave or 1))
        # CRITICAL: Buffer size must account for bursty acquisition pattern
        # Hardware delivers bursts at ~53 kHz with gaps, averaging ~1800 Hz
        # To hold all burst samples without loss, size buffer for peak rate
        # Use 3× safety margin: 2000 Hz × 3 × history_seconds
        history_size = max(1, int(self._estimated_rate * history_seconds * 3.0))
        self._history_samples = history_size
        self._history: Dict[str, deque[float]] = {
            name: deque(maxlen=self._history_samples) for name in self.sensor_names
        }
        self._timestamps: deque[float] = deque(maxlen=self._history_samples)
        self._lock = threading.Lock()

        self._stop_event = threading.Event()
        self._ready_event = threading.Event()

        self._recording = False
        self._recorded_rows: List[List[float]] = []
        self._start_time: float | None = None
        self._last_cycle_time: float = 0.0
        self.error_count = 0
        self.error: str | None = None

    @property
    def sample_rate(self) -> float:
        if self.interrogator and self.interrogator.sample_rate:
            return self.interrogator.sample_rate
        return self._estimated_rate

    @property
    def is_ready(self) -> bool:
        return self._ready_event.is_set()

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        return self._ready_event.wait(timeout=timeout)

    def run(self) -> None:
        # Set high priority for FBG acquisition thread to avoid throttling
        # during heavy system load (tracking, vision processing, etc.)
        try:
            import os
            # Try to set nice level (lower = higher priority, -20 is highest)
            # This may require sudo, but worth trying
            try:
                os.nice(-10)  # Increase priority
            except PermissionError:
                pass  # Not critical, just means we can't boost priority
        except Exception:
            pass
        
        try:
            self._open_connection()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self._shutdown_connection()
            return

        try:
            self._stream_loop()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._shutdown_connection()

    def stop(self) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=2.0)

    def start_recording(self) -> None:
        with self._lock:
            self._recording = True
            self._recorded_rows = []

    def stop_recording(self) -> List[List[float]]:
        with self._lock:
            self._recording = False
            rows = list(self._recorded_rows)
            self._recorded_rows = []
        return rows

    def latest_sample(self) -> Tuple[float, Dict[str, float]]:
        with self._lock:
            if not self._timestamps:
                return float("nan"), {}
            timestamp = float(self._timestamps[-1])
            latest = {
                name: (float(values[-1]) if values else float("nan"))
                for name, values in self._history.items()
            }
        return timestamp, latest

    def snapshot(self, max_points: int | None = None) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
        with self._lock:
            total = len(self._timestamps)
            if max_points is not None and max_points > 0 and total > int(max_points):
                start = total - int(max_points)
            else:
                start = 0

            count = total - start
            if count <= 0:
                return np.array([]), {name: np.array([], dtype=np.float64) for name in self.sensor_names}

            ts_iter = self._timestamps if start == 0 else islice(self._timestamps, start, total)
            timestamps = np.fromiter(ts_iter, dtype=np.float64, count=count)

            series: Dict[str, np.ndarray] = {}
            for name in self.sensor_names:
                values = self._history.get(name)
                if values is None or len(values) == 0:
                    series[name] = np.full(count, np.nan, dtype=np.float64)
                    continue

                n_values = len(values)
                take = min(count, n_values)
                v_start = n_values - take
                v_iter = values if v_start == 0 else islice(values, v_start, n_values)
                arr = np.fromiter(v_iter, dtype=np.float64, count=take)
                if take < count:
                    padded = np.full(count, np.nan, dtype=np.float64)
                    padded[-take:] = arr
                    arr = padded
                series[name] = arr
        
        # Diagnostic: print actual acquisition rate periodically
        if len(timestamps) > 10 and hasattr(self, '_last_diagnostic_time'):
            now = time.perf_counter()
            if now - self._last_diagnostic_time > 5.0:  # Every 5 seconds
                if len(timestamps) >= 2:
                    actual_rate = len(timestamps) / (timestamps[-1] - timestamps[0]) if timestamps[-1] > timestamps[0] else 0
                    print(f"[FBGStreamReader] Buffer: {len(timestamps)} samples, actual rate: {actual_rate:.1f} Hz")
                self._last_diagnostic_time = now
        elif not hasattr(self, '_last_diagnostic_time'):
            self._last_diagnostic_time = time.perf_counter()
        
        return timestamps, series

    def snapshot_from_recording(self, window_sec: float | None = None) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
        """Get snapshot from recording buffer (_recorded_rows) with optional time window.
        
        This returns data from the same source that gets saved to CSV, ensuring
        consistency between CSV and NPZ files during active recording sessions.
        
        Args:
            window_sec: If specified, return only the last N seconds of data.
                       If None, return all recorded data.
        
        Returns:
            Tuple of (timestamps, series_dict) in same format as snapshot().
        """
        with self._lock:
            if not self._recorded_rows:
                # No recording data available, return empty
                print("[FBGStreamReader] Warning: No recorded data available for snapshot_from_recording()")
                return np.array([]), {name: np.array([]) for name in self.sensor_names}

            
            # Convert _recorded_rows (list of [time, sensor1, sensor2, ...]) to arrays
            # Format: each row is [timestamp, sensor1_value, sensor2_value, ...]
            data_array = np.array(self._recorded_rows, dtype=np.float64)
            timestamps = data_array[:, 0]
            
            # Apply time window if requested
            if window_sec is not None and len(timestamps) > 0:
                t_end = timestamps[-1]
                mask = timestamps >= (t_end - window_sec)
                timestamps = timestamps[mask]
                data_array = data_array[mask]
            
            # Build series dict
            series = {}
            for i, name in enumerate(self.sensor_names):
                # Column index is i+1 because column 0 is timestamp
                series[name] = data_array[:, i + 1]
        
        return timestamps, series

    def _open_connection(self) -> None:
        properties = self._interr_cfg.to_fbg_properties()
        self.interrogator = Interrogator(
            self._interr_cfg.ip_address,
            self._interr_cfg.port,
            properties if properties else None,
        )
        self.interrogator.connect()

        self.interrogator.data_interleave = self._interr_cfg.data_interleave
        self.interrogator.num_averages = self._interr_cfg.num_averages

        for idx, gain in enumerate(self._interr_cfg.ch_gains, start=1):
            self.interrogator.set_channel_gain(idx, gain)
        for idx, thres in enumerate(self._interr_cfg.ch_noise_thresholds, start=1):
            self.interrogator.set_channel_noise_threshold(idx, thres)

        self.interrogator.set_trigger_defaults(False)
        self.interrogator.zero_strain_sensors()
        self.interrogator.setup_streaming(True)
        self.interrogator.setup_append_data()
        # Clear residual interrogator backlog at start of a fresh stream.
        try:
            self.interrogator.flush_buffer(receive=False)
        except Exception:
            pass

        if self.interrogator.sensors:
            self.sensor_names = [sensor.name for sensor in self.interrogator.sensors]
            for sensor in self.interrogator.sensors:
                if sensor.name not in self.nominal_wavelengths:
                    self.nominal_wavelengths[sensor.name] = sensor.nominal_wavelength or 0.0

        # CRITICAL: Size buffer for bursty acquisition (see comment in __init__)
        # Use 3× safety margin to hold all burst samples without eviction
        history_size = max(
            1,
            int(
                (self.interrogator.sample_rate or self._estimated_rate)
                * self._history_seconds
                * 3.0
            ),
        )
        with self._lock:
            self._history_samples = history_size
            self._history = {
                name: deque(maxlen=self._history_samples) for name in self.sensor_names
            }
            self._timestamps = deque(maxlen=self._history_samples)

        self._start_time = time.perf_counter()
        self._ready_event.set()

    def _stream_loop(self) -> None:
        assert self.interrogator is not None
        # Remove throttling - let the loop run as fast as the hardware allows
        # The interrogator.get_data() call is blocking and will pace the loop naturally
        
        sample_count = 0
        last_diagnostic_time = time.perf_counter()

        while not self._stop_event.is_set():
            loop_start = time.perf_counter()
            
            try:
                self.interrogator.get_data()
            except Exception:
                self.error_count += 1
                continue

            now = time.perf_counter()
            relative_time = now - (self._start_time or now)
            latest_values: Dict[str, float] = {}

            for name in self.sensor_names:
                key = f"{name}_wavelength"
                raw = self.interrogator.data.get(key)
                if raw is None:
                    latest_values[name] = np.nan
                    continue

                # Micron Optics driver returns length-1 numpy arrays; avoid copying by indexing directly.
                if isinstance(raw, (list, tuple)) and raw:
                    latest_values[name] = float(raw[0])
                else:
                    arr = np.asarray(raw)
                    latest_values[name] = float(arr.flat[0]) if arr.size else np.nan

            with self._lock:
                self._timestamps.append(relative_time)
                for name, value in latest_values.items():
                    if name not in self._history:
                        self._history[name] = deque(maxlen=self._history_samples)
                    self._history[name].append(value)

                if self._recording:
                    row = [relative_time]
                    for name in self.sensor_names:
                        row.append(latest_values.get(name, np.nan))
                    self._recorded_rows.append(row)
            
            sample_count += 1
            
            # Diagnostic: print actual loop rate every 30 seconds (reduced from 10 to minimize overhead)
            if now - last_diagnostic_time > 30.0:
                elapsed = now - last_diagnostic_time
                actual_hz = sample_count / elapsed
                loop_time_ms = (time.perf_counter() - loop_start) * 1000
                print(
                    f"[FBGStreamReader] Loop rate: {actual_hz:.1f} Hz "
                    f"(target: {self._estimated_rate:.1f} Hz), "
                    f"last loop: {loop_time_ms:.2f} ms"
                )
                sample_count = 0
                last_diagnostic_time = now

    def _shutdown_connection(self) -> None:
        if self.interrogator:
            try:
                if getattr(self.interrogator, "sample_rate", None):
                    self._estimated_rate = self.interrogator.sample_rate
                self.interrogator.disconnect()
            finally:
                self.interrogator = None
