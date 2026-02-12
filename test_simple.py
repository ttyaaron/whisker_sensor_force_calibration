#!/usr/bin/env python3
"""
Simplified test - keeps serial port open throughout
"""
import serial
import serial.tools.list_ports
import time
from stage_module import StageModuleControl

def find_ports():
    """Find available COM ports"""
    ports = serial.tools.list_ports.comports()
    print("\n=== Available COM Ports ===")
    for p in ports:
        print(f"  {p.device}: {p.description}")
    return [p.device for p in ports]

def main():
    print("="*60)
    print("  SIMPLE LINEAR STAGE TEST")
    print("="*60)
    
    # Find ports
    ports = find_ports()
    if not ports:
        print("\n✗ No COM ports found!")
        return
    
    # Select port
    default_port = ports[0]
    port_input = input(f"\nEnter port (default: {default_port}): ").strip()
    port = port_input if port_input else default_port
    
    print(f"\n→ Using {port}")
    print("\n" + "="*60)
    
    # Open serial connection ONCE
    try:
        print(f"\nOpening {port}...")
        ser = serial.Serial(port, 9600, timeout=2)
        print(f"✓ Connected successfully!")
        time.sleep(0.5)
        
        # Create stage controllers
        print("\nCreating stage controllers...")
        sx = StageModuleControl(ser, 1, step_size=0.000047625)
        sy = StageModuleControl(ser, 2, step_size=0.000047625)
        sz = StageModuleControl(ser, 3, step_size=0.000047625)
        print("✓ All stages initialized")
        
        # Read positions
        print("\n=== Reading Positions ===")
        pos_x = sx.get_pos()
        time.sleep(0.1)
        pos_y = sy.get_pos()
        time.sleep(0.1)
        pos_z = sz.get_pos()
        
        print(f"X-axis: {pos_x:.3f} mm")
        print(f"Y-axis: {pos_y:.3f} mm")
        print(f"Z-axis: {pos_z:.3f} mm")
        
        # Ask about movement
        print("\n" + "="*60)
        response = input("\nTest movement? (y/n): ").strip().lower()
        
        if response == 'y':
            print("\n=== Testing Small Movement ===")
            start = sx.get_pos()
            print(f"Starting X position: {start:.3f} mm")
            
            # Move 1mm
            target = start + 1.0
            print(f"Moving to {target:.3f} mm...")
            sx.go_pos_mm(target, wait=True)
            time.sleep(0.2)
            
            new = sx.get_pos()
            print(f"✓ New position: {new:.3f} mm")
            
            # Return
            time.sleep(0.5)
            print(f"Returning to {start:.3f} mm...")
            sx.go_pos_mm(start, wait=True)
            time.sleep(0.2)
            
            final = sx.get_pos()
            print(f"✓ Final position: {final:.3f} mm")
        
        # Interactive mode
        print("\n" + "="*60)
        response = input("\nEnter interactive mode? (y/n): ").strip().lower()
        
        if response == 'y':
            print("\n=== Interactive Mode ===")
            print("Commands: x+, x-, y+, y-, z+, z-, p, q")
            
            while True:
                cmd = input("\n> ").strip().lower()
                
                if cmd == 'q':
                    break
                elif cmd == 'p':
                    px = sx.get_pos()
                    py = sy.get_pos()
                    pz = sz.get_pos()
                    print(f"Position: X={px:.2f}, Y={py:.2f}, Z={pz:.2f} mm")
                elif cmd == 'x+':
                    p = sx.get_pos()
                    sx.go_pos_mm(p + 1.0, wait=True)
                    print(f"X: {sx.get_pos():.2f} mm")
                elif cmd == 'x-':
                    p = sx.get_pos()
                    sx.go_pos_mm(p - 1.0, wait=True)
                    print(f"X: {sx.get_pos():.2f} mm")
                elif cmd == 'y+':
                    p = sy.get_pos()
                    sy.go_pos_mm(p + 1.0, wait=True)
                    print(f"Y: {sy.get_pos():.2f} mm")
                elif cmd == 'y-':
                    p = sy.get_pos()
                    sy.go_pos_mm(p - 1.0, wait=True)
                    print(f"Y: {sy.get_pos():.2f} mm")
                elif cmd == 'z+':
                    p = sz.get_pos()
                    sz.go_pos_mm(p + 1.0, wait=True)
                    print(f"Z: {sz.get_pos():.2f} mm")
                elif cmd == 'z-':
                    p = sz.get_pos()
                    sz.go_pos_mm(p - 1.0, wait=True)
                    print(f"Z: {sz.get_pos():.2f} mm")
                else:
                    print("Unknown command")
        
        # Clean up
        print("\nClosing connection...")
        ser.close()
        print("✓ Done!")
        
    except serial.SerialException as e:
        print(f"\n✗ Serial error: {e}")
        if "PermissionError" in str(e):
            print("\n→ Port is busy. Try:")
            print("  1. Wait 5 seconds and run again")
            print("  2. Unplug/replug USB")
            print("  3. Restart computer")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*60)

if __name__ == "__main__":
    main()
