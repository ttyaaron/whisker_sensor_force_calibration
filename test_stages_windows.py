#!/usr/bin/env python3
"""
Windows version - Simple test script for linear stages
"""
import serial
import serial.tools.list_ports
import time
from stage_module import StageModuleControl
from calib_manager import WhiskerCalibrationManager
import numpy as np

def find_serial_ports():
    """List all available COM ports"""
    ports = serial.tools.list_ports.comports()
    return [(port.device, port.description) for port in ports]

def test_0_list_ports():
    """Test 0: List all available COM ports"""
    print("\n=== Test 0: Available COM Ports ===")
    ports = find_serial_ports()
    if ports:
        print(f"Found {len(ports)} port(s):")
        for device, description in ports:
            print(f"  - {device}: {description}")
        return ports[0][0] if ports else None
    else:
        print("✗ No COM ports found!")
        print("  Tip: Make sure your linear stage controller is connected via USB")
        return None

def test_1_basic_connection(port="COM3"):
    """Test 1: Check if we can connect to serial port"""
    print(f"\n=== Test 1: Serial Connection to {port} ===")
    try:
        ser = serial.Serial(port, 9600, timeout=2)
        print(f"✓ Connected to {port}")
        print(f"  Port: {ser.port}, Baudrate: {ser.baudrate}")
        ser.close()
        time.sleep(0.5)  # Give Windows time to release the port
        return True
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        print(f"  Tip: Try a different port from the list above")
        return False

def test_2_read_positions(port="COM3"):
    """Test 2: Read current positions of all axes"""
    print(f"\n=== Test 2: Read Current Positions ===")
    try:
        ser = serial.Serial(port, 9600, timeout=2)
        
        # Create stage objects
        sx = StageModuleControl(ser, 1, step_size=0.000047625)
        sy = StageModuleControl(ser, 2, step_size=0.000047625)
        sz = StageModuleControl(ser, 3, step_size=0.000047625)
        
        # Read positions
        print("Reading positions...")
        pos_x = sx.get_pos()
        time.sleep(0.1)
        pos_y = sy.get_pos()
        time.sleep(0.1)
        pos_z = sz.get_pos()
        
        print(f"✓ X-axis (ID 1): {pos_x:.3f} mm")
        print(f"✓ Y-axis (ID 2): {pos_y:.3f} mm")
        print(f"✓ Z-axis (ID 3): {pos_z:.3f} mm")
        
        ser.close()
        time.sleep(0.5)  # Give Windows time to release the port
        return True
    except serial.SerialException as e:
        print(f"✗ Failed to read positions: {e}")
        if "PermissionError" in str(e) or "Access is denied" in str(e):
            print("  → Port is in use. Try these:")
            print("     1. Wait 5 seconds and run the script again")
            print("     2. Unplug and replug the USB cable")
            print("     3. Close any other programs using the port")
        return False
    except Exception as e:
        print(f"✗ Failed to read positions: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_3_small_move(port="COM3"):
    """Test 3: Make a small movement on X-axis"""
    print("\n=== Test 3: Small Movement Test ===")
    try:
        ser = serial.Serial(port, 9600, timeout=2)
        sx = StageModuleControl(ser, 1, step_size=0.000047625)
        
        # Get current position
        start_pos = sx.get_pos()
        print(f"Starting X position: {start_pos:.3f} mm")
        
        # Move 1mm forward
        target = start_pos + 1.0
        print(f"Moving to {target:.3f} mm...")
        sx.go_pos_mm(target, wait=True)
        time.sleep(0.2)
        
        # Check new position
        new_pos = sx.get_pos()
        print(f"✓ New position: {new_pos:.3f} mm")
        print(f"  Movement: {(new_pos - start_pos):.3f} mm")
        
        # Move back
        time.sleep(0.5)
        print(f"Moving back to {start_pos:.3f} mm...")
        sx.go_pos_mm(start_pos, wait=True)
        time.sleep(0.2)
        final_pos = sx.get_pos()
        print(f"✓ Final position: {final_pos:.3f} mm")
        
        ser.close()
        time.sleep(0.5)  # Give Windows time to release the port
        return True
    except Exception as e:
        print(f"✗ Movement failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def manual_jog(port="COM3"):
    """Interactive jogging mode - move stages manually"""
    print("\n=== Manual Jog Mode ===")
    print("Commands: x+, x-, y+, y-, z+, z-, p (print pos), q (quit)")
    print("Step size: 1mm")
    
    try:
        ser = serial.Serial(port, 9600, timeout=2)
        sx = StageModuleControl(ser, 1, step_size=0.000047625)
        sy = StageModuleControl(ser, 2, step_size=0.000047625)
        sz = StageModuleControl(ser, 3, step_size=0.000047625)
        
        while True:
            cmd = input("\nCommand: ").strip().lower()
            
            if cmd == 'q':
                break
            elif cmd == 'p':
                pos_x = sx.get_pos()
                pos_y = sy.get_pos()
                pos_z = sz.get_pos()
                print(f"Position: X={pos_x:.2f}, Y={pos_y:.2f}, Z={pos_z:.2f} mm")
            elif cmd == 'x+':
                pos = sx.get_pos()
                sx.go_pos_mm(pos + 1.0, wait=True)
                print(f"Moved to X={sx.get_pos():.2f}")
            elif cmd == 'x-':
                pos = sx.get_pos()
                sx.go_pos_mm(pos - 1.0, wait=True)
                print(f"Moved to X={sx.get_pos():.2f}")
            elif cmd == 'y+':
                pos = sy.get_pos()
                sy.go_pos_mm(pos + 1.0, wait=True)
                print(f"Moved to Y={sy.get_pos():.2f}")
            elif cmd == 'y-':
                pos = sy.get_pos()
                sy.go_pos_mm(pos - 1.0, wait=True)
                print(f"Moved to Y={sy.get_pos():.2f}")
            elif cmd == 'z+':
                pos = sz.get_pos()
                sz.go_pos_mm(pos + 1.0, wait=True)
                print(f"Moved to Z={sz.get_pos():.2f}")
            elif cmd == 'z-':
                pos = sz.get_pos()
                sz.go_pos_mm(pos - 1.0, wait=True)
                print(f"Moved to Z={sz.get_pos():.2f}")
            else:
                print("Unknown command!")
        
        ser.close()
        time.sleep(0.5)  # Give Windows time to release the port
        print("Exiting jog mode")
        
    except Exception as e:
        print(f"✗ Jog mode failed: {e}")
        import traceback
        traceback.print_exc()

def main():
    """Run all tests in sequence"""
    print("=" * 60)
    print("  LINEAR STAGE TEST SUITE - Windows Version")
    print("=" * 60)
    
    # First, find available ports
    default_port = test_0_list_ports()
    
    if default_port is None:
        print("\n⚠ No COM ports detected. Cannot continue.")
        return
    
    # Ask user which port to use
    print(f"\nDefault port detected: {default_port}")
    port_input = input(f"Press Enter to use {default_port}, or type a different port: ").strip()
    port = port_input if port_input else default_port
    
    print(f"\n→ Using port: {port}")
    
    # Run tests
    print("\n" + "="*60)
    
    if not test_1_basic_connection(port):
        print("\n⚠ Cannot connect to port. Exiting.")
        return
    
    time.sleep(1.0)  # Extra delay to ensure port is fully released
    
    if not test_2_read_positions(port):
        print("\n⚠ Cannot read positions. Check your controller.")
        return
    
    time.sleep(1.0)  # Extra delay between tests
    
    # Ask before moving
    print("\n" + "="*60)
    response = input("\n📋 Tests passed! Try moving stages? (y/n): ").strip().lower()
    if response == 'y':
        test_3_small_move(port)
        
        response2 = input("\n📋 Enter manual jog mode? (y/n): ").strip().lower()
        if response2 == 'y':
            manual_jog(port)
    
    print("\n" + "="*60)
    print("✓ Testing complete!")
    print("="*60)

if __name__ == "__main__":
    main()
