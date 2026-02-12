#!/usr/bin/env python3
"""
Simple test script for linear stages - Start here!
"""
import serial
import time
from stage_control.stage_module import StageModuleControl
from stage_control.calib_manager import WhiskerCalibrationManager
import numpy as np

def test_1_basic_connection():
    """Test 1: Check if we can connect to serial port"""
    print("\n=== Test 1: Serial Connection ===")
    try:
        ser = serial.Serial("/dev/ttyUSB0", 9600, timeout=2)
        print(f"✓ Connected to /dev/ttyUSB0")
        print(f"  Port: {ser.port}, Baudrate: {ser.baudrate}")
        ser.close()
        return True
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        print("  Tip: Check if /dev/ttyUSB0 exists or try a different port (COM port on Windows)")
        return False

def test_2_read_positions():
    """Test 2: Read current positions of all axes"""
    print("\n=== Test 2: Read Current Positions ===")
    try:
        ser = serial.Serial("/dev/ttyUSB0", 9600, timeout=2)
        
        # Create stage objects
        sx = StageModuleControl(ser, 1, step_size=0.000047625)
        sy = StageModuleControl(ser, 2, step_size=0.000047625)
        sz = StageModuleControl(ser, 3, step_size=0.000047625)
        
        # Read positions
        pos_x = sx.get_pos()
        pos_y = sy.get_pos()
        pos_z = sz.get_pos()
        
        print(f"✓ X-axis: {pos_x:.3f} mm")
        print(f"✓ Y-axis: {pos_y:.3f} mm")
        print(f"✓ Z-axis: {pos_z:.3f} mm")
        
        ser.close()
        return True
    except Exception as e:
        print(f"✗ Failed to read positions: {e}")
        return False

def test_3_small_move():
    """Test 3: Make a small movement on X-axis"""
    print("\n=== Test 3: Small Movement Test ===")
    try:
        ser = serial.Serial("/dev/ttyUSB0", 9600, timeout=2)
        sx = StageModuleControl(ser, 1, step_size=0.000047625)
        
        # Get current position
        start_pos = sx.get_pos()
        print(f"Starting X position: {start_pos:.3f} mm")
        
        # Move 1mm forward
        target = start_pos + 1.0
        print(f"Moving to {target:.3f} mm...")
        sx.go_pos_mm(target, wait=True)
        
        # Check new position
        new_pos = sx.get_pos()
        print(f"✓ New position: {new_pos:.3f} mm")
        print(f"  Movement: {(new_pos - start_pos):.3f} mm")
        
        # Move back
        time.sleep(0.5)
        print(f"Moving back to {start_pos:.3f} mm...")
        sx.go_pos_mm(start_pos, wait=True)
        final_pos = sx.get_pos()
        print(f"✓ Final position: {final_pos:.3f} mm")
        
        ser.close()
        return True
    except Exception as e:
        print(f"✗ Movement failed: {e}")
        return False

def test_4_manager():
    """Test 4: Use WhiskerCalibrationManager"""
    print("\n=== Test 4: Calibration Manager Test ===")
    try:
        # Define safe positions (adjust these to your setup!)
        reset_pos = np.array([50.0, 50.0, 25.0])   # Safe middle position
        origin_pos = np.array([45.0, 45.0, 20.0])  # Origin position
        
        print(f"Reset position: {reset_pos}")
        print(f"Origin position: {origin_pos}")
        
        # Create manager (do_home=False for first test)
        manager = WhiskerCalibrationManager(
            reset_pos=reset_pos,
            origin_pos=origin_pos,
            do_home=False
        )
        
        # Get current position
        current = manager.get_pos()
        print(f"✓ Current position: X={current[0]:.2f}, Y={current[1]:.2f}, Z={current[2]:.2f} mm")
        
        # Try moving to a safe position
        test_pos = np.array([50.0, 50.0, 25.0])
        print(f"\nMoving to test position: {test_pos}")
        manager.goto_pos_mm(test_pos, order=['z','y','x'])
        
        final = manager.get_pos()
        print(f"✓ Final position: X={final[0]:.2f}, Y={final[1]:.2f}, Z={final[2]:.2f} mm")
        
        return True
    except Exception as e:
        print(f"✗ Manager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests in sequence"""
    print("=" * 50)
    print("LINEAR STAGE TEST SUITE")
    print("=" * 50)
    
    # Run tests
    tests = [
        test_1_basic_connection,
        test_2_read_positions,
        # test_3_small_move,  # Uncomment when ready
        # test_4_manager,      # Uncomment when ready
    ]
    
    results = []
    for test_func in tests:
        result = test_func()
        results.append(result)
        if not result:
            print("\n⚠ Test failed. Fix this before continuing to next test.")
            break
        time.sleep(0.5)
    
    # Summary
    print("\n" + "=" * 50)
    print(f"SUMMARY: {sum(results)}/{len(results)} tests passed")
    print("=" * 50)
    
    if all(results):
        print("\n✓ All tests passed! Your stages are ready.")
        print("\nNext steps:")
        print("  1. Uncomment test_3_small_move in main()")
        print("  2. Run test_3 to verify movement")
        print("  3. Uncomment test_4_manager to test full manager")
    else:
        print("\n⚠ Some tests failed. Check connections and settings.")

if __name__ == "__main__":
    main()
