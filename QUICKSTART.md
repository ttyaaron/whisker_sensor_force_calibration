# Linear Stage Quick Start Guide

## 🎯 Your Goal
Get the linear stages moving and understand the code structure.

---

## 📁 Code Structure

### 1. `stage_module.py` - **Core Stage Control**
- **What it does**: Controls individual linear stage motors
- **Key class**: `StageModuleControl`
- **Communication**: Binary protocol over serial (9600 baud)
- **Key methods**:
  - `home()` - Move stage to home position
  - `go_pos_mm(pos)` - Move to position in millimeters
  - `get_pos()` - Read current position in millimeters
  - `go_pos(pos)` - Move to position in steps

**Step size**: 0.000047625 mm/step (very precise!)

---

### 2. `calib_manager.py` - **3-Axis Manager**
- **What it does**: Manages 3 stages together (X, Y, Z)
- **Key class**: `WhiskerCalibrationManager`
- **Creates**: 3 stage objects (sx, sy, sz) with IDs 1, 2, 3
- **Safety**: Clips positions to safe ranges:
  - X & Y: 0-100 mm
  - Z: 0-50 mm

**Key methods**:
- `get_pos()` - Returns [x, y, z] position array
- `goto_pos_mm(p, order)` - Move to position with specified axis order
- `goto_origin()` - Go to predefined origin
- `goto_reset_all()` - Go to safe reset position

**Why order matters**: Axes move one at a time to avoid collisions!
Example: `order=['z','y','x']` moves Z first, then Y, then X

---

### 3. `calibrate.py` - **Main Application**
- **What it does**: Full calibration workflow with sensor data
- **Modes**:
  - `print`: Check positions interactively
  - `move`: Move along a generated path
  - `gather_calib_pts`: Collect calibration data points

**Requires**: 
- Hydra config files (not present yet)
- Sensor data via shared memory (optional with `--no-sensor`)

---

### 4. `utils.py` - **Path Generators**
- `gen_path_cartesian()` - Generate linear paths in XYZ
- `gen_path_spherical()` - Generate paths in spherical coordinates

---

## 🚀 Getting Started (Step-by-Step)

### **Step 1**: Find Your COM Port
```powershell
python test_stages_windows.py
```
This will list all available ports and auto-detect your stage controller.

### **Step 2**: Test Connection
The script will:
1. ✓ Connect to the COM port
2. ✓ Read current positions of all 3 axes
3. ✓ (Optional) Move 1mm and return

### **Step 3**: Manual Control
Use the interactive jog mode:
- `x+`, `x-` - Move X axis ±1mm
- `y+`, `y-` - Move Y axis ±1mm  
- `z+`, `z-` - Move Z axis ±1mm
- `p` - Print current position
- `q` - Quit

---

## ⚠️ Important Notes

### Serial Port Issues
Your current code uses `/dev/ttyUSB0` (Linux), but you're on Windows.

**Fix needed in `calib_manager.py` line 11:**
```python
# Change from:
ser = serial.Serial("/dev/ttyUSB0", 9600)

# To (Windows):
ser = serial.Serial("COM3", 9600)  # Use your actual COM port
```

### Import Path Issues
The `calibrate.py` imports may need adjustment:
```python
# Current (line 8):
from stage_control.calib_manager import WhiskerCalibrationManager

# If you get import errors, change to:
from calib_manager import WhiskerCalibrationManager
from utils import gen_path_cartesian, gen_path_spherical
```

### Config Files Missing
`calibrate.py` expects a `config/config.yaml` file for Hydra.
Not needed for basic stage testing - use `test_stages_windows.py` first!

---

## 🔧 Quick Testing Workflow

### Option A: Simple Test (Recommended First)
```powershell
python test_stages_windows.py
```
**Pros**: 
- No config files needed
- Step-by-step testing
- Interactive jog mode
- Windows-compatible

### Option B: Using calibrate.py
You'll need to:
1. Create config folder and YAML files
2. Fix serial port to COM port
3. Use `--no-sensor` flag if no sensor connected

---

## 📊 Understanding Movement

### Position Units
- **millimeters (mm)**: Human-readable, used in high-level functions
- **steps**: Low-level motor units (1 step = 0.000047625 mm)

### Movement Order
Always specify order to prevent collisions:
```python
# Safe: Move Z up first, then Y, then X
manager.goto_pos_mm([10, 20, 15], order=['z','y','x'])

# Risky: X first might collide with something
manager.goto_pos_mm([10, 20, 15], order=['x','y','z'])
```

### Why wait=True?
```python
stage.go_pos_mm(50.0, wait=True)  # Waits for movement to complete
stage.go_pos_mm(50.0, wait=False) # Returns immediately (asynchronous)
```

---

## 🎓 Quick Code Reading Tips

1. **Start from high-level to low-level**:
   - `test_stages_windows.py` (simplest)
   - `calib_manager.py` (medium)
   - `stage_module.py` (lowest level)

2. **Look for these patterns**:
   - `ser.write()` - Sending commands
   - `ser.read(6)` - Reading 6-byte responses
   - `struct.pack/unpack` - Binary data encoding/decoding

3. **Key variables to watch**:
   - `self.id` - Stage ID (1, 2, or 3)
   - `step_size` - Conversion factor (mm to steps)
   - `pos_step` - Position in motor steps

---

## ✅ Next Steps Checklist

- [ ] Run `test_stages_windows.py` to find COM port
- [ ] Test basic connection
- [ ] Read current positions
- [ ] Try small movement (1mm)
- [ ] Use jog mode to familiarize yourself
- [ ] Read [stage_module.py](stage_module.py) line-by-line
- [ ] Read [calib_manager.py](calib_manager.py) to understand 3-axis control
- [ ] (Optional) Create config files for `calibrate.py`

---

## 🐛 Troubleshooting

**Problem**: No COM ports found
- Check USB connection
- Install drivers for your stage controller
- Check Device Manager (Windows)

**Problem**: Connection timeout
- Wrong COM port - try others from the list
- Wrong baud rate - should be 9600
- Another program using the port - close it

**Problem**: Stage doesn't move
- Check if stage is powered on
- Try homing first: `stage.home()`
- Check position limits (0-100mm for X/Y, 0-50mm for Z)

**Problem**: Position reads wrong
- Add delays between commands: `time.sleep(0.1)`
- Check stage ID (1, 2, or 3)
- Verify step_size is correct

---

## 📖 Further Reading

After basic testing works:
1. Study binary protocol in `stage_module.py`
2. Understand clipping in `calib_manager.py`  
3. Read path generation in `utils.py`
4. Set up config files for full calibration

**Most important**: Start simple with `test_stages_windows.py`!
