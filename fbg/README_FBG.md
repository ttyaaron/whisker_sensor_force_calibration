# FBG Sensor Visualization

Real-time visualization for FBG (Fiber Bragg Grating) sensors.

## Quick Start

### FBG1 vs FBG2 Comparison View

Compare FBG1 and FBG2 signals in real-time:

```bash
# Activate your conda environment first
conda activate whisker

# Run the comparison visualization
./run_fbg_comparison.sh

# Or run directly:
python fbg/visualize_fbg_comparison.py
```

### Default Live Plot (All Sensors)

Run the full live plotting interface with spectrograms:

```bash
conda activate whisker
python -m fbg.app
```

## Features

### Comparison View (`visualize_fbg_comparison.py`)
- **Combined Plot**: FBG1 and FBG2 overlaid for direct comparison
- **Individual Plots**: Separate views for each sensor
- **Difference Plot**: Real-time FBG1 - FBG2 difference
- **Live Statistics**: Sample rate, latest values, and difference
- **10-second history** by default

### Full Live Plot (`app.py`)
- Time series for each sensor
- High-resolution spectrogram (0-25 Hz)
- Wide-range spectrogram (0-200 Hz)
- Manual recording with 'R' key
- Save data with 'S' key

## Configuration

Default settings in `config.py`:
- **Interrogator IP**: 10.0.0.126
- **Port**: 1852
- **Sensors**: fbg_1 (position 0), fbg_2 (position 1)
- **Sample Rate**: ~2000 Hz (hardware dependent)

### Custom Configuration

Create a YAML config file:

```yaml
interrogator:
  ip_address: "10.0.0.126"
  port: 1852
  sensors:
    - name: "fbg_1"
      position: 0
      sensor_type: "strain"
      nominal_wavelength: -14.580973
    - name: "fbg_2"
      position: 1
      sensor_type: "strain"
      nominal_wavelength: -9.541548

plot:
  history_seconds: 10.0
  window_size: [1200, 800]
  update_interval_ms: 20

recording:
  save_directory: "./data"
  file_prefix: "whisker"
```

Then run with:
```bash
python -m fbg.app --config my_config.yaml
```

## Dependencies

Required packages:
- numpy
- pandas
- PyQt5
- pyqtgraph
- scipy
- pyyaml

Install with:
```bash
conda install numpy pandas pyqt pyqtgraph scipy pyyaml
```

## Troubleshooting

### Connection Issues

If you see "Connection timeout":
1. Check interrogator is powered on
2. Verify IP address: `ping 10.0.0.126`
3. Ensure network cable is connected
4. Check firewall settings

### No Data Appearing

1. Verify sensors are configured correctly
2. Check sensor positions match hardware
3. Ensure interrogator is in correct operating mode

### Low Frame Rate

1. Reduce `history_seconds` in config
2. Increase `update_interval_ms`
3. Disable spectrograms if not needed

## File Structure

```
fbg/
├── __init__.py
├── app.py                        # Main application
├── config.py                     # Configuration
├── interrogator.py               # Hardware interface
├── plotting.py                   # Full plotting window
├── sensor.py                     # Sensor data model
├── streaming.py                  # Background data reader
├── visualize_fbg_comparison.py  # FBG1 vs FBG2 comparison
└── utils/
    ├── anime.py                  # Animation utilities
    └── example_usage.py          # Usage examples
```

## Notes

- Default configuration shows fbg_1 and fbg_2
- Both sensors stream at ~2000 Hz (hardware dependent)
- Data is buffered for 10 seconds by default
- Recording saves to `./data/` directory with timestamp
