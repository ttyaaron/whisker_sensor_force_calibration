import numpy as np
import typing
import serial
from copy import deepcopy
from stage_module import StageModuleControl

class WhiskerCalibrationManager:
    """
    WhiskerCalibrationManager is a class that manages a set of linear stage modules
    to enforce a contact point on a whisker sensor for gathering calibration data.
    """

    def __init__(self, reset_pos: np.ndarray, origin_pos: np.ndarray, do_home=False):
        ser = serial.Serial("/dev/ttyUSB0", 9600)

        sx = StageModuleControl(ser, 1, step_size=0.000047625, total_steps=2133333)
        sy = StageModuleControl(ser, 2, step_size=0.000047625, total_steps=2133333)
        sz = StageModuleControl(ser, 3, step_size=0.000047625, total_steps=int(2133333/2))

        sx.get_pos()
        sy.get_pos()
        sz.get_pos()
        
        # Home 3 axis stage if specified
        if (do_home):
            sy.home()
            sx.home()
            sz.home()
        self.sx = sx
        self.sy = sy
        self.sz = sz

        self.reset_pos = reset_pos
        self.origin_pos = origin_pos

    def get_pos(self):
        return np.array([self.sx.get_pos(), self.sy.get_pos(), self.sz.get_pos()])
    
    def get_sx_pos(self):
        return deepcopy(self.sx.get_pos())

    def goto_pos(self, p):
        self.sz.go_pos(p[2])
        self.sy.go_pos(p[1])
        self.sx.go_pos(p[0])


    def goto_pos_mm(self, p, order=['z','y','x'], wait=True):
        """ function commands linear stage modules to move to target
        position p one axis at a time, with order specified by arg:order
        """
        assert len(order) == 3, "order should only have three literals [x,y,z]"
        for i, axis in enumerate(order):
            if axis == 'x':
                self.sx.go_pos_mm(np.clip(p[0], 0, 100), wait = wait)
            elif axis == 'y':
                self.sy.go_pos_mm(np.clip(p[1], 0, 100), wait= wait)
            elif axis == 'z':
                self.sz.go_pos_mm(np.clip(p[2], 0, 50), wait= wait)

    def goto_reset_all(self):
        self.goto_pos_mm(self.reset_pos, order=['y','z','x'])

    def goto_origin(self):
        """ function commands linear stage modules to move to origin position. """
        self.goto_pos_mm(self.reset_pos, order=['z','x','y'])
        self.goto_pos_mm(self.origin_pos, order=['z','x','y'])

    def reset_to_origin(self, loc=None):
        """ function commands linear stage modules to reset first, then
        move to origin with 'y' as last axis to move. This axis is in the
        direction normal to the sensor base."""
        self.goto_reset_all()
        self.goto_pos_mm(self.origin_pos, order=['x','z','y'])
