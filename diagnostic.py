#!/usr/bin/env python3
"""
Diagnostic tool to check serial communication with stages
"""
import serial
import serial.tools.list_ports
import time
import struct

def find_ports():
    """Find available COM ports"""
    ports = serial.tools.list_ports.comports()
    print("\n=== Available COM Ports ===")
    for p in ports:
        print(f"  {p.device}: {p.description}")
    return [p.device for p in ports]

def test_raw_communication(port):
    """Test raw serial communication"""
    print(f"\n{'='*60}")
    print("RAW COMMUNICATION TEST")
    print('='*60)
    
    try:
        ser = serial.Serial(port, 9600, timeout=2)
        print(f"✓ Connected to {port}\n")
        
        # Flush any old data
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(0.2)
        
        # Test each stage ID
        for stage_id in [1, 2, 3]:
            print(f"\n--- Testing Stage ID {stage_id} (Get Position) ---")
            
            # Build message: ID=stage_id, Command=60 (get position), Data=0
            msg = struct.pack('BB', stage_id, 60) + struct.pack('<I', 0)
            print(f"Sending: {msg.hex()}")
            
            # Clear buffer before sending
            ser.reset_input_buffer()
            
            # Send command
            ser.write(msg)
            
            # Wait for response
            timeout = time.time() + 2.0
            while ser.in_waiting < 6 and time.time() < timeout:
                time.sleep(0.01)
            
            if ser.in_waiting >= 6:
                response = ser.read(6)
                print(f"Received: {response.hex()}")
                
                # Parse response
                recv_id, recv_cmd, recv_val = struct.unpack('<BBI', response)
                print(f"  ID: {recv_id} (expected {stage_id})")
                print(f"  Command: {recv_cmd}")
                print(f"  Value: {recv_val}")
                print(f"  Position: {recv_val * 0.000047625:.3f} mm")
                
                if recv_id == stage_id:
                    print(f"  ✓ ID matches!")
                else:
                    print(f"  ✗ ID mismatch! Expected {stage_id}, got {recv_id}")
            else:
                print(f"  ✗ Timeout - no response received")
                print(f"  Bytes available: {ser.in_waiting}")
            
            time.sleep(0.2)
        
        # Check if there's extra data in buffer
        time.sleep(0.5)
        if ser.in_waiting > 0:
            print(f"\n⚠ Warning: {ser.in_waiting} extra bytes in buffer")
            extra = ser.read(ser.in_waiting)
            print(f"Extra data: {extra.hex()}")
        
        ser.close()
        print("\n" + "="*60)
        print("✓ Diagnostic complete")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()

def test_with_delays(port):
    """Test communication with various delays"""
    print(f"\n{'='*60}")
    print("TIMING TEST")
    print('='*60)
    
    try:
        ser = serial.Serial(port, 9600, timeout=2)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        for delay in [0.05, 0.1, 0.2, 0.5]:
            print(f"\n--- Testing with {delay}s delay between reads ---")
            
            positions = []
            for stage_id in [1, 2, 3]:
                msg = struct.pack('BB', stage_id, 60) + struct.pack('<I', 0)
                ser.reset_input_buffer()
                ser.write(msg)
                
                # Wait for response
                timeout = time.time() + 2.0
                while ser.in_waiting < 6 and time.time() < timeout:
                    time.sleep(0.01)
                
                if ser.in_waiting >= 6:
                    response = ser.read(6)
                    recv_id, recv_cmd, recv_val = struct.unpack('<BBI', response)
                    pos = recv_val * 0.000047625
                    positions.append(f"ID{stage_id}:{pos:.1f}mm")
                    
                    if recv_id != stage_id:
                        print(f"  ✗ ID mismatch for stage {stage_id}")
                else:
                    positions.append(f"ID{stage_id}:TIMEOUT")
                
                time.sleep(delay)
            
            print(f"  Results: {' | '.join(positions)}")
        
        ser.close()
        print("\n" + "="*60)
        
    except Exception as e:
        print(f"\n✗ Error: {e}")

def main():
    print("="*60)
    print("  LINEAR STAGE DIAGNOSTIC TOOL")
    print("="*60)
    
    ports = find_ports()
    if not ports:
        print("\n✗ No COM ports found!")
        return
    
    default_port = ports[0]
    port_input = input(f"\nEnter port (default: {default_port}): ").strip()
    port = port_input if port_input else default_port
    
    # Run diagnostics
    test_raw_communication(port)
    
    response = input("\nRun timing test? (y/n): ").strip().lower()
    if response == 'y':
        test_with_delays(port)
    
    print("\n" + "="*60)
    print("RECOMMENDATIONS:")
    print("="*60)
    print("1. If all IDs match: Your hardware is working correctly")
    print("2. If IDs mismatch: There may be crosstalk or buffer issues")
    print("3. If timeouts occur: Check cable connections and power")
    print("4. Note the delay that works best for reliable communication")
    print("="*60)

if __name__ == "__main__":
    main()
