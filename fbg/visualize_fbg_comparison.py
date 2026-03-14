#!/usr/bin/env python3
"""
Real-time FBG1 vs FBG2 Comparison Visualization
Displays both FBG sensors on the same plot for direct comparison
"""
import sys
import os
from pathlib import Path

# Add parent directory to path for imports
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))

import numpy as np
from collections import deque
from PyQt5 import QtWidgets, QtCore
import pyqtgraph as pg

# Direct imports to avoid module-level initialization issues
from fbg.streaming import FBGStreamReader
from fbg.config import DEFAULT_CONFIG


class FBGComparisonWindow(QtWidgets.QMainWindow):
    """Live comparison plot showing FBG1 and FBG2 together."""
    
    def __init__(self, reader: FBGStreamReader):
        super().__init__()
        self.reader = reader
        self.history_seconds = 10.0
        self.max_points = int(2000 * self.history_seconds)
        
        # Data buffers
        self.time_data = deque(maxlen=self.max_points)
        self.fbg1_data = deque(maxlen=self.max_points)
        self.fbg2_data = deque(maxlen=self.max_points)
        
        self._init_ui()
        self._init_timer()
        
    def _init_ui(self):
        self.setWindowTitle("FBG1 and FBG2 - Live Plot")
        self.resize(1200, 800)
        
        # Create central widget with layout
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        
        # Create graphics layout widget
        self.graphics_widget = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics_widget)
        
        # Create FBG1 plot
        self.fbg1_plot = self.graphics_widget.addPlot(
            title="FBG1 Wavelength",
            row=0, col=0
        )
        self.fbg1_plot.setLabel('left', 'Wavelength', units='nm')
        self.fbg1_plot.setLabel('bottom', 'Time', units='s')
        self.fbg1_plot.showGrid(x=True, y=True, alpha=0.3)
        self.fbg1_individual = self.fbg1_plot.plot(
            pen=pg.mkPen(color='r', width=2)
        )
        
        # Create FBG2 plot
        self.graphics_widget.nextRow()
        self.fbg2_plot = self.graphics_widget.addPlot(
            title="FBG2 Wavelength",
            row=1, col=0
        )
        self.fbg2_plot.setLabel('left', 'Wavelength', units='nm')
        self.fbg2_plot.setLabel('bottom', 'Time', units='s')
        self.fbg2_plot.showGrid(x=True, y=True, alpha=0.3)
        self.fbg2_individual = self.fbg2_plot.plot(
            pen=pg.mkPen(color='b', width=2)
        )
        
        # Add status bar
        self.statusBar().showMessage("Waiting for data...")
        
    def _init_timer(self):
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._update_plots)
        self.timer.start(20)  # 50 Hz update rate
        
    def _update_plots(self):
        # Get latest data from reader
        time_arr, data_dict = self.reader.snapshot()
        
        if len(time_arr) == 0:
            return
            
        # Extract FBG1 and FBG2 data
        fbg1_arr = data_dict.get('fbg_1', np.array([]))
        fbg2_arr = data_dict.get('fbg_2', np.array([]))
        
        if len(fbg1_arr) == 0 or len(fbg2_arr) == 0:
            return
        
        # Make time relative
        if len(time_arr) > 0:
            time_arr = time_arr - time_arr[0]
        
        # Update plots
        self.fbg1_individual.setData(time_arr, fbg1_arr)
        self.fbg2_individual.setData(time_arr, fbg2_arr)
        
        # Update status
        rate = len(time_arr) / max(time_arr[-1] - time_arr[0], 0.001) if len(time_arr) > 1 else 0
        fbg1_latest = fbg1_arr[-1] if len(fbg1_arr) > 0 else 0
        fbg2_latest = fbg2_arr[-1] if len(fbg2_arr) > 0 else 0
        
        status = (
            f"Rate: {rate:.1f} Hz | "
            f"FBG1: {fbg1_latest:.6f} nm | "
            f"FBG2: {fbg2_latest:.6f} nm | "
            f"Points: {len(time_arr)}"
        )
        self.statusBar().showMessage(status)


def main():
    """Launch FBG comparison visualization."""
    app = QtWidgets.QApplication(sys.argv)
    
    # Create and start reader
    reader = FBGStreamReader(
        DEFAULT_CONFIG.interrogator,
        history_seconds=10.0
    )
    reader.start()
    
    # Create and show window
    window = FBGComparisonWindow(reader)
    window.show()
    
    # Wait for connection
    print("Waiting for interrogator connection...")
    if reader.wait_until_ready(timeout=5.0):
        print("Connected to interrogator!")
    else:
        print("Warning: Connection timeout - check interrogator IP and connection")
    
    # Run application
    exit_code = app.exec_()
    
    # Cleanup
    reader.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
