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

  def _read_response_for_id(self, timeout_s=3.0, expected_cmd=None):
    """Read 6-byte frames until a response for this module ID (and optional command) arrives."""
    deadline = time.time() + timeout_s
    mismatched_ids = []
    mismatched_cmds = []

    while time.time() < deadline:
      if self.ser.inWaiting() < 6:
        time.sleep(0.001)
        continue

      response = self.ser.read(6)
      if len(response) < 6:
        time.sleep(0.001)
        continue

      rid, cmd, val = struct.unpack('<BBI', response)
      if rid == self.id:
        if expected_cmd is None or cmd == expected_cmd:
          return rid, cmd, val
        mismatched_cmds.append(cmd)
        continue

      mismatched_ids.append(rid)

    mismatch_id_text = f", seen other IDs: {sorted(set(mismatched_ids))}" if mismatched_ids else ""
    mismatch_cmd_text = (
      f", seen other CMDs for this ID: {sorted(set(mismatched_cmds))}"
      if mismatched_cmds else ""
    )
    if expected_cmd is not None:
      raise TimeoutError(
        f"Stage {self.id}: response timeout waiting for CMD {expected_cmd}"
        f"{mismatch_id_text}{mismatch_cmd_text}"
      )
    raise TimeoutError(f"Stage {self.id}: response timeout{mismatch_id_text}{mismatch_cmd_text}")

  def home(self):
    # Clear buffer before sending command
    self.ser.reset_input_buffer()
    msg = bytearray(struct.pack('BB', self.id, 1)) + bytearray(struct.pack('<I', 0))
    self.ser.write(msg)
    # Homing acknowledgement can be slower on some controllers.
    self._read_response_for_id(timeout_s=5.0, expected_cmd=1)

  def go_pos_mm(self, pos, wait=True):
    # convert position in millimeters to steps
    pos_step = int((pos - self.stage_offset)/self.step_size)
    
    # Clear buffer before sending command
    self.ser.reset_input_buffer()
    
    msg = bytearray(struct.pack('BB', self.id, 20)) + bytearray(struct.pack('<I', pos_step))
    self.ser.write(msg)
    
    if wait:
      self._read_response_for_id(timeout_s=3.0, expected_cmd=20)

  def go_pos(self, pos, wait=True):
    # Clear buffer before sending command
    self.ser.reset_input_buffer()
    
    msg = bytearray(struct.pack('BB', self.id, 20)) + bytearray(struct.pack('<I', pos))
    self.ser.write(msg)
    
    if wait:
      self._read_response_for_id(timeout_s=3.0, expected_cmd=20)

  def get_pos(self):
    # Clear buffer completely before sending command
    self.ser.reset_input_buffer()

    msg = struct.pack('BB', self.id, 60) + struct.pack('<I', 0)
    self.ser.write(msg)
    self.ser.flush()  # Ensure write completes
    _, _, val = self._read_response_for_id(timeout_s=3.0, expected_cmd=60)
    return val*self.step_size
  

  def set_speed(self):
    # Clear buffer before sending command
    self.ser.reset_input_buffer()
    
    msg = struct.pack('BB', self.id, 36) + struct.pack('<I', 0)
    self.ser.write(msg)
    self._read_response_for_id(timeout_s=3.0, expected_cmd=36)
