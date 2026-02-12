#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from collections import defaultdict
import serial
import time
import struct
import numpy as np
import pandas as pd


# Total steps for LSM100A 101.6/0.000047625 = 2,133,333
# Total steps for LSM50A 50.8/0.000047625 = 1,066,666

# Format: [Unit #] (1 byte) [Command #] (1 byte) [Data LSB] (4 btes)
#s.write(b'\0 move abs 1000\n')
#s.write(b'\x00\x01\x00\x00\x00\x00')
#s.write(b'\x01\x14\x01\x02\x0F\x00')
#s.write(b'\x02\x14\x01\x02\x0F\x00')
#s.write(b'\x03\x14\x01\x02\x07\x00')
#s.write(b'\x00\x01\x00\x00\x00\x00')

class StageModuleControl():

  def __init__(self, ser, mid, step_size, total_steps=2133333):
    self.step_size = step_size
    self.total_steps = total_steps
    self.stage_offset = 0.00 # measured in mm
    self.id = mid
    self.ser = ser
    self.sensor_type = 'straight'
    # Clear buffers on initialization
    self.ser.reset_input_buffer()
    self.ser.reset_output_buffer()

  def home(self):
    # Clear buffer before sending command
    self.ser.reset_input_buffer()
    msg = bytearray(struct.pack('BB', self.id, 1)) + bytearray(struct.pack('<I', 0))
    self.ser.write(msg)
    time.sleep(0.01)  # Small delay for command to process
    
    # Wait for response with timeout
    timeout = time.time() + 2.0
    while not self.ser.inWaiting():
      if time.time() > timeout:
        raise TimeoutError(f"Stage {self.id}: Home command timeout")
      time.sleep(0.001)
    
    id, cmd, val = struct.unpack('<BBI', self.ser.read(6))
    if id != self.id:
      raise ValueError(f"Stage {self.id}: Home response ID mismatch (got {id})")

  def go_pos_mm(self, pos, wait=True):
    # convert position in millimeters to steps
    pos_step = int((pos - self.stage_offset)/self.step_size)
    
    # Clear buffer before sending command
    self.ser.reset_input_buffer()
    
    msg = bytearray(struct.pack('BB', self.id, 20)) + bytearray(struct.pack('<I', pos_step))
    self.ser.write(msg)
    
    if wait:
      time.sleep(0.01)  # Small delay for command to process
      
      # Wait for response with timeout
      timeout = time.time() + 2.0
      while not self.ser.inWaiting():
        if time.time() > timeout:
          raise TimeoutError(f"Stage {self.id}: Move command timeout")
        time.sleep(0.001)
      
      id, cmd, val = struct.unpack('<BBI', self.ser.read(6))
      if not (id == self.id):
        raise ValueError(f"Stage {self.id}: Move response ID mismatch (expected {self.id}, got {id})")

  def go_pos(self, pos, wait=True):
    # Clear buffer before sending command
    self.ser.reset_input_buffer()
    
    msg = bytearray(struct.pack('BB', self.id, 20)) + bytearray(struct.pack('<I', pos))
    self.ser.write(msg)
    
    if wait:
      time.sleep(0.01)  # Small delay for command to process
      
      # Wait for response with timeout
      timeout = time.time() + 2.0
      while not self.ser.inWaiting():
        if time.time() > timeout:
          raise TimeoutError(f"Stage {self.id}: Move command timeout")
        time.sleep(0.001)
      
      id, cmd, val = struct.unpack('<BBI', self.ser.read(6))
      if not (id == self.id):
        raise ValueError(f"Stage {self.id}: Move response ID mismatch (expected {self.id}, got {id})")

  def get_pos(self):
    # Clear buffer completely before sending command
    self.ser.reset_input_buffer()
    self.ser.reset_output_buffer()
    time.sleep(0.05)  # Give hardware time to clear
    
    msg = struct.pack('BB', self.id, 60) + struct.pack('<I', 0)
    self.ser.write(msg)
    self.ser.flush()  # Ensure write completes
    time.sleep(0.05)  # Wait for response to arrive
    
    # Wait for exact 6 bytes with timeout
    timeout = time.time() + 2.0
    while self.ser.inWaiting() < 6:
      if time.time() > timeout:
        raise TimeoutError(f"Stage {self.id}: Get position timeout (got {self.ser.inWaiting()} bytes)")
      time.sleep(0.01)
    
    response = self.ser.read(6)
    id, cmd, val = struct.unpack('<BBI', response)
    
    if (id != self.id):
      # Flush remaining data and show what we got
      remaining = self.ser.read(self.ser.inWaiting())
      raise ValueError(f"Stage {self.id}: Get position ID mismatch (expected {self.id}, got {id}). Response: {response.hex()}")
    
    return val*self.step_size
  

  def set_speed(self):
    # Clear buffer before sending command
    self.ser.reset_input_buffer()
    
    msg = struct.pack('BB', self.id, 36) + struct.pack('<I', 0)
    self.ser.write(msg)
    time.sleep(0.01)  # Small delay for command to process
    
    # Wait for response with timeout
    timeout = time.time() + 2.0
    while not self.ser.inWaiting():
      if time.time() > timeout:
        raise TimeoutError(f"Stage {self.id}: Set speed timeout")
      time.sleep(0.001)
    
    id, cmd, val = struct.unpack('<BBI', self.ser.read(6))
    if not (id == self.id):
      raise ValueError(f"Stage {self.id}: Set speed ID mismatch (expected {self.id}, got {id})")
