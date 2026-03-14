from __future__ import annotations

import sys
from datetime import datetime
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd
from PyQt5 import QtCore, QtGui, QtWidgets

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
import pyqtgraph as pg
from scipy import signal

from .config import (
    InterrogatorSettings,
    PlotSettings,
    RecordingSettings,
    SpectrogramSettings,
)
from .streaming import FBGStreamReader


class LivePlotWindow(QtWidgets.QMainWindow):
    """Live plotting window with manual recording controls."""

    def __init__(
        self,
        reader: FBGStreamReader,
        plot_cfg: PlotSettings,
        interr_cfg: InterrogatorSettings,
        recording_cfg: RecordingSettings,
        parent: QtWidgets.QWidget | None = None,
        on_recording_started: Optional[Callable[["LivePlotWindow"], None]] = None,
        on_recording_finished: Optional[Callable[["LivePlotWindow", Optional[Path]], None]] = None,
        *,
        enable_spectrograms: bool = True,
    ) -> None:
        super().__init__(parent)
        self.reader = reader
        self.plot_cfg = plot_cfg
        self.interr_cfg = interr_cfg
        self.recording_cfg = recording_cfg
        self._on_recording_started = on_recording_started
        self._on_recording_finished = on_recording_finished
        self._enable_spectrograms = enable_spectrograms

        self.sensor_settings = interr_cfg.sensors
        self.sensor_names = [sensor.name for sensor in self.sensor_settings]
        if not self.sensor_names:
            raise ValueError("No sensors configured. Please add sensors to InterrogatorSettings.")

        self._init_ui()
        self._init_timer()

        QtWidgets.QApplication.instance().aboutToQuit.connect(self._on_app_about_to_quit)

        self.is_recording = False
        self.recording_start_time: datetime | None = None

    def _init_ui(self) -> None:
        self.setWindowTitle("FBG Live Plot - Press 'R' to record, 'S' to stop & save")
        self.resize(self.plot_cfg.window_size[0], self.plot_cfg.window_size[1])

        self._central_widget = pg.GraphicsLayoutWidget()
        self.setCentralWidget(self._central_widget)

        self._line_plots: List[pg.PlotItem] = []
        self._line_curves: List[pg.PlotDataItem] = []
        self._spec_high_items: List[pg.ImageItem] = []
        self._spec_wide_items: List[pg.ImageItem] = []
        self._hist_high: List[pg.HistogramLUTItem] = []
        self._hist_wide: List[pg.HistogramLUTItem] = []

        for sensor in self.sensor_settings:
            self._add_sensor_row(sensor.name, sensor.nominal_wavelength)

    def _add_sensor_row(self, sensor_name: str, nominal_wavelength: float) -> None:
        time_plot = self._central_widget.addPlot(title=f"{sensor_name} Time Series")
        curve = time_plot.plot(pen="y")
        self._line_plots.append(time_plot)
        self._line_curves.append(curve)

        if self.plot_cfg.plot_limit:
            y_min = nominal_wavelength - self.plot_cfg.vis_height_range
            y_max = nominal_wavelength + self.plot_cfg.vis_height_range
            time_plot.setYRange(y_min, y_max, padding=0.05)

        self._line_plots[-1].setLabel("left", "Wavelength (nm)")
        self._line_plots[-1].setLabel("bottom", "Time (s)")

        if self._enable_spectrograms:
            spec_high_plot = self._central_widget.addPlot(
                title=f"{sensor_name} High-Res (0-{self.plot_cfg.high_res.max_freq}Hz)"
            )
            spec_high = pg.ImageItem()
            spec_high_plot.addItem(spec_high)
            hist_high = pg.HistogramLUTItem()
            hist_high.setImageItem(spec_high)
            hist_high.gradient.restoreState(
                {
                    "mode": "rgb",
                    "ticks": [
                        (0.0, (75, 0, 113, 255)),
                        (0.5, (0, 182, 188, 255)),
                        (1.0, (246, 111, 0, 255)),
                    ],
                }
            )

            self._central_widget.addItem(hist_high)

            spec_wide_plot = self._central_widget.addPlot(
                title=f"{sensor_name} Wide-Range (0-{self.plot_cfg.wide_range.max_freq}Hz)"
            )
            spec_wide = pg.ImageItem()
            spec_wide_plot.addItem(spec_wide)
            hist_wide = pg.HistogramLUTItem()
            hist_wide.setImageItem(spec_wide)
            hist_wide.gradient.restoreState(
                {
                    "mode": "rgb",
                    "ticks": [
                        (0.0, (75, 0, 113, 255)),
                        (0.5, (0, 182, 188, 255)),
                        (1.0, (246, 111, 0, 255)),
                    ],
                }
            )
            self._central_widget.addItem(hist_wide)

            spec_high_plot.setLabel("bottom", "Time (s)")
            spec_high_plot.setLabel("left", "Frequency (Hz)")
            spec_wide_plot.setLabel("bottom", "Time (s)")
            spec_wide_plot.setLabel("left", "Frequency (Hz)")

            self._spec_high_items.append(spec_high)
            self._spec_wide_items.append(spec_wide)
            self._hist_high.append(hist_high)
            self._hist_wide.append(hist_wide)

        self._central_widget.nextRow()

    def _init_timer(self) -> None:
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._update_plots)
        self._timer.start(self.plot_cfg.update_interval_ms)

    def _update_plots(self) -> None:
        if not self.reader.is_ready:
            return

        timestamps, series = self.reader.snapshot()
        if timestamps.size == 0:
            return

        sample_rate = self.reader.sample_rate

        for idx, sensor_name in enumerate(self.sensor_names):
            data = series.get(sensor_name)
            if data is None or data.size == 0:
                continue
            self._line_curves[idx].setData(timestamps, data)

            if not self._enable_spectrograms:
                continue

            self._update_spectrogram(
                data=data,
                image_item=self._spec_high_items[idx],
                hist_item=self._hist_high[idx],
                config=self.plot_cfg.high_res,
                sample_rate=sample_rate,
            )
            self._update_spectrogram(
                data=data,
                image_item=self._spec_wide_items[idx],
                hist_item=self._hist_wide[idx],
                config=self.plot_cfg.wide_range,
                sample_rate=sample_rate,
            )

    def _update_spectrogram(
        self,
        *,
        data: np.ndarray,
        image_item: pg.ImageItem,
        hist_item: pg.HistogramLUTItem,
        config: SpectrogramSettings,
        sample_rate: float,
    ) -> None:
        nperseg = min(config.nperseg, data.size)
        if nperseg <= 8 or data.size <= nperseg:
            return

        noverlap = int(nperseg * config.noverlap_ratio)
        f_axis, t_axis, sxx = signal.spectrogram(
            data, fs=sample_rate, nperseg=nperseg, noverlap=noverlap
        )
        mask = f_axis <= config.max_freq
        f_axis = f_axis[mask]
        sxx = sxx[mask, :]

        sxx_db = 10.0 * np.log10(sxx + 1e-12)
        if not np.isfinite(sxx_db).any():
            return

        image_item.setImage(sxx_db.T, autoLevels=False)
        hist_item.setLevels(float(np.nanmin(sxx_db)), float(np.nanmax(sxx_db)))

        transform = QtGui.QTransform()
        if t_axis.size > 0 and f_axis.size > 0:
            transform.scale(
                t_axis[-1] / max(1, sxx.shape[1]),
                f_axis[-1] / max(1, sxx.shape[0]),
            )
        image_item.setTransform(transform)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_R:
            self._start_recording()
        elif event.key() == QtCore.Qt.Key_S:
            self._stop_and_save()
        else:
            super().keyPressEvent(event)

    def _start_recording(self) -> None:
        if self.is_recording:
            return
        self.reader.start_recording()
        self.is_recording = True
        self.recording_start_time = datetime.now()
        timestamp = self.recording_start_time.strftime("%Y-%m-%d %H:%M:%S")
        self.setWindowTitle(
            f"FBG Live Plot - RECORDING since {timestamp} - Press 'S' to stop & save"
        )
        print(f"[FBG] Recording started at {timestamp}")
        if self._on_recording_started:
            self._on_recording_started(self)

    def _stop_and_save(self) -> None:
        if not self.is_recording:
            return

        rows = self.reader.stop_recording()
        self.is_recording = False
        self.setWindowTitle("FBG Live Plot - Press 'R' to record, 'S' to stop & save")

        saved_path: Optional[Path] = None
        if not rows:
            print("[FBG] No samples captured during recording.")
        else:
            save_dir = self.recording_cfg.save_directory
            save_dir.mkdir(parents=True, exist_ok=True)

            timestamp = (self.recording_start_time or datetime.now()).strftime("%Y%m%d-%H%M%S")
            columns = ["time_seconds"] + self.sensor_names
            df = pd.DataFrame(rows, columns=columns)
            filename = f"{self.recording_cfg.file_prefix}_{timestamp}.csv"
            saved_path = save_dir / filename
            df.to_csv(saved_path, index=False)
            print(f"[FBG] Saved {len(df)} samples to {saved_path}")

        if self._on_recording_finished:
            self._on_recording_finished(self, saved_path)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.reader.stop()
        super().closeEvent(event)

    def _on_app_about_to_quit(self) -> None:
        self.reader.stop()

    def stop_recording(self) -> None:
        """Programmatically stop recording and finalize the dataset."""
        if self.is_recording:
            self._stop_and_save()

    def set_recording_directory(self, directory: Path) -> None:
        """Update the directory where future recordings will be saved."""
        self.recording_cfg.save_directory = directory


def create_application(argv: List[str] | None = None) -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(argv or sys.argv)
    return app
