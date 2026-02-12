#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Improved stage module with better error handling and diagnostics
"""

from collections import defaultdict
import serial
import time
import struct
import numpy as np
import pandas as pd


# Total steps for LSM100A 101.6/0.000047625 = 2,133,333
# Total steps for LSM50A 50.8/0.000047625 = 1,066,666

class StageModuleControl():

  def __init__(self, ser, mid, step_size, total_steps=2133333, verbose=False):
    self.step_size = step_size
    self.total_steps = total_steps
    self.stage_offset = 0.00 # measured in mm
    self.id = mid
    self.ser = ser
    self.sensor_type = 'straight'
    self.verbose = verbose
    
    # Clear buffer on init
    self.ser.reset_input_buffer()
    self.ser.reset_output_buffer()

  def _send_command(self, command_id, data=0, wait_response=True, timeout=2.0):
    """
    Send a command and optionally wait for response
    Returns: (recv_id, recv_cmd, recv_val) if wait_response=True, else None
    """
    # Clear input buffer before sending
    self.ser.reset_input_buffer()
    
    # Build and send message
    msg = bytearray(struct.pack('BB', self.id, command_id)) + bytearray(struct.pack('<I', data))
    if self.verbose:
      print(f"[Stage {self.id}] Sending cmd {command_id}: {msg.hex()}")
    
    self.ser.write(msg)
    
    if wait_response:
      # Wait for response with timeout
      start_time = time.time()
      while self.ser.in_waiting < 6:
        if time.time() - start_time > timeout:
          raise TimeoutError(f"Stage {self.id}: No response after {timeout}s")
        time.sleep(0.001)
      
      # Read and parse response
      response = self.ser.read(6)
      recv_id, recv_cmd, recv_val = struct.unpack('<BBI', response)
      
      if self.verbose:
        print(f"[Stage {self.id}] Response: {response.hex()} -> ID:{recv_id}, CMD:{recv_cmd}, VAL:{recv_val}")
      
      # Verify ID matches
      if recv_id != self.id:
        raise ValueError(f"Stage {self.id}: ID mismatch in response (got {recv_id})")
      
      return recv_id, recv_cmd, recv_val
    
    return None

  def home(self):
    """Home the stage"""
    if self.verbose:
      print(f"[Stage {self.id}] Homing...")
    self._send_command(command_id=1, data=0, wait_response=True)
    if self.verbose:
      print(f"[Stage {self.id}] Homing complete")

  def go_pos_mm(self, pos, wait=True):
    """Move to position in millimeters"""
    # convert position in millimeters to steps
    pos_step = int((pos - self.stage_offset) / self.step_size)
    
    # Clamp to valid range
    if pos_step < 0:
      print(f"⚠ Warning: Stage {self.id} position {pos}mm is negative, clamping to 0")
      pos_step = 0
    elif pos_step > self.total_steps:
      print(f"⚠ Warning: Stage {self.id} position {pos}mm exceeds max, clamping to {self.total_steps * self.step_size:.2f}mm")
      pos_step = self.total_steps
    
    if self.verbose:
      print(f"[Stage {self.id}] Moving to {pos:.3f}mm (step {pos_step})")
    
    self._send_command(command_id=20, data=pos_step, wait_response=wait)

  def go_pos(self, pos, wait=True):
    """Move to position in steps"""
    if self.verbose:
      print(f"[Stage {self.id}] Moving to step {pos}")
    self._send_command(command_id=20, data=pos, wait_response=wait)

  def get_pos(self):
    """Get current position in millimeters"""
    _, _, val = self._send_command(command_id=60, data=0, wait_response=True)
    pos_mm = val * self.step_size
    
    if self.verbose:
      print(f"[Stage {self.id}] Position: {pos_mm:.3f}mm (step {val})")
    
    return pos_mm
  
  def get_pos_steps(self):
    """Get current position in steps"""
    _, _, val = self._send_command(command_id=60, data=0, wait_response=True)
    return val

  def set_speed(self, speed=None):
    """Set speed (implementation depends on your hardware)"""
    data = speed if speed is not None else 0
    self._send_command(command_id=36, data=data, wait_response=True)
