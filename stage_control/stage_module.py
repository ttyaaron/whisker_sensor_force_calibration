#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from collections import defaultdict
try:
  import serial
except ModuleNotFoundError as exc:
  raise SystemExit(
    "缺少依赖 pyserial。请安装后重试:\n"
    "  pip install pyserial\n"
    "或在 conda 环境中:\n"
    "  conda install pyserial"
  ) from exc
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

  def _read_response_for_id(self, timeout_s=3.0, expected_cmd=None, accepted_cmds=None):
    """Read 6-byte frames until a response for this module ID (and optional command set) arrives."""
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
        if expected_cmd is None and accepted_cmds is None:
          return rid, cmd, val
        if expected_cmd is not None and cmd == expected_cmd:
          return rid, cmd, val
        if accepted_cmds is not None and cmd in accepted_cmds:
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
    if accepted_cmds is not None:
      expected_text = sorted(set(int(c) for c in accepted_cmds))
      raise TimeoutError(
        f"Stage {self.id}: response timeout waiting for CMD in {expected_text}"
        f"{mismatch_id_text}{mismatch_cmd_text}"
      )
    raise TimeoutError(f"Stage {self.id}: response timeout{mismatch_id_text}{mismatch_cmd_text}")

  def home(self, poll_completion=False, poll_timeout_s=60.0):
    """Send hardware home command (CMD 1).

    If *poll_completion* is True (recommended for Y-axis), we avoid reading
    the ACK (which can interfere with some firmware) and instead poll
    ``get_pos()`` until the axis reaches ~0 or stalls.  This replaces the
    old blind-sleep approach and gives deterministic success/failure.
    """
    # Clear buffer thoroughly before sending home command
    time.sleep(0.05)
    self.ser.reset_input_buffer()
    time.sleep(0.05)

    msg = bytearray(struct.pack('BB', self.id, 1)) + bytearray(struct.pack('<I', 0))
    self.ser.write(msg)

    if not poll_completion:
      # Standard ACK read (works for X/Z).
      self._read_response_for_id(timeout_s=15.0, accepted_cmds={1, 255})
      return

    # --- Poll-based completion (Y-axis) ---
    # Don't read the ACK — it may not arrive or may interfere.
    # Instead, give the motor a head-start then poll position.
    time.sleep(2.0)
    prev_pos = None
    stable_count = 0
    deadline = time.time() + poll_timeout_s

    while time.time() < deadline:
      time.sleep(1.5)
      try:
        self.ser.reset_input_buffer()
        time.sleep(0.05)
        pos = self.get_pos()
      except TimeoutError:
        continue  # Transient read failure during motion — retry

      if pos < 0.15:  # Within ~0.15 mm of home
        return

      if prev_pos is not None and abs(pos - prev_pos) < 0.02:
        stable_count += 1
        if stable_count >= 3:
          raise RuntimeError(
            f"Stage {self.id}: home STALLED at {pos:.2f} mm "
            f"(position unchanged for {stable_count * 1.5:.0f}s)"
          )
      else:
        stable_count = 0
      prev_pos = pos

    # If we get here, we timed out
    try:
      final = self.get_pos()
    except Exception:
      final = -1
    raise TimeoutError(
      f"Stage {self.id}: home timed out after {poll_timeout_s:.0f}s, "
      f"position={final:.2f} mm"
    )

  def go_pos_mm(self, pos, wait=True, timeout_s=3.0):
    # convert position in millimeters to steps
    pos_step = int((pos - self.stage_offset)/self.step_size)

    # Clear buffer before sending command
    self.ser.reset_input_buffer()

    msg = bytearray(struct.pack('BB', self.id, 20)) + bytearray(struct.pack('<I', pos_step))
    self.ser.write(msg)

    if wait:
      # Some controllers report move status with CMD 10 instead of echoing CMD 20.
      self._read_response_for_id(timeout_s=timeout_s, accepted_cmds={20, 10})

  def go_pos(self, pos, wait=True, timeout_s=3.0):
    # Clear buffer before sending command
    self.ser.reset_input_buffer()

    msg = bytearray(struct.pack('BB', self.id, 20)) + bytearray(struct.pack('<I', pos))
    self.ser.write(msg)

    if wait:
      self._read_response_for_id(timeout_s=timeout_s, accepted_cmds={20, 10})

  def set_zero(self):
    """Reset the controller's internal step counter to 0 at the current position.

    Useful for open-loop axes (Y) after manual repositioning or suspected drift.
    Sends CMD 6 (Set Position) with value 0.
    """
    self.ser.reset_input_buffer()
    msg = struct.pack('BB', self.id, 6) + struct.pack('<I', 0)
    self.ser.write(msg)
    try:
      self._read_response_for_id(timeout_s=2.0, accepted_cmds={6, 255})
    except TimeoutError:
      pass  # Some firmware doesn't ACK CMD 6; position is still reset

  def get_pos(self):
    # Clear buffer completely before sending command
    self.ser.reset_input_buffer()

    msg = struct.pack('BB', self.id, 60) + struct.pack('<I', 0)
    self.ser.write(msg)
    self.ser.flush()  # Ensure write completes
    _, _, val = self._read_response_for_id(timeout_s=3.0, expected_cmd=60)
    return val*self.step_size
  

  def set_speed(self, step_rate=0):
    """Set maxspeed (CMD 42) and limit.approach.maxspeed (CMD 41).

    Both must be set because:
      - Before homing, speed = min(CMD 42, CMD 41)
      - After homing,  speed = CMD 42
    step_rate is in microsteps/second (0 = use device default).
    """
    step_rate = int(step_rate)
    if step_rate < 0:
      raise ValueError('step_rate must be >= 0')

    # CMD 42 = maxspeed (controls speed after homing)
    self.ser.reset_input_buffer()
    msg = struct.pack('BB', self.id, 42) + struct.pack('<I', step_rate)
    self.ser.write(msg)
    try:
      self._read_response_for_id(timeout_s=2.0, accepted_cmds={42, 255})
    except TimeoutError:
      pass  # Some firmware may not ACK; continue anyway

    # CMD 41 = limit.approach.maxspeed (controls speed before homing)
    time.sleep(0.05)
    self.ser.reset_input_buffer()
    msg = struct.pack('BB', self.id, 41) + struct.pack('<I', step_rate)
    self.ser.write(msg)
    try:
      self._read_response_for_id(timeout_s=2.0, accepted_cmds={41, 255})
    except TimeoutError:
      pass
