#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from typing import Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import serial
import serial.tools.list_ports
from PyQt5 import QtCore, QtWidgets
try:
    import pyqtgraph as pg
except Exception:  # pragma: no cover - optional UI dependency
    pg = None

try:
    import bota_driver
except ImportError as exc:  # pragma: no cover - hardware/env specific
    bota_driver = None
    BOTA_IMPORT_ERROR = exc
else:
    BOTA_IMPORT_ERROR = None

try:
    from read_phidgetbridge_loadcell import (
        compute_interval_ms as phidget_compute_interval_ms,
        load_calibration as load_phidget_calibration,
        PhidgetLoadCell,
    )
except Exception as exc:  # pragma: no cover - optional hardware path
    PhidgetLoadCell = None  # type: ignore[assignment]
    PHIDGET_PANEL_IMPORT_ERROR = exc
else:
    PHIDGET_PANEL_IMPORT_ERROR = None

from fbg.config import DEFAULT_CONFIG, InterrogatorSettings, load_config
from fbg.streaming import FBGStreamReader

ROOT_DIR = Path(__file__).resolve().parent
STAGE_DIR = ROOT_DIR / "stage_control"
if str(STAGE_DIR) not in sys.path:
    sys.path.insert(0, str(STAGE_DIR))

from stage_module import StageModuleControl  # noqa: E402


def detect_linux_network_interface() -> str:
    if os.name == "nt":
        return ""

    net_dir = Path("/sys/class/net")
    if not net_dir.is_dir():
        return ""

    interfaces: List[Tuple[str, str]] = []
    for iface_path in sorted(net_dir.iterdir()):
        iface = iface_path.name
        if iface == "lo":
            continue
        state_file = iface_path / "operstate"
        state = "unknown"
        if state_file.is_file():
            state = state_file.read_text(encoding="utf-8").strip()
        interfaces.append((iface, state))

    up_eth = [iface for iface, state in interfaces if state == "up" and iface.startswith("en")]
    if up_eth:
        return up_eth[0]

    up_any = [iface for iface, state in interfaces if state == "up"]
    if up_any:
        return up_any[0]

    eth_any = [iface for iface, _ in interfaces if iface.startswith("en")]
    if eth_any:
        return eth_any[0]

    return interfaces[0][0] if interfaces else ""


def list_linux_network_interfaces() -> List[str]:
    if os.name == "nt":
        return []

    net_dir = Path("/sys/class/net")
    if not net_dir.is_dir():
        return []

    interfaces: List[Tuple[str, str]] = []
    for iface_path in sorted(net_dir.iterdir()):
        iface = iface_path.name
        if iface == "lo":
            continue
        state_file = iface_path / "operstate"
        state = "unknown"
        if state_file.is_file():
            state = state_file.read_text(encoding="utf-8").strip()
        interfaces.append((iface, state))

    ordered: List[str] = []

    def _append(items: List[str]) -> None:
        for item in items:
            if item not in ordered:
                ordered.append(item)

    _append([iface for iface, state in interfaces if state == "up" and iface.startswith("en")])
    _append([iface for iface, state in interfaces if state == "up"])
    _append([iface for iface, _ in interfaces if iface.startswith("en")])
    _append([iface for iface, _ in interfaces])
    return ordered


def prepare_bota_config_path(
    config_path: Path,
    network_interface_override: Optional[str] = None,
) -> Tuple[Path, Optional[Path]]:
    if not config_path.is_file():
        raise RuntimeError(f"Bota config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        config_data = json.load(handle)

    driver_config = config_data.get("driver_config", {})
    interface_name = driver_config.get("communication_interface_name", "")
    interface_params = driver_config.get("communication_interface_params", {})
    current_iface = interface_params.get("network_interface", "")
    env_override_iface = os.environ.get("BOTA_NETWORK_INTERFACE", "").strip()
    override_iface = (network_interface_override or env_override_iface).strip()
    override_sensor_ip = os.environ.get("BOTA_SENSOR_IP", "").strip()

    changed = False
    is_ethercat = "EtherCAT" in interface_name

    if override_sensor_ip and "sensor_ip_address" in interface_params:
        interface_params["sensor_ip_address"] = override_sensor_ip
        changed = True

    if override_iface and is_ethercat:
        interface_params["network_interface"] = override_iface
        changed = True
    elif (
        os.name != "nt"
        and is_ethercat
        and isinstance(current_iface, str)
        and current_iface.startswith("\\\\Device\\NPF_")
    ):
        auto_iface = detect_linux_network_interface()
        if auto_iface:
            interface_params["network_interface"] = auto_iface
            changed = True
        else:
            raise RuntimeError(
                "No Linux network interface detected for EtherCAT. "
                "Set BOTA_NETWORK_INTERFACE (e.g. export BOTA_NETWORK_INTERFACE=enp3s0)."
            )

    if not changed:
        return config_path, None

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        encoding="utf-8",
    )
    with temp_file as handle:
        json.dump(config_data, handle, ensure_ascii=False, indent=2)
    runtime_path = Path(temp_file.name)
    return runtime_path, runtime_path


@dataclass
class DisplacementConfig:
    start_x_mm: float = 0.0
    displacement_mm: float = 1.0
    use_z_stack: bool = False
    start_z_mm: float = 0.0
    z_step_mm: float = 5.0
    z_level_count: int = 1
    whisker_name: str = "whisker"
    repeat_count: int = 5
    stage_speed_scale: float = 1.0
    inter_trial_home_wait_s: float = 20.0
    snapshot_avg_window_s: float = 1.0
    pre_wait_s: float = 30.0
    final_wait_s: float = 30.0
    settle_time_s: float = 0.10
    position_tolerance_mm: float = 0.005
    move_poll_interval_s: float = 0.05
    max_move_wait_s: float = 20.0
    x_min_mm: float = 0.0
    x_max_mm: float = 101.6
    z_min_mm: float = 0.0
    z_max_mm: float = 50.8


@dataclass
class DisplacementResult:
    trial_id: str
    trial_index: int
    trial_number_within_z: int
    z_level_index: int
    z_level_total: int
    whisker_name: str
    start_time_iso: str
    end_time_iso: str
    stop_reason: str
    elapsed_s: float
    requested_start_x_mm: float
    requested_displacement_mm: float
    target_end_x_mm: float
    requested_z_mm: float
    initial_x_mm: float
    initial_z_mm: float
    start_x_mm: float
    start_z_mm: float
    end_x_mm: float
    end_z_mm: float
    actual_displacement_mm: float
    start_force_z_n: float
    start_fbg1_nm: float
    end_force_z_n: float
    end_fbg1_nm: float
    trace_csv_path: str
    summary_table_csv_path: str


@dataclass
class DisplacementBatchResult:
    run_id: str
    whisker_name: str
    requested_trials: int
    completed_trials: int
    stop_reason: str
    trial_dir_path: str
    trace_csv_paths: List[str]
    summary_table_csv_path: str
    trials: List[DisplacementResult]


class BotaForceReader(threading.Thread):
    def __init__(
        self,
        config_path: Path,
        *,
        network_interface_override: Optional[str] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._config_path = config_path
        self._network_interface_override = network_interface_override
        self._runtime_config_temp: Optional[Path] = None
        self._driver = None

        self._stop_event = threading.Event()
        self._initialized_event = threading.Event()
        self._lock = threading.Lock()

        self.error: Optional[str] = None
        self._latest_force_xyz = (np.nan, np.nan, np.nan)
        self._latest_temperature = np.nan
        self._latest_timestamp: Optional[float] = None
        self._consecutive_read_failures = 0

    @property
    def is_ready(self) -> bool:
        return self._initialized_event.is_set() and self.error is None

    def wait_until_initialized(self, timeout: Optional[float] = None) -> bool:
        return self._initialized_event.wait(timeout=timeout)

    def wait_for_first_sample(self, timeout: float = 5.0) -> bool:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self.get_latest() is not None:
                return True
            time.sleep(0.01)
        return False

    def get_latest(self) -> Optional[Dict[str, float]]:
        with self._lock:
            if self._latest_timestamp is None:
                return None
            fx, fy, fz = self._latest_force_xyz
            return {
                "timestamp": self._latest_timestamp,
                "fx": float(fx),
                "fy": float(fy),
                "fz": float(fz),
                "temperature": float(self._latest_temperature),
            }

    def stop(self) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=2.0)

    def run(self) -> None:  # pragma: no cover - hardware path
        if bota_driver is None:
            self.error = f"Failed to import bota_driver: {BOTA_IMPORT_ERROR}"
            self._initialized_event.set()
            return

        runtime_cfg = self._config_path
        try:
            runtime_cfg, temp_cfg = prepare_bota_config_path(
                self._config_path,
                network_interface_override=self._network_interface_override,
            )
            self._runtime_config_temp = temp_cfg

            self._driver = bota_driver.BotaDriver(str(runtime_cfg))
            if not self._driver.configure():
                raise RuntimeError("Bota configure() failed")
            if not self._driver.tare():
                raise RuntimeError("Bota tare() failed")
            if not self._driver.activate():
                raise RuntimeError("Bota activate() failed")
            self._initialized_event.set()
            loop_count = 0

            while not self._stop_event.is_set():
                try:
                    frame = self._driver.read_frame()
                except Exception as exc:
                    self._consecutive_read_failures += 1
                    if self._consecutive_read_failures >= 30:
                        self.error = (
                            "Bota communication interrupted (read_frame failures >= 30). "
                            f"Last error: {exc}"
                        )
                        break
                    continue

                force = tuple(float(v) for v in frame.force)
                timestamp = time.perf_counter()
                temp = float(frame.temperature)
                self._consecutive_read_failures = 0
                with self._lock:
                    self._latest_force_xyz = force
                    self._latest_temperature = temp
                    self._latest_timestamp = timestamp
                loop_count += 1
                # Yield the GIL periodically so FBG/network threads stay responsive.
                if (loop_count % 16) == 0:
                    time.sleep(0)
        except Exception as exc:
            self.error = str(exc)
            self._initialized_event.set()
        finally:
            try:
                if self._driver is not None:
                    self._driver.deactivate()
                    self._driver.shutdown()
            except Exception:
                pass

            if self._runtime_config_temp is not None:
                try:
                    self._runtime_config_temp.unlink(missing_ok=True)
                except Exception:
                    pass


class PhidgetForceReader(threading.Thread):
    def __init__(
        self,
        *,
        channel: int,
        rate_hz: float,
        interval_ms: Optional[float],
        gain: int,
        avg_window: int,
        serial_number: Optional[int],
        calibration_path: Path,
        attach_timeout_s: float = 5.0,
    ) -> None:
        super().__init__(daemon=True)
        self.channel = int(channel)
        self.rate_hz = float(rate_hz)
        self.interval_ms = interval_ms
        self.gain = int(gain)
        self.avg_window = max(1, int(avg_window))
        self.serial_number = serial_number
        self.calibration_path = calibration_path
        self.attach_timeout_s = max(0.2, float(attach_timeout_s))

        self._stop_event = threading.Event()
        self._initialized_event = threading.Event()
        self._first_sample_event = threading.Event()
        self._lock = threading.Lock()

        self.error: Optional[str] = None
        self._sensor: Optional[PhidgetLoadCell] = None
        self._latest_force_n = float("nan")
        self._latest_raw = float("nan")
        self._latest_timestamp: Optional[float] = None

    @property
    def is_ready(self) -> bool:
        return self._initialized_event.is_set() and self.error is None

    def wait_until_initialized(self, timeout: Optional[float] = None) -> bool:
        return self._initialized_event.wait(timeout=timeout)

    def wait_for_first_sample(self, timeout: float = 5.0) -> bool:
        return self._first_sample_event.wait(timeout=max(0.1, float(timeout)))

    def get_latest(self) -> Optional[Dict[str, float]]:
        with self._lock:
            if self._latest_timestamp is None:
                return None
            return {
                "timestamp": float(self._latest_timestamp),
                "fz": float(self._latest_force_n),
                "raw": float(self._latest_raw),
            }

    def rezero(self, capture_window_s: float = 1.5) -> Dict[str, float]:
        sensor = self._sensor
        if sensor is None:
            raise RuntimeError("Load-cell is not connected.")

        with sensor.lock:
            previous_offset = float(sensor.zero_offset)
            scale = float(sensor.scale) if sensor.scale is not None else float("nan")

        new_offset, samples = sensor.tare(capture_window_s)

        latest_force = float("nan")
        latest_raw = float("nan")
        latest_timestamp = float("nan")
        with sensor.lock:
            latest_raw = float(sensor.latest_raw)
            latest_timestamp = float(sensor.latest_ts)
            if sensor.scale is not None and np.isfinite(latest_raw):
                latest_force = float((latest_raw - sensor.zero_offset) * sensor.scale)
                sensor.latest_force = latest_force

        # Drop pre-rezero samples so the UI does not briefly show stale force values.
        while True:
            try:
                sensor.records.get_nowait()
            except Empty:
                break

        with self._lock:
            self._latest_raw = latest_raw
            self._latest_force_n = latest_force
            if np.isfinite(latest_timestamp):
                self._latest_timestamp = latest_timestamp

        return {
            "previous_offset": previous_offset,
            "new_offset": float(new_offset),
            "scale": scale,
            "raw": latest_raw,
            "force_n": latest_force,
            "samples": int(samples),
            "capture_window_s": float(capture_window_s),
        }

    def stop(self) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=2.0)

    def run(self) -> None:  # pragma: no cover - hardware path
        if PHIDGET_PANEL_IMPORT_ERROR is not None:
            self.error = f"Failed to import Phidget load-cell helpers: {PHIDGET_PANEL_IMPORT_ERROR}"
            self._initialized_event.set()
            return
        if PhidgetLoadCell is None:
            self.error = "Phidget support is unavailable in this environment."
            self._initialized_event.set()
            return

        try:
            if not self.calibration_path.is_file():
                raise RuntimeError(
                    f"Calibration file not found: {self.calibration_path}. "
                    "Create calibration first, then retry."
                )
            calibration = load_phidget_calibration(str(self.calibration_path))
            interval = phidget_compute_interval_ms(self.rate_hz, self.interval_ms)

            sensor = PhidgetLoadCell(
                channel=self.channel,
                interval_ms=interval,
                gain=self.gain,
                avg_window=self.avg_window,
                serial_number=self.serial_number,
                calibration=calibration,
                stop_event=self._stop_event,
            )
            self._sensor = sensor
            sensor.open_with_retry(self.attach_timeout_s)
            self._initialized_event.set()

            while not self._stop_event.is_set():
                try:
                    t_host, raw, force_n = sensor.records.get(timeout=0.25)
                except Empty:
                    continue

                # Keep only the newest reading to minimize queue lag.
                while True:
                    try:
                        t_host, raw, force_n = sensor.records.get_nowait()
                    except Empty:
                        break

                with self._lock:
                    self._latest_timestamp = float(t_host)
                    self._latest_raw = float(raw)
                    self._latest_force_n = float(force_n)
                self._first_sample_event.set()

        except Exception as exc:
            self.error = str(exc)
            self._initialized_event.set()
        finally:
            try:
                if self._sensor is not None:
                    self._sensor.close()
            except Exception:
                pass


class ExperimentController:
    _STAGE_STEP_SIZE_MM = 0.000047625

    def __init__(
        self,
        *,
        output_dir: Path,
        bota_config_path: Path,
        bota_interface_override: Optional[str],
        fbg_interrogator_cfg: InterrogatorSettings,
        stage_module_id: int = 1,
        z_stage_module_id: int = 3,
        z_total_steps: int = 1066666,
        invert_z_axis: bool = False,
        enable_bota: bool = False,
        enable_loadcell: bool = False,
        loadcell_channel: int = 0,
        loadcell_rate_hz: float = 200.0,
        loadcell_interval_ms: Optional[float] = None,
        loadcell_gain: int = 128,
        loadcell_avg_window: int = 20,
        loadcell_serial: Optional[int] = None,
        loadcell_calibration_path: Optional[Path] = None,
        loadcell_attach_timeout_s: float = 5.0,
    ) -> None:
        self.output_dir = output_dir
        self.bota_config_path = bota_config_path
        self.bota_interface_override = (bota_interface_override or "").strip() or None
        self.fbg_interrogator_cfg = fbg_interrogator_cfg
        self.stage_module_id = int(stage_module_id)
        self.z_stage_module_id = int(z_stage_module_id)
        self.z_total_steps = int(z_total_steps)
        self.invert_z_axis = bool(invert_z_axis)
        self.enable_bota = bool(enable_bota)
        self.enable_loadcell = bool(enable_loadcell)
        self.loadcell_channel = int(loadcell_channel)
        self.loadcell_rate_hz = float(loadcell_rate_hz)
        self.loadcell_interval_ms = loadcell_interval_ms
        self.loadcell_gain = int(loadcell_gain)
        self.loadcell_avg_window = max(1, int(loadcell_avg_window))
        self.loadcell_serial = loadcell_serial
        self.loadcell_calibration_path = (
            Path(loadcell_calibration_path)
            if loadcell_calibration_path is not None
            else ROOT_DIR / "calibration.json"
        )
        self.loadcell_attach_timeout_s = max(0.2, float(loadcell_attach_timeout_s))

        self._stage_serial: Optional[serial.Serial] = None
        self._stage_x: Optional[StageModuleControl] = None
        self._stage_z: Optional[StageModuleControl] = None
        self._stage_lock = threading.Lock()
        self._x_state_lock = threading.Lock()
        self._x_reader_thread: Optional[threading.Thread] = None
        self._x_reader_stop = threading.Event()
        self._latest_x_mm = float("nan")
        self._latest_x_error = ""
        self._latest_x_timestamp = 0.0
        self._latest_z_mm = float("nan")
        self._latest_z_error = ""
        self._latest_z_timestamp = 0.0
        self._z_min_mm = 0.0
        self._z_max_mm = float(self.z_total_steps) * self._STAGE_STEP_SIZE_MM

        self._force_reader: Optional[Union[BotaForceReader, PhidgetForceReader]] = None
        self._fbg_reader: Optional[FBGStreamReader] = None

    @staticmethod
    def _stage_total_steps(module_id: int) -> int:
        return 1066666 if int(module_id) == 3 else 2133333

    @staticmethod
    def _run_stage_call(
        label: str,
        fn: Callable[[], float | None],
        *,
        retries: int = 3,
        retry_delay_s: float = 0.08,
    ) -> float | None:
        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                # Retry only for transient serial/timeout-like failures.
                if not isinstance(exc, (TimeoutError, ValueError, serial.SerialException)):
                    raise
                if attempt < retries:
                    time.sleep(retry_delay_s)
                    continue
        raise RuntimeError(
            f"Stage {label} failed after {retries} attempts: {last_exc}. "
            "If this persists, verify Stage ID and serial link stability."
        )

    @property
    def is_connected(self) -> bool:
        force_required = self.enable_bota or self.enable_loadcell
        force_ok = (self._force_reader is not None) or (not force_required)
        return self._stage_x is not None and force_ok and self._fbg_reader is not None

    def set_invert_z_axis(self, enabled: bool) -> None:
        self.invert_z_axis = bool(enabled)
        if self._stage_z is None:
            return
        try:
            with self._stage_lock:
                raw_val = self._run_stage_call("refresh_z_after_invert", lambda: self._stage_z.get_pos(), retries=2)
            with self._x_state_lock:
                raw_z_mm = float(raw_val) if raw_val is not None else float("nan")
                self._latest_z_mm = self._stage_to_user_z_mm(raw_z_mm)
                self._latest_z_error = ""
                self._latest_z_timestamp = time.perf_counter()
        except Exception as exc:
            with self._x_state_lock:
                self._latest_z_error = str(exc)

    def _stage_to_user_z_mm(self, stage_z_mm: float) -> float:
        z = float(stage_z_mm)
        if not np.isfinite(z):
            return float("nan")
        z_clamped = self._clamp(z, self._z_min_mm, self._z_max_mm)
        if not self.invert_z_axis:
            return z_clamped
        return self._z_max_mm - z_clamped

    def _user_to_stage_z_mm(self, user_z_mm: float) -> float:
        z = float(user_z_mm)
        if not np.isfinite(z):
            return float("nan")
        z_clamped = self._clamp(z, self._z_min_mm, self._z_max_mm)
        if not self.invert_z_axis:
            return z_clamped
        return self._z_max_mm - z_clamped

    def _build_stage_module(self, module_id: int, *, is_z_axis: bool = False) -> StageModuleControl:
        if self._stage_serial is None:
            raise RuntimeError("Stage serial link is not connected")
        total_steps = self.z_total_steps if is_z_axis else self._stage_total_steps(module_id)
        return StageModuleControl(
            self._stage_serial,
            int(module_id),
            step_size=self._STAGE_STEP_SIZE_MM,
            total_steps=total_steps,
        )

    @property
    def z_axis_max_mm(self) -> float:
        return float(self._z_max_mm)

    @staticmethod
    def list_stage_port_candidates() -> List[str]:
        # Prefer physical USB serial adapters and ignore generic ttyS* UARTs.
        candidates: List[str] = []
        ports = list(serial.tools.list_ports.comports())

        def _is_usb_serial(device: str, desc: str, hwid: str) -> bool:
            text = f"{device} {desc} {hwid}".lower()
            usb_markers = (
                "usb",
                "ttyusb",
                "ttyacm",
                "ch340",
                "cp210",
                "ftdi",
                "pl2303",
            )
            if os.name == "nt":
                return "com" in device.lower()
            return any(marker in text for marker in usb_markers)

        ranked: List[Tuple[int, str]] = []
        for p in ports:
            device = p.device or ""
            desc = p.description or ""
            hwid = p.hwid or ""
            if not device:
                continue
            if not _is_usb_serial(device, desc, hwid):
                continue

            key = f"{device} {desc} {hwid}".lower()
            score = 10
            if "usb serial" in key or "ch340" in key or "ftdi" in key or "cp210" in key:
                score = 0
            elif "ttyusb" in key or "ttyacm" in key:
                score = 1
            elif "usb" in key:
                score = 2
            ranked.append((score, device))

        ranked.sort(key=lambda item: (item[0], item[1]))
        candidates.extend([device for _, device in ranked])

        if os.name != "nt":
            # Some systems expose better stable names via /dev/serial/by-id
            by_id_dir = Path("/dev/serial/by-id")
            if by_id_dir.is_dir():
                for path in sorted(by_id_dir.iterdir()):
                    resolved = str(path.resolve()) if path.exists() else str(path)
                    if resolved.startswith("/dev/ttyUSB") or resolved.startswith("/dev/ttyACM"):
                        if str(path) not in candidates:
                            candidates.insert(0, str(path))

            # Fallback to direct glob if pyserial metadata is sparse.
            for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*"):
                for path in sorted(Path("/dev").glob(pattern.replace("/dev/", ""))):
                    dev = str(path)
                    if dev not in candidates:
                        candidates.append(dev)

        # Deduplicate while preserving order.
        deduped: List[str] = []
        seen = set()
        for dev in candidates:
            if dev in seen:
                continue
            seen.add(dev)
            deduped.append(dev)
        return deduped

    @staticmethod
    def find_stage_port() -> Optional[str]:
        candidates = ExperimentController.list_stage_port_candidates()
        return candidates[0] if candidates else None

    def connect(self, stage_port: Optional[str] = None) -> None:
        if self.is_connected:
            return

        candidates = self.list_stage_port_candidates()
        if stage_port:
            preferred = stage_port.strip()
            candidate_ports = [preferred] + [p for p in candidates if p != preferred]
        else:
            candidate_ports = candidates

        if not candidate_ports:
            raise RuntimeError(
                "No USB serial port found for stage. "
                "Expected a device like /dev/ttyUSB0, /dev/ttyACM0, or /dev/serial/by-id/... "
                "Check cable/power and USB-serial adapter."
            )

        stage_serial: Optional[serial.Serial] = None
        stage_x: Optional[StageModuleControl] = None
        port_errors: List[str] = []

        for port in candidate_ports:
            trial_serial: Optional[serial.Serial] = None
            try:
                trial_serial = serial.Serial(port, 9600, timeout=2)
                time.sleep(0.3)
                trial_serial.reset_input_buffer()
                trial_serial.reset_output_buffer()
                time.sleep(0.2)

                trial_stage_x = StageModuleControl(
                    trial_serial,
                    self.stage_module_id,
                    step_size=0.000047625,
                    total_steps=self._stage_total_steps(self.stage_module_id),
                )
                # Initial read can occasionally miss one response; retry.
                self._run_stage_call("connect/get_pos", lambda: trial_stage_x.get_pos(), retries=3)
                stage_serial = trial_serial
                stage_x = trial_stage_x
                first_pos = self._run_stage_call("connect/get_pos", lambda: trial_stage_x.get_pos(), retries=1)
                x_init = float(first_pos) if first_pos is not None else float("nan")
                with self._x_state_lock:
                    self._latest_x_mm = x_init
                    self._latest_x_error = ""
                    self._latest_x_timestamp = time.perf_counter()
                break
            except Exception as exc:
                port_errors.append(f"{port}: {exc}")
                try:
                    if trial_serial is not None:
                        trial_serial.close()
                except Exception:
                    pass

        if stage_serial is None or stage_x is None:
            details = "\n".join(port_errors) if port_errors else "No candidate ports tried."
            msg = (
                "Unable to open/configure the stage serial port.\n"
                "Tried ports:\n"
                f"{details}\n\n"
                "Check:\n"
                "1) Stage controller is powered and connected\n"
                "2) No other app is using the serial port\n"
                "3) Linux user has serial permission (dialout group)\n"
                "4) Correct Stage Port is selected"
            )
            raise RuntimeError(msg)

        try:
            self._stage_serial = stage_serial
            self._stage_x = stage_x
            self._stage_z = None
            self._start_x_reader()

            if self.enable_bota:
                force_reader = self._connect_bota_reader()
                self._force_reader = force_reader
            elif self.enable_loadcell:
                force_reader = self._connect_phidget_reader()
                self._force_reader = force_reader
            else:
                self._force_reader = None

            fbg_reader = FBGStreamReader(self.fbg_interrogator_cfg, history_seconds=10.0)
            self._fbg_reader = fbg_reader
            fbg_reader.start()
            if not fbg_reader.wait_until_ready(timeout=8.0):
                cfg = self.fbg_interrogator_cfg
                detail = (fbg_reader.error or "").strip()
                if detail:
                    raise RuntimeError(
                        "FBG interrogator connection failed: "
                        f"{detail} (target {cfg.ip_address}:{cfg.port})"
                    )
                raise RuntimeError(
                    "Timeout waiting for FBG interrogator connection "
                    f"(target {cfg.ip_address}:{cfg.port}). "
                    "Check interrogator power/cable/IP, and ensure no other app is connected."
                )
        except Exception:
            self.disconnect()
            raise

    def _bota_config_candidates(self) -> List[Path]:
        candidates: List[Path] = []

        env_cfg = os.environ.get("BOTA_CONFIG_PATH", "").strip()
        if env_cfg:
            candidates.append(Path(env_cfg))

        candidates.append(self.bota_config_path)
        candidates.append(ROOT_DIR / "bota_driver_config" / "ethercat_gen0.json")
        candidates.append(ROOT_DIR / "bota_driver_config" / "ethercat.json")

        deduped: List[Path] = []
        seen = set()
        for path in candidates:
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                continue
            seen.add(key)
            if path.exists():
                deduped.append(path)
        return deduped

    def _bota_interface_candidates(self) -> List[Optional[str]]:
        if self.bota_interface_override:
            return [self.bota_interface_override]

        env_iface = os.environ.get("BOTA_NETWORK_INTERFACE", "").strip()
        if env_iface:
            return [env_iface]

        if os.name == "nt":
            return [None]

        interfaces = list_linux_network_interfaces()
        if not interfaces:
            return [None]

        # Try discovered interfaces first, then "None" to keep config default as last fallback.
        return [*interfaces, None]

    def _connect_bota_reader(self) -> BotaForceReader:
        config_candidates = self._bota_config_candidates()
        if not config_candidates:
            raise RuntimeError(
                "Bota initialization failed: no valid config file found. "
                "Provide --bota-config or set BOTA_CONFIG_PATH."
            )

        iface_candidates = self._bota_interface_candidates()
        attempt_errors: List[str] = []

        for cfg in config_candidates:
            for iface in iface_candidates:
                reader = BotaForceReader(cfg, network_interface_override=iface)
                reader.start()

                iface_label = iface if iface else "<config-default>"
                prefix = f"config={cfg} iface={iface_label}"

                if not reader.wait_until_initialized(timeout=10.0):
                    reader.stop()
                    attempt_errors.append(f"{prefix}: timeout during initialize")
                    continue
                if reader.error:
                    reader.stop()
                    attempt_errors.append(f"{prefix}: {reader.error}")
                    continue
                if not reader.wait_for_first_sample(timeout=5.0):
                    reader.stop()
                    attempt_errors.append(f"{prefix}: initialized but no samples")
                    continue

                # Keep successful config/interface for future reconnections.
                self.bota_config_path = cfg
                if iface:
                    self.bota_interface_override = iface
                return reader

        details = "\n".join(attempt_errors) if attempt_errors else "No attempts executed."
        raise RuntimeError(
            "Bota initialization failed: unable to open communication with the sensor.\n"
            "Tried:\n"
            f"{details}\n\n"
            "Hints:\n"
            "1) Run with root on Linux: sudo -E ./run_experiment_panel.sh\n"
            "2) Set Bota NIC explicitly: export BOTA_NETWORK_INTERFACE=<iface>\n"
            "3) Verify standalone first: ./run_bota_realtime.sh"
        )

    def _connect_phidget_reader(self) -> PhidgetForceReader:
        reader = PhidgetForceReader(
            channel=self.loadcell_channel,
            rate_hz=self.loadcell_rate_hz,
            interval_ms=self.loadcell_interval_ms,
            gain=self.loadcell_gain,
            avg_window=self.loadcell_avg_window,
            serial_number=self.loadcell_serial,
            calibration_path=self.loadcell_calibration_path,
            attach_timeout_s=self.loadcell_attach_timeout_s,
        )
        reader.start()

        if not reader.wait_until_initialized(timeout=max(8.0, self.loadcell_attach_timeout_s + 5.0)):
            reader.stop()
            raise RuntimeError("Load-cell initialization timeout.")
        if reader.error:
            reader.stop()
            raise RuntimeError(
                "Load-cell initialization failed: "
                f"{reader.error}\n\n"
                "Hints:\n"
                "1) Verify Phidget USB permissions (udev rules)\n"
                "2) Verify calibration file path (--loadcell-cal)\n"
                "3) Verify channel/gain settings"
            )
        if not reader.wait_for_first_sample(timeout=5.0):
            reader.stop()
            raise RuntimeError("Load-cell initialized but no force samples were received.")
        return reader

    def disconnect(self) -> None:
        self._stop_x_reader()
        if self._fbg_reader is not None:
            self._fbg_reader.stop()
            self._fbg_reader = None

        if self._force_reader is not None:
            self._force_reader.stop()
            self._force_reader = None

        if self._stage_serial is not None:
            try:
                if self._stage_serial.is_open:
                    self._stage_serial.close()
            finally:
                self._stage_serial = None
                self._stage_x = None
                self._stage_z = None
        with self._x_state_lock:
            self._latest_x_mm = float("nan")
            self._latest_x_error = ""
            self._latest_x_timestamp = 0.0
            self._latest_z_mm = float("nan")
            self._latest_z_error = ""
            self._latest_z_timestamp = 0.0

    def _start_x_reader(self) -> None:
        if self._x_reader_thread is not None and self._x_reader_thread.is_alive():
            return
        self._x_reader_stop.clear()

        def _worker() -> None:
            while not self._x_reader_stop.is_set():
                if self._stage_x is None:
                    self._x_reader_stop.wait(0.10)
                    continue
                try:
                    with self._stage_lock:
                        value = self._run_stage_call("get_pos", lambda: self._stage_x.get_pos(), retries=1)
                        x_mm = float(value) if value is not None else float("nan")
                        z_mm = float("nan")
                        z_error = ""
                        if self._stage_z is not None:
                            try:
                                value_z = self._run_stage_call("get_pos_z", lambda: self._stage_z.get_pos(), retries=1)
                                raw_z_mm = float(value_z) if value_z is not None else float("nan")
                                z_mm = self._stage_to_user_z_mm(raw_z_mm)
                            except Exception as exc:
                                z_error = str(exc)
                    with self._x_state_lock:
                        self._latest_x_mm = x_mm
                        self._latest_x_error = ""
                        self._latest_x_timestamp = time.perf_counter()
                        if self._stage_z is not None:
                            self._latest_z_mm = z_mm
                            self._latest_z_error = z_error
                            self._latest_z_timestamp = time.perf_counter()
                except Exception as exc:
                    with self._x_state_lock:
                        self._latest_x_error = str(exc)
                self._x_reader_stop.wait(0.10)

        self._x_reader_thread = threading.Thread(target=_worker, daemon=True)
        self._x_reader_thread.start()

    def _stop_x_reader(self) -> None:
        self._x_reader_stop.set()
        if self._x_reader_thread is not None and self._x_reader_thread.is_alive():
            self._x_reader_thread.join(timeout=1.0)
        self._x_reader_thread = None

    def set_stage_module_id(self, module_id: int) -> None:
        module_id = int(module_id)
        if module_id not in (1, 2, 3):
            raise ValueError(f"Invalid stage module id: {module_id}")
        if module_id == self.z_stage_module_id:
            raise ValueError("X Stage ID and Z Stage ID must be different.")

        self.stage_module_id = module_id

        # If not connected yet, just store preference.
        if self._stage_serial is None:
            return

        with self._stage_lock:
            candidate = StageModuleControl(
                self._stage_serial,
                module_id,
                step_size=0.000047625,
                total_steps=self._stage_total_steps(module_id),
            )
            self._run_stage_call("switch_stage_id/get_pos", lambda: candidate.get_pos(), retries=3)
            self._stage_x = candidate

    def set_z_stage_module_id(self, module_id: int) -> None:
        module_id = int(module_id)
        if module_id not in (1, 2, 3):
            raise ValueError(f"Invalid Z stage module id: {module_id}")
        if module_id == self.stage_module_id:
            raise ValueError("X Stage ID and Z Stage ID must be different.")

        self.z_stage_module_id = module_id

        if self._stage_serial is None:
            self._stage_z = None
            return

        if self._stage_z is None:
            return

        with self._stage_lock:
            candidate = self._build_stage_module(module_id, is_z_axis=True)
            self._run_stage_call("switch_z_stage_id/get_pos", lambda: candidate.get_pos(), retries=3)
            self._stage_z = candidate
            z_value = self._run_stage_call("switch_z_stage_id/get_pos", lambda: candidate.get_pos(), retries=1)
        with self._x_state_lock:
            raw_z_mm = float(z_value) if z_value is not None else float("nan")
            self._latest_z_mm = self._stage_to_user_z_mm(raw_z_mm)
            self._latest_z_error = ""
            self._latest_z_timestamp = time.perf_counter()

    def _ensure_stage_z(self) -> StageModuleControl:
        if self._stage_serial is None:
            raise RuntimeError("Stage is not connected")
        if self.z_stage_module_id == self.stage_module_id:
            raise RuntimeError("X Stage ID and Z Stage ID must be different.")
        if self._stage_z is not None:
            return self._stage_z

        with self._stage_lock:
            candidate = self._build_stage_module(self.z_stage_module_id, is_z_axis=True)
            self._run_stage_call("connect_z/get_pos", lambda: candidate.get_pos(), retries=3)
            self._stage_z = candidate
            z_value = self._run_stage_call("connect_z/get_pos", lambda: candidate.get_pos(), retries=1)
        with self._x_state_lock:
            raw_z_mm = float(z_value) if z_value is not None else float("nan")
            self._latest_z_mm = self._stage_to_user_z_mm(raw_z_mm)
            self._latest_z_error = ""
            self._latest_z_timestamp = time.perf_counter()
        return candidate

    def home_x(self) -> None:
        if self._stage_x is None:
            raise RuntimeError("Stage is not connected")
        with self._stage_lock:
            self._run_stage_call("home", lambda: self._stage_x.home(), retries=3)

    def home_z(self) -> None:
        if not self.invert_z_axis:
            stage_z = self._ensure_stage_z()
            with self._stage_lock:
                self._run_stage_call("home_z", lambda: stage_z.home(), retries=3)
            return

        # Reversed axis: use software home so "Home Z" still maps to user Z=0.
        self.move_z_to_mm(
            0.0,
            tolerance_mm=0.02,
            poll_interval_s=0.05,
            max_wait_s=30.0,
        )

    def get_x_position_mm(self) -> float:
        if self._stage_x is None:
            raise RuntimeError("Stage is not connected")
        with self._stage_lock:
            value = self._run_stage_call("get_pos", lambda: self._stage_x.get_pos(), retries=3)
            assert value is not None
            return float(value)

    def get_z_position_mm(self) -> float:
        stage_z = self._ensure_stage_z()
        with self._stage_lock:
            value = self._run_stage_call("get_pos_z", lambda: stage_z.get_pos(), retries=3)
            assert value is not None
            return self._stage_to_user_z_mm(float(value))

    def move_z_to_mm(
        self,
        target_mm: float,
        *,
        tolerance_mm: float,
        poll_interval_s: float,
        max_wait_s: float,
        abort_event: Optional[threading.Event] = None,
    ) -> float:
        stage_z = self._ensure_stage_z()
        target_stage_mm = self._user_to_stage_z_mm(target_mm)
        with self._stage_lock:
            self._run_stage_call(
                "move_z",
                lambda: stage_z.go_pos_mm(target_stage_mm, wait=False),
                retries=3,
            )
        deadline = time.perf_counter() + max(1.0, float(max_wait_s))
        while True:
            if abort_event is not None and abort_event.is_set():
                raise RuntimeError("aborted")
            z_now = self.get_z_position_mm()
            if abs(z_now - target_mm) <= float(tolerance_mm):
                return z_now
            if time.perf_counter() >= deadline:
                raise RuntimeError(
                    f"move_z timeout: target={target_mm:.4f} mm, latest={z_now:.4f} mm"
                )
            time.sleep(max(0.01, float(poll_interval_s)))

    def get_latest_force(self) -> Optional[Dict[str, float]]:
        if self._force_reader is None:
            return None
        return self._force_reader.get_latest()

    def rezero_loadcell_force(self, capture_window_s: float = 1.5) -> Dict[str, float]:
        if not self.enable_loadcell:
            raise RuntimeError("Load-cell force is not enabled.")
        if self._force_reader is None:
            raise RuntimeError("Load-cell is not connected.")
        if not isinstance(self._force_reader, PhidgetForceReader):
            raise RuntimeError("Current force source is not the Phidget load cell.")
        return self._force_reader.rezero(capture_window_s=capture_window_s)

    def _latest_fbg1_with_timestamp(self) -> Tuple[float, float]:
        if self._fbg_reader is None or not self._fbg_reader.is_ready:
            return float("nan"), float("nan")

        sample_t, latest_values = self._fbg_reader.latest_sample()
        if not latest_values:
            return float(sample_t), float("nan")

        fbg1 = float(latest_values.get("fbg_1", np.nan))
        if not np.isfinite(fbg1):
            sensor_names = list(self._fbg_reader.sensor_names)
            if sensor_names:
                fbg1 = float(latest_values.get(sensor_names[0], np.nan))

        return float(sample_t), float(fbg1)

    def _latest_fbg1(self) -> float:
        _, fbg1 = self._latest_fbg1_with_timestamp()
        return fbg1

    def latest_snapshot(self, include_x: bool = False) -> Dict[str, float]:
        force = self.get_latest_force() or {}
        fbg_t, fbg1 = self._latest_fbg1_with_timestamp()
        snapshot = {
            "force_z_n": float(force.get("fz", np.nan)),
            "force_sample_time_s": float(force.get("timestamp", np.nan)),
            "fbg1_nm": float(fbg1),
            "fbg_sample_time_s": float(fbg_t),
            "x_read_error": "",
        }
        if include_x and self._stage_x is not None:
            with self._x_state_lock:
                snapshot["x_mm"] = float(self._latest_x_mm)
                snapshot["x_read_error"] = str(self._latest_x_error)
                snapshot["z_mm"] = float(self._latest_z_mm)
                snapshot["z_read_error"] = str(self._latest_z_error)
        return snapshot

    def latest_z_position_mm(self) -> float:
        with self._x_state_lock:
            return float(self._latest_z_mm)

    def get_fbg_history(self, max_points: int = 1500) -> Tuple[np.ndarray, np.ndarray]:
        if self._fbg_reader is None or not self._fbg_reader.is_ready:
            return np.array([]), np.array([])

        use_max = max_points if max_points and max_points > 0 else None
        timestamps, series = self._fbg_reader.snapshot(max_points=use_max)
        if timestamps.size == 0:
            return np.array([]), np.array([])

        def _series_for(name: str, fallback_idx: int) -> np.ndarray:
            values = series.get(name)
            if values is not None and values.size == timestamps.size:
                return values
            names = list(self._fbg_reader.sensor_names)
            if fallback_idx < len(names):
                alt = series.get(names[fallback_idx])
                if alt is not None and alt.size == timestamps.size:
                    return alt
            return np.full_like(timestamps, np.nan, dtype=np.float64)

        fbg1 = _series_for("fbg_1", 0)

        return timestamps, fbg1

    def probe_stage_ids(self) -> Dict[int, float]:
        if self._stage_serial is None:
            raise RuntimeError("Stage is not connected")

        results: Dict[int, float] = {}
        with self._stage_lock:
            for mid in (1, 2, 3):
                total_steps = 1066666 if mid == 3 else 2133333
                try:
                    probe = StageModuleControl(
                        self._stage_serial,
                        mid,
                        step_size=0.000047625,
                        total_steps=total_steps,
                    )
                    results[mid] = float(probe.get_pos())
                except Exception:
                    results[mid] = float("nan")
        return results

    def _capture_sensor_snapshot(self) -> Dict[str, float]:
        force = self.get_latest_force()
        fbg1 = self._latest_fbg1()
        force_z = float("nan")
        if force is not None and np.isfinite(force.get("fz", np.nan)):
            force_z = float(force["fz"])
        return {
            "force_z_n": force_z,
            "fbg1_nm": float(fbg1) if np.isfinite(fbg1) else float("nan"),
        }

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _sanitize_name_for_filename(value: str, default: str = "whisker") -> str:
        cleaned = (value or "").strip().replace(" ", "_")
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned)
        cleaned = cleaned.strip("._-")
        return cleaned or default

    def run_displacement(
        self,
        config: DisplacementConfig,
        *,
        abort_event: threading.Event,
        progress_callback: Optional[Callable[[Dict[str, float]], None]] = None,
    ) -> DisplacementBatchResult:
        if not self.is_connected:
            raise RuntimeError("Devices are not connected")

        whisker_name = (config.whisker_name or "").strip() or "whisker"
        whisker_slug = self._sanitize_name_for_filename(whisker_name, default="whisker")
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        trial_dir = self.output_dir / f"{whisker_slug}_displacement_{run_id}"
        requested_start_x = self._clamp(config.start_x_mm, config.x_min_mm, config.x_max_mm)
        requested_end_x = self._clamp(
            requested_start_x + config.displacement_mm,
            config.x_min_mm,
            config.x_max_mm,
        )
        use_z_stack = bool(config.use_z_stack)
        z_level_total = max(1, int(config.z_level_count)) if use_z_stack else 1
        if use_z_stack:
            self._ensure_stage_z()
        z_targets = [
            self._clamp(
                config.start_z_mm + float(level_idx) * config.z_step_mm,
                config.z_min_mm,
                config.z_max_mm,
            )
            for level_idx in range(z_level_total)
        ] if use_z_stack else [float("nan")]

        trials_per_z = max(1, int(config.repeat_count))
        requested_trials = trials_per_z * z_level_total
        trace_csv_paths: List[str] = []
        trial_results: List[DisplacementResult] = []
        stop_reason = "completed"

        def _emit_inter_trial_progress(
            phase: str,
            trial_number: int,
            *,
            trial_number_within_z: int,
            requested_z_mm: float,
            z_level_index: int,
        ) -> None:
            if progress_callback is None:
                return
            try:
                x_now = self.get_x_position_mm()
            except Exception:
                x_now = float("nan")
            z_now = self.latest_z_position_mm()
            if use_z_stack and not np.isfinite(z_now):
                try:
                    z_now = self.get_z_position_mm()
                except Exception:
                    z_now = float("nan")
            snapshot = self._capture_sensor_snapshot()
            payload = {
                "phase": phase,
                "elapsed_s": float("nan"),
                "x_mm": float(x_now),
                "z_mm": float(z_now),
                "requested_start_x_mm": float(requested_start_x),
                "requested_end_x_mm": float(requested_end_x),
                "requested_z_mm": float(requested_z_mm),
                "force_z_n": float(snapshot["force_z_n"]),
                "fbg1_nm": float(snapshot["fbg1_nm"]),
                "trial_index": int(trial_number),
                "trial_total": int(requested_trials),
                "trial_number_within_z": int(trial_number_within_z),
                "trials_per_z": int(trials_per_z),
                "z_level_index": int(z_level_index),
                "z_level_total": int(z_level_total),
            }
            progress_callback(payload)

        global_trial_index = 0
        for z_idx, requested_z_mm in enumerate(z_targets, start=1):
            if abort_event.is_set():
                stop_reason = "aborted"
                break

            if use_z_stack:
                _emit_inter_trial_progress(
                    "moving_to_z_level",
                    global_trial_index + 1,
                    trial_number_within_z=1,
                    requested_z_mm=requested_z_mm,
                    z_level_index=z_idx,
                )
                try:
                    self.move_z_to_mm(
                        float(requested_z_mm),
                        tolerance_mm=max(0.01, float(config.position_tolerance_mm)),
                        poll_interval_s=float(config.move_poll_interval_s),
                        max_wait_s=float(config.max_move_wait_s),
                        abort_event=abort_event,
                    )
                except Exception as exc:
                    stop_reason = "aborted" if abort_event.is_set() else f"move_z_failed: {exc}"
                    break

                if config.settle_time_s > 0:
                    _emit_inter_trial_progress(
                        "z_settle",
                        global_trial_index + 1,
                        trial_number_within_z=1,
                        requested_z_mm=requested_z_mm,
                        z_level_index=z_idx,
                    )
                    time.sleep(float(config.settle_time_s))

                _emit_inter_trial_progress(
                    "z_level_reached",
                    global_trial_index + 1,
                    trial_number_within_z=1,
                    requested_z_mm=requested_z_mm,
                    z_level_index=z_idx,
                )

            for trial_in_z in range(1, trials_per_z + 1):
                if abort_event.is_set():
                    stop_reason = "aborted"
                    break

                global_trial_index += 1
                trial_id = f"{run_id}_trial_{global_trial_index:02d}"
                trace_filename = f"trace_trial_{global_trial_index:02d}.csv"

                wrapped_progress_callback = None
                if progress_callback is not None:

                    def _wrapped_progress(
                        row: Dict[str, float],
                        tnum: int = global_trial_index,
                        tz: int = trial_in_z,
                        zlevel: int = z_idx,
                        ztarget: float = requested_z_mm,
                    ) -> None:
                        payload = dict(row)
                        payload["trial_index"] = int(tnum)
                        payload["trial_total"] = int(requested_trials)
                        payload["trial_number_within_z"] = int(tz)
                        payload["trials_per_z"] = int(trials_per_z)
                        payload["z_level_index"] = int(zlevel)
                        payload["z_level_total"] = int(z_level_total)
                        payload["requested_z_mm"] = float(ztarget)
                        progress_callback(payload)

                    wrapped_progress_callback = _wrapped_progress

                trial_result = self._run_single_displacement_trial(
                    config,
                    trial_id=trial_id,
                    trial_index=global_trial_index,
                    trial_number_within_z=trial_in_z,
                    z_level_index=z_idx,
                    z_level_total=z_level_total,
                    requested_z_mm=float(requested_z_mm),
                    trial_dir=trial_dir,
                    trace_filename=trace_filename,
                    abort_event=abort_event,
                    progress_callback=wrapped_progress_callback,
                )
                trial_results.append(trial_result)
                trace_csv_paths.append(trial_result.trace_csv_path)

                if trial_result.stop_reason != "completed":
                    stop_reason = trial_result.stop_reason
                    break

                has_next_trial = global_trial_index < requested_trials
                if has_next_trial:
                    if abort_event.is_set():
                        stop_reason = "aborted"
                        break

                    _emit_inter_trial_progress(
                        "inter_trial_homing",
                        global_trial_index,
                        trial_number_within_z=trial_in_z,
                        requested_z_mm=requested_z_mm,
                        z_level_index=z_idx,
                    )
                    try:
                        self.home_x()
                    except Exception as exc:
                        stop_reason = f"inter_trial_home_failed: {exc}"
                        break
                    _emit_inter_trial_progress(
                        "inter_trial_homed",
                        global_trial_index,
                        trial_number_within_z=trial_in_z,
                        requested_z_mm=requested_z_mm,
                        z_level_index=z_idx,
                    )

                    wait_s = max(0.0, float(config.inter_trial_home_wait_s))
                    if wait_s > 0:
                        deadline = time.perf_counter() + wait_s
                        while time.perf_counter() < deadline:
                            if abort_event.is_set():
                                stop_reason = "aborted"
                                break
                            _emit_inter_trial_progress(
                                "inter_trial_wait",
                                global_trial_index,
                                trial_number_within_z=trial_in_z,
                                requested_z_mm=requested_z_mm,
                                z_level_index=z_idx,
                            )
                            sleep_step = max(0.02, min(0.25, float(config.move_poll_interval_s)))
                            time.sleep(sleep_step)
                        if stop_reason == "aborted":
                            break

            if stop_reason != "completed":
                break

        if not trial_results and stop_reason == "completed":
            stop_reason = "aborted" if abort_event.is_set() else "no_trials"

        summary_table_csv_path = self._save_summary_table_csv(trial_dir, trial_results)
        summary_table_csv_str = str(summary_table_csv_path)
        for trial_result in trial_results:
            trial_result.summary_table_csv_path = summary_table_csv_str

        return DisplacementBatchResult(
            run_id=run_id,
            whisker_name=whisker_name,
            requested_trials=requested_trials,
            completed_trials=len(trial_results),
            stop_reason=stop_reason,
            trial_dir_path=str(trial_dir),
            trace_csv_paths=trace_csv_paths,
            summary_table_csv_path=summary_table_csv_str,
            trials=trial_results,
        )

    def _run_single_displacement_trial(
        self,
        config: DisplacementConfig,
        *,
        trial_id: str,
        trial_index: int,
        trial_number_within_z: int,
        z_level_index: int,
        z_level_total: int,
        requested_z_mm: float,
        trial_dir: Path,
        trace_filename: str,
        abort_event: threading.Event,
        progress_callback: Optional[Callable[[Dict[str, float]], None]] = None,
    ) -> DisplacementResult:
        start_time = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        whisker_name = (config.whisker_name or "").strip() or "whisker"

        requested_start_x = self._clamp(config.start_x_mm, config.x_min_mm, config.x_max_mm)
        requested_end_x = self._clamp(
            requested_start_x + config.displacement_mm,
            config.x_min_mm,
            config.x_max_mm,
        )

        initial_x = self.get_x_position_mm()
        initial_z = self.latest_z_position_mm()
        if (not np.isfinite(initial_z)) and (config.use_z_stack or self._stage_z is not None):
            try:
                initial_z = self.get_z_position_mm()
            except Exception:
                initial_z = float("nan")
        initial_snapshot = self._capture_sensor_snapshot()

        trace_rows: List[Dict[str, float]] = []

        def _append_trace(phase: str, x_mm: float, snapshot: Dict[str, float]) -> None:
            z_mm = self.latest_z_position_mm()
            if not np.isfinite(z_mm):
                z_mm = initial_z
            row = {
                "phase": phase,
                "elapsed_s": time.perf_counter() - t0,
                "x_mm": float(x_mm),
                "z_mm": float(z_mm),
                "requested_start_x_mm": requested_start_x,
                "requested_end_x_mm": requested_end_x,
                "requested_z_mm": float(requested_z_mm),
                "force_z_n": float(snapshot["force_z_n"]),
                "fbg1_nm": float(snapshot["fbg1_nm"]),
            }
            trace_rows.append(row)
            if progress_callback is not None:
                progress_callback(dict(row))

        def _monitor_until_target(phase: str, target_x: float) -> Optional[str]:
            deadline = time.perf_counter() + max(1.0, float(config.max_move_wait_s))
            while True:
                if abort_event.is_set():
                    return "aborted"

                x_now = self.get_x_position_mm()
                snap_now = self._capture_sensor_snapshot()
                _append_trace(phase, x_now, snap_now)

                if abs(x_now - target_x) <= config.position_tolerance_mm:
                    return None

                if time.perf_counter() >= deadline:
                    return f"{phase}_timeout"

                time.sleep(max(0.01, float(config.move_poll_interval_s)))

        def _command_move_and_monitor(phase: str, target_x: float) -> Tuple[Optional[str], float]:
            t_move0 = time.perf_counter()
            with self._stage_lock:
                assert self._stage_x is not None
                self._run_stage_call(
                    phase,
                    # Non-blocking command so we can keep sampling while stage is moving.
                    lambda: self._stage_x.go_pos_mm(target_x, wait=False),
                    retries=3,
                )
            reason = _monitor_until_target(phase, target_x)
            return reason, time.perf_counter() - t_move0

        def _move_to_with_speed_scale(phase: str, target_x: float) -> Optional[str]:
            x_now = self.get_x_position_mm()
            if abs(x_now - target_x) <= float(config.position_tolerance_mm):
                return None

            speed_scale = max(0.05, min(1.0, float(config.stage_speed_scale)))
            if speed_scale >= 0.999:
                reason, _ = _command_move_and_monitor(phase, target_x)
                return reason

            # Use two equal segments and an extra hold to realize a lower effective speed.
            mid_x = x_now + 0.5 * (target_x - x_now)
            reason, first_segment_s = _command_move_and_monitor(phase, mid_x)
            if reason is not None:
                return reason

            base_two_segment_s = max(0.0, 2.0 * float(first_segment_s))
            extra_hold_s = base_two_segment_s * ((1.0 / speed_scale) - 1.0)
            if extra_hold_s > 1e-3:
                reason = _wait_with_progress(f"{phase}_slow_hold", extra_hold_s)
                if reason is not None:
                    return reason

            reason, _ = _command_move_and_monitor(phase, target_x)
            return reason

        def _wait_with_progress(phase: str, wait_s: float) -> Optional[str]:
            if wait_s <= 0:
                return None
            deadline = time.perf_counter() + wait_s
            while True:
                if abort_event.is_set():
                    return "aborted"
                x_now = self.get_x_position_mm()
                snap_now = self._capture_sensor_snapshot()
                _append_trace(phase, x_now, snap_now)
                if time.perf_counter() >= deadline:
                    return None
                time.sleep(max(0.02, float(config.move_poll_interval_s)))

        def _capture_average_stationary(
            phase_prefix: str,
            expected_x: float,
        ) -> Tuple[Dict[str, float], float, Optional[str]]:
            window_s = max(0.05, float(config.snapshot_avg_window_s))
            stability_span_limit = max(0.002, float(config.position_tolerance_mm) * 2.0)
            max_attempt_s = max(2.0, float(config.max_move_wait_s))
            attempt_deadline = time.perf_counter() + max_attempt_s
            last_avg_x = float("nan")
            last_snapshot = self._capture_sensor_snapshot()

            while True:
                x_samples: List[float] = []
                fz_samples: List[float] = []
                fbg1_samples: List[float] = []

                window_deadline = time.perf_counter() + window_s
                while True:
                    if abort_event.is_set():
                        return last_snapshot, float(last_avg_x), "aborted"
                    x_now = self.get_x_position_mm()
                    snap_now = self._capture_sensor_snapshot()
                    _append_trace(f"{phase_prefix}_avg_window", x_now, snap_now)

                    if np.isfinite(x_now):
                        x_samples.append(float(x_now))
                    fz_now = float(snap_now.get("force_z_n", np.nan))
                    if np.isfinite(fz_now):
                        fz_samples.append(fz_now)
                    fbg1_now = float(snap_now.get("fbg1_nm", np.nan))
                    if np.isfinite(fbg1_now):
                        fbg1_samples.append(fbg1_now)

                    if time.perf_counter() >= window_deadline:
                        break
                    time.sleep(max(0.01, min(0.05, float(config.move_poll_interval_s))))

                avg_x = float(np.mean(x_samples)) if x_samples else float("nan")
                avg_snapshot = {
                    "force_z_n": float(np.mean(fz_samples)) if fz_samples else float("nan"),
                    "fbg1_nm": float(np.mean(fbg1_samples)) if fbg1_samples else float("nan"),
                }
                last_avg_x = avg_x
                last_snapshot = avg_snapshot

                x_span = float(max(x_samples) - min(x_samples)) if len(x_samples) >= 2 else 0.0
                near_target = np.isfinite(avg_x) and (abs(avg_x - expected_x) <= float(config.position_tolerance_mm))
                is_stationary = x_span <= stability_span_limit

                if is_stationary and near_target:
                    return avg_snapshot, avg_x, None

                if time.perf_counter() >= attempt_deadline:
                    return avg_snapshot, avg_x, None

                if config.settle_time_s > 0:
                    time.sleep(config.settle_time_s)

        _append_trace("initial", initial_x, initial_snapshot)

        stop_reason = "completed"
        start_x = initial_x
        start_z = initial_z
        start_snapshot = initial_snapshot
        end_x = initial_x
        end_z = initial_z
        end_snapshot = initial_snapshot

        if abort_event.is_set():
            stop_reason = "aborted"
        else:
            # Requested warm-up wait before any displacement motion.
            reason = _wait_with_progress("pre_wait", float(config.pre_wait_s))
            if reason is not None:
                stop_reason = reason

            if abs(initial_x - requested_start_x) > config.position_tolerance_mm:
                reason = _move_to_with_speed_scale("moving_to_start", requested_start_x)
                if reason is not None:
                    stop_reason = reason

            if stop_reason == "completed":
                if config.settle_time_s > 0:
                    time.sleep(config.settle_time_s)

                start_snapshot, averaged_start_x, reason = _capture_average_stationary("start_reached", requested_start_x)
                if reason is not None:
                    stop_reason = reason
                start_x = float(averaged_start_x) if np.isfinite(averaged_start_x) else self.get_x_position_mm()
                start_z = self.latest_z_position_mm()
                if not np.isfinite(start_z):
                    start_z = initial_z
                _append_trace("start_reached", start_x, start_snapshot)

            if stop_reason == "completed" and abort_event.is_set():
                stop_reason = "aborted"

            if stop_reason == "completed":
                reason = _move_to_with_speed_scale("moving_to_end", requested_end_x)
                if reason is not None:
                    stop_reason = reason

                if stop_reason == "completed" and config.settle_time_s > 0:
                    time.sleep(config.settle_time_s)

                # Hold and keep sampling before final snapshot capture.
                if stop_reason == "completed":
                    reason = _wait_with_progress("post_wait_before_final", float(config.final_wait_s))
                    if reason is not None:
                        stop_reason = reason

                end_snapshot, averaged_end_x, reason = _capture_average_stationary("end_reached", requested_end_x)
                if reason is not None:
                    stop_reason = reason
                end_x = float(averaged_end_x) if np.isfinite(averaged_end_x) else self.get_x_position_mm()
                end_z = self.latest_z_position_mm()
                if not np.isfinite(end_z):
                    end_z = start_z
                _append_trace("end_reached", end_x, end_snapshot)

        if stop_reason == "aborted":
            end_x = self.get_x_position_mm()
            end_z = self.latest_z_position_mm()
            if not np.isfinite(end_z):
                end_z = start_z
            end_snapshot = self._capture_sensor_snapshot()
            _append_trace("aborted", end_x, end_snapshot)

        end_time = datetime.now(timezone.utc)
        elapsed_total = time.perf_counter() - t0
        trace_path = self._save_trace_csv(trial_dir, trace_rows, filename=trace_filename)

        return DisplacementResult(
            trial_id=trial_id,
            trial_index=int(trial_index),
            trial_number_within_z=int(trial_number_within_z),
            z_level_index=int(z_level_index),
            z_level_total=int(z_level_total),
            whisker_name=whisker_name,
            start_time_iso=start_time.isoformat(),
            end_time_iso=end_time.isoformat(),
            stop_reason=stop_reason,
            elapsed_s=elapsed_total,
            requested_start_x_mm=requested_start_x,
            requested_displacement_mm=config.displacement_mm,
            target_end_x_mm=requested_end_x,
            requested_z_mm=float(requested_z_mm),
            initial_x_mm=initial_x,
            initial_z_mm=float(initial_z),
            start_x_mm=start_x,
            start_z_mm=float(start_z),
            end_x_mm=end_x,
            end_z_mm=float(end_z),
            actual_displacement_mm=end_x - start_x,
            start_force_z_n=float(start_snapshot["force_z_n"]),
            start_fbg1_nm=float(start_snapshot["fbg1_nm"]),
            end_force_z_n=float(end_snapshot["force_z_n"]),
            end_fbg1_nm=float(end_snapshot["fbg1_nm"]),
            trace_csv_path=str(trace_path),
            summary_table_csv_path="",
        )

    def _save_trace_csv(self, trial_dir: Path, rows: List[Dict[str, float]], *, filename: str = "trace.csv") -> Path:
        trial_dir.mkdir(parents=True, exist_ok=True)
        path = trial_dir / filename
        fieldnames = [
            "phase",
            "elapsed_s",
            "x_mm",
            "z_mm",
            "requested_start_x_mm",
            "requested_end_x_mm",
            "requested_z_mm",
            "force_z_n",
            "fbg1_nm",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def _save_summary_table_csv(self, trial_dir: Path, results: List[DisplacementResult]) -> Path:
        trial_dir.mkdir(parents=True, exist_ok=True)
        path = trial_dir / "summary_table.csv"
        fieldnames = [
            "trial_id",
            "trial_index",
            "trial_number_within_z",
            "z_level_index",
            "z_level_total",
            "whisker_name",
            "requested_z_mm",
            "start_z_mm",
            "end_z_mm",
            "fbg1_displacement_nm",
            "force_change_n",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for result in results:
                writer.writerow(
                    {
                        "trial_id": result.trial_id,
                        "trial_index": int(result.trial_index),
                        "trial_number_within_z": int(result.trial_number_within_z),
                        "z_level_index": int(result.z_level_index),
                        "z_level_total": int(result.z_level_total),
                        "whisker_name": result.whisker_name,
                        "requested_z_mm": float(result.requested_z_mm),
                        "start_z_mm": float(result.start_z_mm),
                        "end_z_mm": float(result.end_z_mm),
                        "fbg1_displacement_nm": float(result.end_fbg1_nm - result.start_fbg1_nm),
                        "force_change_n": float(result.end_force_z_n - result.start_force_z_n),
                    }
                )
        return path


class ExperimentPanel(QtWidgets.QMainWindow):
    progress_signal = QtCore.pyqtSignal(dict)
    trial_done_signal = QtCore.pyqtSignal(object, object)
    rezero_done_signal = QtCore.pyqtSignal(object, object)

    def __init__(
        self,
        controller: ExperimentController,
        *,
        initial_stage_port: str = "",
        initial_whisker_name: str = "whisker",
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._trial_thread: Optional[threading.Thread] = None
        self._rezero_thread: Optional[threading.Thread] = None
        self._abort_event = threading.Event()
        self._setting_stage_id = False
        self._setting_z_stage_id = False
        self._loadcell_rezero_window_s = 1.5
        self._fbg_plot_window_s = 20.0
        self._fbg_plot_max_points = 800
        self._fbg_plot_enabled = pg is not None
        self._fbg_plot_times = deque()
        self._fbg_plot_values = deque()
        self._fbg_plot_t0: Optional[float] = None
        self._last_fbg_sample_time = float("nan")
        self._force_plot_window_s = 20.0
        self._force_plot_max_points = 800
        self._force_plot_times = deque()
        self._force_plot_values = deque()
        self._force_plot_t0: Optional[float] = None
        self._last_force_sample_time = float("nan")
        self._x_poll_period_s = 0.20
        self._last_x_poll_monotonic = 0.0

        if not initial_stage_port:
            initial_stage_port = self.controller.find_stage_port() or ""

        self.setWindowTitle("Whisker X/Z-Displacement Experiment Panel")
        self.resize(900, 600)

        self._build_ui(initial_stage_port, initial_whisker_name)
        self.progress_signal.connect(self._on_trial_progress)
        self.trial_done_signal.connect(self._on_trial_done)
        self.rezero_done_signal.connect(self._on_rezero_done)

        self._live_timer = QtCore.QTimer(self)
        self._live_timer.timeout.connect(self._refresh_live_snapshot)
        self._live_timer.start(50)

    def _build_ui(self, initial_stage_port: str, initial_whisker_name: str) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        conn_group = QtWidgets.QGroupBox("Connections")
        conn_layout = QtWidgets.QGridLayout(conn_group)
        self.stage_port_edit = QtWidgets.QLineEdit(initial_stage_port)
        self.stage_port_edit.setPlaceholderText("Auto-detect (e.g. /dev/ttyUSB0 or COM11)")
        self.stage_id_spin = QtWidgets.QSpinBox()
        self.stage_id_spin.setRange(1, 3)
        self.stage_id_spin.setValue(int(self.controller.stage_module_id))
        self.stage_id_spin.valueChanged.connect(self._on_stage_id_changed)
        self.z_stage_id_spin = QtWidgets.QSpinBox()
        self.z_stage_id_spin.setRange(1, 3)
        self.z_stage_id_spin.setValue(int(self.controller.z_stage_module_id))
        self.z_stage_id_spin.valueChanged.connect(self._on_z_stage_id_changed)
        self.invert_z_check = QtWidgets.QCheckBox("Invert Z Axis")
        self.invert_z_check.setChecked(bool(self.controller.invert_z_axis))
        self.invert_z_check.setToolTip(
            "Use software coordinate inversion for Z (recommended if Home Z moves in the wrong physical direction)."
        )
        self.invert_z_check.toggled.connect(self._on_invert_z_toggled)
        self.output_dir_edit = QtWidgets.QLineEdit(str(self.controller.output_dir))
        browse_btn = QtWidgets.QPushButton("Browse")
        browse_btn.clicked.connect(self._on_browse_output_dir)
        conn_layout.addWidget(QtWidgets.QLabel("Stage Port"), 0, 0)
        conn_layout.addWidget(self.stage_port_edit, 0, 1)
        conn_layout.addWidget(QtWidgets.QLabel("X Stage ID"), 0, 2)
        conn_layout.addWidget(self.stage_id_spin, 0, 3)
        conn_layout.addWidget(QtWidgets.QLabel("Z Stage ID"), 0, 4)
        conn_layout.addWidget(self.z_stage_id_spin, 0, 5)
        conn_layout.addWidget(QtWidgets.QLabel("Output Directory"), 1, 0)
        conn_layout.addWidget(self.output_dir_edit, 1, 1, 1, 4)
        conn_layout.addWidget(browse_btn, 1, 5)
        conn_layout.addWidget(self.invert_z_check, 2, 4, 1, 2)
        layout.addWidget(conn_group)

        disp_group = QtWidgets.QGroupBox("Displacement Control")
        disp_layout = QtWidgets.QGridLayout(disp_group)
        z_max_mm = float(self.controller.z_axis_max_mm)
        self.start_x_spin = self._make_spin(0.0, 101.6, 0.0, decimals=3, step=0.1)
        self.displacement_spin = self._make_spin(-101.6, 101.6, 1.0, decimals=3, step=0.1)
        self.whisker_name_edit = QtWidgets.QLineEdit((initial_whisker_name or "").strip() or "whisker")
        self.whisker_name_edit.setPlaceholderText("e.g. whisker_left_01")
        self.pre_wait_spin = self._make_spin(0.0, 300.0, 30.0, decimals=1, step=1.0)
        self.final_wait_spin = self._make_spin(0.0, 300.0, 30.0, decimals=1, step=1.0)
        self.settle_spin = self._make_spin(0.0, 5.0, 0.10, decimals=2, step=0.05)
        self.tolerance_spin = self._make_spin(0.001, 0.100, 0.005, decimals=3, step=0.001)
        self.use_z_stack_check = QtWidgets.QCheckBox("Enable Z Stack")
        self.start_z_spin = self._make_spin(0.0, z_max_mm, 0.0, decimals=3, step=0.1)
        self.z_step_spin = self._make_spin(-z_max_mm, z_max_mm, 5.0, decimals=3, step=0.1)
        self.z_levels_spin = QtWidgets.QSpinBox()
        self.z_levels_spin.setRange(1, 100)
        self.z_levels_spin.setValue(1)
        self.trial_count_spin = QtWidgets.QSpinBox()
        self.trial_count_spin.setRange(1, 100)
        self.trial_count_spin.setValue(int(DisplacementConfig.repeat_count))

        disp_layout.addWidget(QtWidgets.QLabel("Start X (mm)"), 0, 0)
        disp_layout.addWidget(self.start_x_spin, 0, 1)
        disp_layout.addWidget(QtWidgets.QLabel("X Displacement (mm)"), 0, 2)
        disp_layout.addWidget(self.displacement_spin, 0, 3)
        disp_layout.addWidget(QtWidgets.QLabel("Whisker Name"), 1, 0)
        disp_layout.addWidget(self.whisker_name_edit, 1, 1, 1, 3)
        disp_layout.addWidget(QtWidgets.QLabel("Pre-Wait (s)"), 2, 0)
        disp_layout.addWidget(self.pre_wait_spin, 2, 1)
        disp_layout.addWidget(QtWidgets.QLabel("Final-Wait (s)"), 2, 2)
        disp_layout.addWidget(self.final_wait_spin, 2, 3)
        disp_layout.addWidget(QtWidgets.QLabel("Settle Time (s)"), 3, 0)
        disp_layout.addWidget(self.settle_spin, 3, 1)
        disp_layout.addWidget(QtWidgets.QLabel("Start Tolerance (mm)"), 3, 2)
        disp_layout.addWidget(self.tolerance_spin, 3, 3)
        disp_layout.addWidget(self.use_z_stack_check, 4, 0, 1, 2)
        disp_layout.addWidget(QtWidgets.QLabel("Start Z (mm)"), 4, 2)
        disp_layout.addWidget(self.start_z_spin, 4, 3)
        disp_layout.addWidget(QtWidgets.QLabel("Z Step (mm)"), 5, 0)
        disp_layout.addWidget(self.z_step_spin, 5, 1)
        disp_layout.addWidget(QtWidgets.QLabel("Z Levels"), 5, 2)
        disp_layout.addWidget(self.z_levels_spin, 5, 3)
        disp_layout.addWidget(QtWidgets.QLabel("Trials / Z"), 6, 0)
        disp_layout.addWidget(self.trial_count_spin, 6, 1)
        layout.addWidget(disp_group)

        live_group = QtWidgets.QGroupBox("Live Readout")
        live_layout = QtWidgets.QGridLayout(live_group)
        self.force_label = QtWidgets.QLabel("nan")
        self.x_label = QtWidgets.QLabel("nan")
        self.z_label = QtWidgets.QLabel("nan")
        self.fbg1_label = QtWidgets.QLabel("nan")
        self.status_label = QtWidgets.QLabel("Disconnected")
        self.last_result_label = QtWidgets.QLabel("No trials yet.")
        self.last_result_label.setWordWrap(True)
        self.zero_force_btn = QtWidgets.QPushButton("Zero Force Here")
        self.zero_force_btn.setEnabled(False)
        self.zero_force_btn.setToolTip(
            "Keep the current load-cell slope unchanged and set the present force reading to zero."
        )

        live_layout.addWidget(QtWidgets.QLabel("Force (N)"), 0, 0)
        live_layout.addWidget(self.force_label, 0, 1)
        live_layout.addWidget(self.zero_force_btn, 0, 2, 1, 2)
        live_layout.addWidget(QtWidgets.QLabel("X Position (mm)"), 1, 0)
        live_layout.addWidget(self.x_label, 1, 1)
        live_layout.addWidget(QtWidgets.QLabel("Z Position (mm)"), 1, 2)
        live_layout.addWidget(self.z_label, 1, 3)
        live_layout.addWidget(QtWidgets.QLabel("FBG1 (nm)"), 2, 0)
        live_layout.addWidget(self.fbg1_label, 2, 1)
        live_layout.addWidget(QtWidgets.QLabel("Status"), 3, 0)
        live_layout.addWidget(self.status_label, 3, 1, 1, 3)
        live_layout.addWidget(QtWidgets.QLabel("Last Trial"), 4, 0)
        live_layout.addWidget(self.last_result_label, 4, 1, 1, 3)
        layout.addWidget(live_group)

        plot_group = QtWidgets.QGroupBox("Live Plots")
        plot_layout = QtWidgets.QVBoxLayout(plot_group)
        if self._fbg_plot_enabled:
            self.fbg_plot_widget = pg.GraphicsLayoutWidget()
            self.force_plot = self.fbg_plot_widget.addPlot(row=0, col=0, title="Force")
            self.force_plot.showGrid(x=True, y=True, alpha=0.25)
            self.force_plot.setLabel("left", "Force", units="N")
            self.force_plot.setLabel("bottom", "Time (latest=0)", units="s")
            self.force_plot.getAxis("left").enableAutoSIPrefix(False)
            self.force_plot.setClipToView(True)
            self.force_plot.setDownsampling(auto=True, mode="peak")
            self.force_curve = self.force_plot.plot(
                [],
                [],
                pen=pg.mkPen(color=(38, 139, 210), width=2),
            )

            self.fbg1_plot = self.fbg_plot_widget.addPlot(row=1, col=0, title="FBG1")
            self.fbg1_plot.showGrid(x=True, y=True, alpha=0.25)
            self.fbg1_plot.setLabel("left", "Wavelength", units="nm")
            self.fbg1_plot.setLabel("bottom", "Time (latest=0)", units="s")
            self.fbg1_plot.getAxis("left").enableAutoSIPrefix(False)
            self.fbg1_plot.setClipToView(True)
            self.fbg1_plot.setDownsampling(auto=True, mode="peak")
            self.fbg1_curve = self.fbg1_plot.plot(
                [],
                [],
                pen=pg.mkPen(color=(220, 50, 47), width=2),
            )
            plot_layout.addWidget(self.fbg_plot_widget)
        else:
            self.fbg_plot_widget = None
            self.force_plot = None
            self.force_curve = None
            self.fbg1_plot = None
            self.fbg1_curve = None
            plot_layout.addWidget(
                QtWidgets.QLabel("pyqtgraph is not available, so live plotting is disabled.")
            )
        layout.addWidget(plot_group)

        button_row = QtWidgets.QHBoxLayout()
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.home_btn = QtWidgets.QPushButton("Home X")
        self.home_z_btn = QtWidgets.QPushButton("Home Z")
        self.probe_btn = QtWidgets.QPushButton("Probe IDs")
        self.start_btn = QtWidgets.QPushButton("Start Displacement")
        self.abort_btn = QtWidgets.QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.home_btn.setEnabled(False)
        self.home_z_btn.setEnabled(False)
        self.probe_btn.setEnabled(False)
        self.start_btn.setEnabled(False)

        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.home_btn.clicked.connect(self._on_home_clicked)
        self.home_z_btn.clicked.connect(self._on_home_z_clicked)
        self.probe_btn.clicked.connect(self._on_probe_clicked)
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.abort_btn.clicked.connect(self._on_abort_clicked)
        self.zero_force_btn.clicked.connect(self._on_zero_force_clicked)

        button_row.addWidget(self.connect_btn)
        button_row.addWidget(self.home_btn)
        button_row.addWidget(self.home_z_btn)
        button_row.addWidget(self.probe_btn)
        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.abort_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)

    def _make_spin(
        self,
        min_v: float,
        max_v: float,
        default_v: float,
        *,
        decimals: int,
        step: float,
    ) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setRange(min_v, max_v)
        spin.setSingleStep(step)
        spin.setValue(default_v)
        return spin

    def _on_browse_output_dir(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory",
            str(self.controller.output_dir),
        )
        if selected:
            self.output_dir_edit.setText(selected)

    def _on_connect_clicked(self) -> None:
        if self.controller.is_connected:
            if self._trial_thread and self._trial_thread.is_alive():
                QtWidgets.QMessageBox.warning(self, "Busy", "Trial is running. Abort before disconnecting.")
                return
            self.controller.disconnect()
            self._reset_fbg_plot_buffers(clear_curve=True)
            self._reset_force_plot_buffers(clear_curve=True)
            self.status_label.setText("Disconnected")
            self.connect_btn.setText("Connect")
            self.home_btn.setEnabled(False)
            self.home_z_btn.setEnabled(False)
            self.probe_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.zero_force_btn.setEnabled(False)
            self.stage_port_edit.setEnabled(True)
            self.stage_id_spin.setEnabled(True)
            self.z_stage_id_spin.setEnabled(True)
            return

        output_dir = Path(self.output_dir_edit.text().strip() or self.controller.output_dir)
        self.controller.output_dir = output_dir
        x_stage_id = int(self.stage_id_spin.value())
        z_stage_id = int(self.z_stage_id_spin.value())
        if x_stage_id == z_stage_id:
            QtWidgets.QMessageBox.warning(self, "Stage IDs", "X Stage ID and Z Stage ID must be different.")
            return
        self.controller.stage_module_id = x_stage_id
        self.controller.z_stage_module_id = z_stage_id
        self.controller.set_invert_z_axis(bool(self.invert_z_check.isChecked()))
        stage_port = self.stage_port_edit.text().strip()
        if not stage_port:
            stage_port = self.controller.find_stage_port() or ""
            if stage_port:
                self.stage_port_edit.setText(stage_port)
        stage_port = stage_port or None

        try:
            self.status_label.setText("Connecting...")
            QtWidgets.QApplication.processEvents()
            self.controller.connect(stage_port=stage_port)
        except Exception as exc:
            self.status_label.setText("Connection failed")
            QtWidgets.QMessageBox.critical(self, "Connection Error", str(exc))
            return

        self.connect_btn.setText("Disconnect")
        self.home_btn.setEnabled(True)
        self.home_z_btn.setEnabled(True)
        self.probe_btn.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.zero_force_btn.setEnabled(self.controller.enable_loadcell)
        self.stage_port_edit.setEnabled(False)
        self.stage_id_spin.setEnabled(True)
        self.z_stage_id_spin.setEnabled(True)
        self.status_label.setText(
            f"Connected (X Stage ID {self.controller.stage_module_id}, Z Stage ID {self.controller.z_stage_module_id})"
        )
        self._reset_fbg_plot_buffers(clear_curve=True)
        self._reset_force_plot_buffers(clear_curve=True)
        self._last_x_poll_monotonic = 0.0

    def _on_stage_id_changed(self, value: int) -> None:
        if self._setting_stage_id:
            return

        if not self.controller.is_connected:
            self.controller.stage_module_id = int(value)
            return

        old_id = int(self.controller.stage_module_id)
        try:
            self.controller.set_stage_module_id(int(value))
            self.status_label.setText(
                f"Connected (X Stage ID {self.controller.stage_module_id}, Z Stage ID {self.controller.z_stage_module_id})"
            )
        except Exception as exc:
            self._setting_stage_id = True
            try:
                self.stage_id_spin.setValue(old_id)
            finally:
                self._setting_stage_id = False
            QtWidgets.QMessageBox.critical(
                self,
                "Stage ID Switch Failed",
                f"Failed to switch Stage ID to {value}:\n{exc}",
            )

    def _on_z_stage_id_changed(self, value: int) -> None:
        if self._setting_z_stage_id:
            return

        if not self.controller.is_connected:
            self.controller.z_stage_module_id = int(value)
            return

        old_id = int(self.controller.z_stage_module_id)
        try:
            self.controller.set_z_stage_module_id(int(value))
            self.status_label.setText(
                f"Connected (X Stage ID {self.controller.stage_module_id}, Z Stage ID {self.controller.z_stage_module_id})"
            )
        except Exception as exc:
            self._setting_z_stage_id = True
            try:
                self.z_stage_id_spin.setValue(old_id)
            finally:
                self._setting_z_stage_id = False
            QtWidgets.QMessageBox.critical(
                self,
                "Z Stage ID Switch Failed",
                f"Failed to switch Z Stage ID to {value}:\n{exc}",
            )

    def _on_invert_z_toggled(self, checked: bool) -> None:
        self.controller.set_invert_z_axis(bool(checked))
        if self.controller.is_connected:
            try:
                z_pos = self.controller.get_z_position_mm()
                if np.isfinite(z_pos):
                    self.z_label.setText(f"{z_pos:.4f}")
            except Exception:
                pass

    def _on_home_clicked(self) -> None:
        try:
            self.status_label.setText("Homing X...")
            QtWidgets.QApplication.processEvents()
            self.controller.home_x()
            x_pos = self.controller.get_x_position_mm()
            self.x_label.setText(f"{x_pos:.4f}")
            self.status_label.setText("X homed")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Stage Error", str(exc))
            self.status_label.setText("Home failed")

    def _on_home_z_clicked(self) -> None:
        try:
            if self.controller.invert_z_axis:
                self.status_label.setText("Homing Z (software-reversed)...")
            else:
                self.status_label.setText("Homing Z...")
            QtWidgets.QApplication.processEvents()
            self.controller.home_z()
            z_pos = self.controller.get_z_position_mm()
            self.z_label.setText(f"{z_pos:.4f}")
            self.status_label.setText("Z homed")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Stage Error", str(exc))
            self.status_label.setText("Z home failed")

    def _on_probe_clicked(self) -> None:
        try:
            positions = self.controller.probe_stage_ids()
            lines = []
            for mid in (1, 2, 3):
                value = positions.get(mid, float("nan"))
                text = f"{value:.4f} mm" if np.isfinite(value) else "read failed"
                marker = "  <-- selected" if mid == int(self.stage_id_spin.value()) else ""
                lines.append(f"ID {mid}: {text}{marker}")
            message = (
                "Stage module positions:\n\n"
                + "\n".join(lines)
                + "\n\nMove the axis manually, click Probe IDs again, and pick the ID that changes."
            )
            QtWidgets.QMessageBox.information(self, "Stage ID Probe", message)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Probe Error", str(exc))

    def _build_displacement_config(self) -> DisplacementConfig:
        return DisplacementConfig(
            start_x_mm=float(self.start_x_spin.value()),
            displacement_mm=float(self.displacement_spin.value()),
            use_z_stack=bool(self.use_z_stack_check.isChecked()),
            start_z_mm=float(self.start_z_spin.value()),
            z_step_mm=float(self.z_step_spin.value()),
            z_level_count=int(self.z_levels_spin.value()),
            whisker_name=self.whisker_name_edit.text().strip() or "whisker",
            repeat_count=int(self.trial_count_spin.value()),
            # UI does not expose reduced-speed segmented motion; use direct moves by default.
            stage_speed_scale=1.0,
            pre_wait_s=float(self.pre_wait_spin.value()),
            final_wait_s=float(self.final_wait_spin.value()),
            settle_time_s=float(self.settle_spin.value()),
            position_tolerance_mm=float(self.tolerance_spin.value()),
            z_max_mm=float(self.controller.z_axis_max_mm),
        )

    def _on_start_clicked(self) -> None:
        if not self.controller.is_connected:
            QtWidgets.QMessageBox.warning(self, "Not Connected", "Connect devices first.")
            return
        if self._trial_thread and self._trial_thread.is_alive():
            QtWidgets.QMessageBox.warning(self, "Busy", "Trial already running.")
            return

        config = self._build_displacement_config()
        self._abort_event.clear()
        self._set_running_ui(True)
        self.status_label.setText("Displacement running")

        def _worker() -> None:
            try:
                result = self.controller.run_displacement(
                    config,
                    abort_event=self._abort_event,
                    progress_callback=self.progress_signal.emit,
                )
                self.trial_done_signal.emit(result, None)
            except Exception as exc:
                self.trial_done_signal.emit(None, str(exc))

        self._trial_thread = threading.Thread(target=_worker, daemon=True)
        self._trial_thread.start()

    def _on_abort_clicked(self) -> None:
        self._abort_event.set()
        self.status_label.setText("Abort requested")

    def _on_zero_force_clicked(self) -> None:
        if not self.controller.is_connected:
            QtWidgets.QMessageBox.warning(self, "Not Connected", "Connect devices first.")
            return
        if not self.controller.enable_loadcell:
            QtWidgets.QMessageBox.warning(
                self,
                "Unavailable",
                "Force re-zero is only available for the Phidget load cell.",
            )
            return
        if self._trial_thread and self._trial_thread.is_alive():
            QtWidgets.QMessageBox.warning(self, "Busy", "Abort the trial before zeroing the force.")
            return
        if self._rezero_thread and self._rezero_thread.is_alive():
            return

        self.zero_force_btn.setEnabled(False)
        self.status_label.setText(
            f"Zeroing force at current position ({self._loadcell_rezero_window_s:.1f}s average)..."
        )

        def _worker() -> None:
            try:
                result = self.controller.rezero_loadcell_force(
                    capture_window_s=self._loadcell_rezero_window_s
                )
                self.rezero_done_signal.emit(result, None)
            except Exception as exc:
                self.rezero_done_signal.emit(None, str(exc))

        self._rezero_thread = threading.Thread(target=_worker, daemon=True)
        self._rezero_thread.start()

    def _set_running_ui(self, running: bool) -> None:
        self.connect_btn.setEnabled(not running)
        self.home_btn.setEnabled(not running)
        self.home_z_btn.setEnabled(not running and self.controller.is_connected)
        self.probe_btn.setEnabled(not running and self.controller.is_connected)
        self.start_btn.setEnabled(not running)
        self.abort_btn.setEnabled(running)
        self.zero_force_btn.setEnabled(
            (not running)
            and self.controller.is_connected
            and self.controller.enable_loadcell
            and not (self._rezero_thread and self._rezero_thread.is_alive())
        )

    def _on_trial_progress(self, row: Dict[str, float]) -> None:
        phase = str(row.get("phase", "")).strip()
        trial_index = row.get("trial_index")
        trial_total = row.get("trial_total")
        trial_prefix = ""
        if isinstance(trial_index, (int, float)) and isinstance(trial_total, (int, float)) and float(trial_total) > 0:
            trial_prefix = f"Trial {int(trial_index)}/{int(trial_total)} "
        phase_labels = {
            "inter_trial_homing": "inter-trial homing",
            "inter_trial_homed": "inter-trial homed",
            "inter_trial_wait": "inter-trial waiting",
            "moving_to_z_level": "moving to z level",
            "z_level_reached": "z level reached",
            "z_settle": "z settling",
        }
        phase_text = phase_labels.get(phase, phase)
        z_level_index = row.get("z_level_index")
        z_level_total = row.get("z_level_total")
        z_prefix = ""
        if (
            isinstance(z_level_index, (int, float))
            and isinstance(z_level_total, (int, float))
            and float(z_level_total) > 1
        ):
            z_prefix = f"Z {int(z_level_index)}/{int(z_level_total)} "
        if phase:
            self.status_label.setText(f"Displacement: {z_prefix}{trial_prefix}{phase_text}")
        if "force_z_n" in row and np.isfinite(row["force_z_n"]):
            self.force_label.setText(f"{row['force_z_n']:.4f}")
        if "x_mm" in row and np.isfinite(row["x_mm"]):
            self.x_label.setText(f"{row['x_mm']:.4f}")
        if "z_mm" in row and np.isfinite(row["z_mm"]):
            self.z_label.setText(f"{row['z_mm']:.4f}")
        if "fbg1_nm" in row and np.isfinite(row["fbg1_nm"]):
            self.fbg1_label.setText(f"{row['fbg1_nm']:.6f}")

    def _reset_fbg_plot_buffers(self, *, clear_curve: bool) -> None:
        self._fbg_plot_times.clear()
        self._fbg_plot_values.clear()
        self._fbg_plot_t0 = None
        self._last_fbg_sample_time = float("nan")
        if clear_curve and self._fbg_plot_enabled and self.fbg1_curve is not None:
            self.fbg1_curve.clear()

    def _reset_force_plot_buffers(self, *, clear_curve: bool) -> None:
        self._force_plot_times.clear()
        self._force_plot_values.clear()
        self._force_plot_t0 = None
        self._last_force_sample_time = float("nan")
        if clear_curve and self._fbg_plot_enabled and self.force_curve is not None:
            self.force_curve.clear()

    def _append_force_plot_sample(self, force_n: float, sample_time_s: Optional[float]) -> None:
        if not np.isfinite(force_n):
            return

        if sample_time_s is not None and np.isfinite(sample_time_s):
            if self._force_plot_t0 is None:
                self._force_plot_t0 = float(sample_time_s)
            t_plot = float(sample_time_s) - float(self._force_plot_t0)
        else:
            now = time.perf_counter()
            if self._force_plot_t0 is None:
                self._force_plot_t0 = now
            t_plot = now - float(self._force_plot_t0)

        self._force_plot_times.append(float(t_plot))
        self._force_plot_values.append(float(force_n))

        cutoff = float(t_plot) - self._force_plot_window_s
        while self._force_plot_times and self._force_plot_times[0] < cutoff:
            self._force_plot_times.popleft()
            self._force_plot_values.popleft()

        max_keep = max(self._force_plot_max_points * 2, 400)
        while len(self._force_plot_times) > max_keep:
            self._force_plot_times.popleft()
            self._force_plot_values.popleft()

    def _refresh_force_plot(self) -> None:
        if not self._fbg_plot_enabled or self.force_curve is None or self.force_plot is None:
            return

        if not self._force_plot_times:
            return

        ts = np.fromiter(self._force_plot_times, dtype=np.float64)
        fz = np.fromiter(self._force_plot_values, dtype=np.float64)
        if ts.size == 0 or fz.size == 0:
            return

        if ts.size > self._force_plot_max_points:
            step = max(1, int(np.ceil(ts.size / float(self._force_plot_max_points))))
            ts = ts[::step]
            fz = fz[::step]

        ts_rel = ts - float(ts[-1])
        self.force_curve.setData(ts_rel, fz)
        x_min = max(-self._force_plot_window_s, float(ts_rel[0]))
        self.force_plot.setXRange(x_min, 0.0, padding=0.01)

    def _append_fbg_plot_sample(self, fbg1_nm: float, sample_time_s: Optional[float]) -> None:
        if not np.isfinite(fbg1_nm):
            return

        if sample_time_s is not None and np.isfinite(sample_time_s):
            if self._fbg_plot_t0 is None:
                self._fbg_plot_t0 = float(sample_time_s)
            t_plot = float(sample_time_s) - float(self._fbg_plot_t0)
        else:
            now = time.perf_counter()
            if self._fbg_plot_t0 is None:
                self._fbg_plot_t0 = now
            t_plot = now - float(self._fbg_plot_t0)

        self._fbg_plot_times.append(float(t_plot))
        self._fbg_plot_values.append(float(fbg1_nm))

        cutoff = float(t_plot) - self._fbg_plot_window_s
        while self._fbg_plot_times and self._fbg_plot_times[0] < cutoff:
            self._fbg_plot_times.popleft()
            self._fbg_plot_values.popleft()

        max_keep = max(self._fbg_plot_max_points * 2, 400)
        while len(self._fbg_plot_times) > max_keep:
            self._fbg_plot_times.popleft()
            self._fbg_plot_values.popleft()

    def _refresh_fbg_plot(self) -> None:
        if not self._fbg_plot_enabled or self.fbg1_curve is None or self.fbg1_plot is None:
            return

        if not self._fbg_plot_times:
            return

        ts = np.fromiter(self._fbg_plot_times, dtype=np.float64)
        fbg1 = np.fromiter(self._fbg_plot_values, dtype=np.float64)
        if ts.size == 0 or fbg1.size == 0:
            return

        if ts.size > self._fbg_plot_max_points:
            step = max(1, int(np.ceil(ts.size / float(self._fbg_plot_max_points))))
            ts = ts[::step]
            fbg1 = fbg1[::step]

        ts_rel = ts - float(ts[-1])
        self.fbg1_curve.setData(ts_rel, fbg1)
        x_min = max(-self._fbg_plot_window_s, float(ts_rel[0]))
        self.fbg1_plot.setXRange(x_min, 0.0, padding=0.01)

    def _on_trial_done(self, result: Optional[DisplacementBatchResult], error: Optional[str]) -> None:
        self._set_running_ui(False)
        if error:
            self.status_label.setText("Displacement failed")
            QtWidgets.QMessageBox.critical(self, "Displacement Error", error)
            return

        assert result is not None
        self.status_label.setText(
            f"Displacement done: {result.completed_trials}/{result.requested_trials} trials ({result.stop_reason})"
        )
        if result.trials:
            last_trial = result.trials[-1]
            z_text = (
                f" | z_level={last_trial.z_level_index}/{last_trial.z_level_total}"
                f" | z={last_trial.start_z_mm:.4f}->{last_trial.end_z_mm:.4f} mm"
                if np.isfinite(last_trial.start_z_mm) or np.isfinite(last_trial.end_z_mm)
                else ""
            )
            self.last_result_label.setText(
                f"Batch {result.run_id} ({result.whisker_name}) | trials={result.completed_trials}/{result.requested_trials} | "
                f"reason={result.stop_reason} | last start_x={last_trial.start_x_mm:.4f} -> end_x={last_trial.end_x_mm:.4f} "
                f"(requested {last_trial.requested_displacement_mm:+.4f} mm, actual {last_trial.actual_displacement_mm:+.4f} mm)"
                f"{z_text} | "
                f"trial_dir={result.trial_dir_path} | summary={result.summary_table_csv_path}"
            )
        else:
            self.last_result_label.setText(
                f"Batch {result.run_id} ({result.whisker_name}) | no completed trials | trial_dir={result.trial_dir_path}"
            )

    def _on_rezero_done(self, result: Optional[Dict[str, float]], error: Optional[str]) -> None:
        self.zero_force_btn.setEnabled(self.controller.is_connected and self.controller.enable_loadcell)
        if error:
            self.status_label.setText("Force zero failed")
            QtWidgets.QMessageBox.critical(self, "Force Zero Failed", error)
            return

        assert result is not None
        force_n = float(result.get("force_n", np.nan))
        if np.isfinite(force_n):
            self.force_label.setText(f"{force_n:.4f}")
        self.status_label.setText(
            "Force zeroed at current position "
            f"(offset {result['previous_offset']:.12g} -> {result['new_offset']:.12g}, "
            f"{int(result['samples'])} samples)"
        )

    def _refresh_live_snapshot(self) -> None:
        if not self.controller.is_connected:
            return

        now = time.perf_counter()
        trial_running = self._trial_thread is not None and self._trial_thread.is_alive()
        include_x = (not trial_running) and ((now - self._last_x_poll_monotonic) >= self._x_poll_period_s)
        snapshot = self.controller.latest_snapshot(include_x=include_x)
        if include_x:
            self._last_x_poll_monotonic = now
        force_n = float(snapshot.get("force_z_n", np.nan))
        force_sample_t = float(snapshot.get("force_sample_time_s", np.nan))
        if np.isfinite(force_n):
            self.force_label.setText(f"{force_n:.4f}")
            if (
                not np.isfinite(force_sample_t)
                or not np.isfinite(self._last_force_sample_time)
                or force_sample_t > self._last_force_sample_time
            ):
                self._append_force_plot_sample(force_n, force_sample_t if np.isfinite(force_sample_t) else None)
                if np.isfinite(force_sample_t):
                    self._last_force_sample_time = force_sample_t
        fbg1_nm = float(snapshot.get("fbg1_nm", np.nan))
        fbg_sample_t = float(snapshot.get("fbg_sample_time_s", np.nan))
        if np.isfinite(fbg1_nm):
            self.fbg1_label.setText(f"{fbg1_nm:.6f}")
            if not np.isfinite(fbg_sample_t) or not np.isfinite(self._last_fbg_sample_time) or fbg_sample_t > self._last_fbg_sample_time:
                self._append_fbg_plot_sample(fbg1_nm, fbg_sample_t if np.isfinite(fbg_sample_t) else None)
                if np.isfinite(fbg_sample_t):
                    self._last_fbg_sample_time = fbg_sample_t
        x_value = snapshot.get("x_mm", float("nan"))
        if np.isfinite(x_value):
            self.x_label.setText(f"{x_value:.4f}")
        z_value = snapshot.get("z_mm", float("nan"))
        if np.isfinite(z_value):
            self.z_label.setText(f"{z_value:.4f}")
        self._refresh_force_plot()
        self._refresh_fbg_plot()
        x_err = str(snapshot.get("x_read_error", "") or "").strip()
        z_err = str(snapshot.get("z_read_error", "") or "").strip()
        if x_err:
            self.status_label.setText(f"X read issue: {x_err}")
        elif z_err:
            self.status_label.setText(f"Z read issue: {z_err}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._abort_event.set()
        if self._trial_thread and self._trial_thread.is_alive():
            self._trial_thread.join(timeout=2.0)
        if self._rezero_thread and self._rezero_thread.is_alive():
            self._rezero_thread.join(timeout=2.0)
        self.controller.disconnect()
        super().closeEvent(event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="X-displacement stage experiment panel with FBG + force logging")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT_DIR / "experiment_data",
        help="Directory for trial CSV/JSON output",
    )
    parser.add_argument(
        "--bota-config",
        type=Path,
        default=ROOT_DIR / "bota_driver_config" / "ethercat_gen0.json",
        help="Path to Bota driver JSON config",
    )
    parser.add_argument(
        "--bota-interface",
        type=str,
        default="",
        help="Optional Linux network interface for Bota EtherCAT (e.g. enp14s0)",
    )
    parser.add_argument(
        "--enable-bota",
        action="store_true",
        help="Enable Bota force sensor connection (disabled by default).",
    )
    parser.add_argument(
        "--disable-bota",
        action="store_true",
        help="Deprecated compatibility flag; Bota is already disabled by default.",
    )
    parser.add_argument(
        "--enable-loadcell",
        action="store_true",
        help="Enable Phidget load-cell force input (uses calibrated force).",
    )
    parser.add_argument(
        "--loadcell-cal",
        type=Path,
        default=ROOT_DIR / "calibration.json",
        help="Calibration JSON for the load cell (offset/scale).",
    )
    parser.add_argument(
        "--loadcell-channel",
        type=int,
        default=0,
        choices=[0, 1, 2, 3],
        help="Phidget bridge channel for the load cell.",
    )
    parser.add_argument(
        "--loadcell-rate",
        type=float,
        default=200.0,
        help="Target load-cell rate in Hz (ignored if --loadcell-interval-ms set).",
    )
    parser.add_argument(
        "--loadcell-interval-ms",
        type=float,
        default=None,
        help="Load-cell data interval in ms (overrides --loadcell-rate).",
    )
    parser.add_argument(
        "--loadcell-gain",
        type=int,
        default=128,
        choices=[1, 8, 16, 32, 64, 128],
        help="Load-cell bridge gain.",
    )
    parser.add_argument(
        "--loadcell-avg-window",
        type=int,
        default=20,
        help="Moving-average window (samples) for calibrated force.",
    )
    parser.add_argument(
        "--loadcell-serial",
        type=int,
        default=None,
        help="Optional Phidget serial number to bind.",
    )
    parser.add_argument(
        "--loadcell-attach-timeout",
        type=float,
        default=5.0,
        help="Seconds per load-cell attach attempt before retrying.",
    )
    parser.add_argument(
        "--fbg-config",
        type=Path,
        default=None,
        help="Optional YAML config for FBG interrogator settings",
    )
    parser.add_argument(
        "--fbg-data-interleave",
        type=int,
        default=4,
        help="FBG data interleave (higher -> lower sample rate, lower lag risk).",
    )
    parser.add_argument(
        "--fbg-num-averages",
        type=int,
        default=1,
        help="FBG number of averages per sample.",
    )
    parser.add_argument(
        "--stage-port",
        type=str,
        default="",
        help="Optional serial port for stage (auto-detect when omitted)",
    )
    parser.add_argument(
        "--stage-id",
        type=int,
        default=1,
        choices=[1, 2, 3],
        help="X stage module ID to control (1/2/3).",
    )
    parser.add_argument(
        "--z-stage-id",
        type=int,
        default=3,
        choices=[1, 2, 3],
        help="Z stage module ID to control for Z-stack experiments (default: 3).",
    )
    parser.add_argument(
        "--z-total-steps",
        type=int,
        default=1066666,
        help="Total steps for Z axis travel scaling (default: 1066666 for 50.8 mm stage).",
    )
    parser.add_argument(
        "--invert-z-axis",
        action="store_true",
        help="Invert Z software coordinates and use software Z home (use when Z direction appears reversed).",
    )
    parser.add_argument(
        "--whisker-name",
        type=str,
        default="whisker",
        help="Default whisker name used in output filenames.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    enable_bota = bool(args.enable_bota and not args.disable_bota)
    enable_loadcell = bool(args.enable_loadcell)
    if enable_bota and enable_loadcell:
        print("Enable either Bota or load-cell, not both.", file=sys.stderr)
        return 1
    if enable_bota and bota_driver is None:
        print(f"bota_driver import failed: {BOTA_IMPORT_ERROR}", file=sys.stderr)
        return 1

    fbg_cfg = load_config(args.fbg_config).interrogator if args.fbg_config else DEFAULT_CONFIG.interrogator
    if len(fbg_cfg.sensors) > 1:
        # Force panel to use only FBG1 from configuration.
        fbg_cfg = InterrogatorSettings(
            ip_address=fbg_cfg.ip_address,
            port=fbg_cfg.port,
            data_interleave=fbg_cfg.data_interleave,
            num_averages=fbg_cfg.num_averages,
            ch_gains=list(fbg_cfg.ch_gains),
            ch_noise_thresholds=list(fbg_cfg.ch_noise_thresholds),
            sensors=[fbg_cfg.sensors[0]],
        )

    fbg_interleave = max(1, int(args.fbg_data_interleave))
    fbg_num_averages = max(1, int(args.fbg_num_averages))
    if (
        fbg_cfg.data_interleave != fbg_interleave
        or fbg_cfg.num_averages != fbg_num_averages
    ):
        fbg_cfg = InterrogatorSettings(
            ip_address=fbg_cfg.ip_address,
            port=fbg_cfg.port,
            data_interleave=fbg_interleave,
            num_averages=fbg_num_averages,
            ch_gains=list(fbg_cfg.ch_gains),
            ch_noise_thresholds=list(fbg_cfg.ch_noise_thresholds),
            sensors=list(fbg_cfg.sensors),
        )
    print(
        "[ExperimentPanel] FBG settings: "
        f"sensors={len(fbg_cfg.sensors)}, "
        f"data_interleave={fbg_cfg.data_interleave}, "
        f"num_averages={fbg_cfg.num_averages}"
    )
    print(f"[ExperimentPanel] Bota enabled: {enable_bota}")
    print(
        "[ExperimentPanel] Load-cell enabled: "
        f"{enable_loadcell}"
        + (
            f" (ch={args.loadcell_channel}, cal={args.loadcell_cal})"
            if enable_loadcell
            else ""
        )
    )
    print(f"[ExperimentPanel] Z axis inverted: {bool(args.invert_z_axis)}")
    print(f"[ExperimentPanel] Z total steps: {int(args.z_total_steps)}")

    controller = ExperimentController(
        output_dir=args.output_dir,
        bota_config_path=args.bota_config,
        bota_interface_override=args.bota_interface,
        fbg_interrogator_cfg=fbg_cfg,
        stage_module_id=args.stage_id,
        z_stage_module_id=args.z_stage_id,
        z_total_steps=args.z_total_steps,
        invert_z_axis=bool(args.invert_z_axis),
        enable_bota=enable_bota,
        enable_loadcell=enable_loadcell,
        loadcell_channel=args.loadcell_channel,
        loadcell_rate_hz=args.loadcell_rate,
        loadcell_interval_ms=args.loadcell_interval_ms,
        loadcell_gain=args.loadcell_gain,
        loadcell_avg_window=args.loadcell_avg_window,
        loadcell_serial=args.loadcell_serial,
        loadcell_calibration_path=args.loadcell_cal,
        loadcell_attach_timeout_s=args.loadcell_attach_timeout,
    )

    app = QtWidgets.QApplication(sys.argv)
    window = ExperimentPanel(
        controller,
        initial_stage_port=args.stage_port,
        initial_whisker_name=args.whisker_name,
    )
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
