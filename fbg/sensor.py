import json
from typing import List

class Sensor(object):
    def __init__(self, name, properties=None):
        self.name = name
        self.properties = properties
        self.position = None
        self.type = None
        self.serial_no = None
        self.nominal_wavelength = None
        self.gage_factor = None
        self.gage_constant_1 = None
        self.gage_constant_2 = None
        self.temperature_change = 0.0
        self.cte_specimen = None
        self.wavelength_shift = None
        self.wavelength = 0
        self.initial_wavelength = None
        self.wavelength_offset = None
        self.cal_coeff_1 = None
        self.cal_coeff_2 = None
        self.cal_coeff_3 = None
        self.cal_coeff_0 = None
        self.temp_sens = None
        if self.properties:
            self.load_properties()
            
    def load_properties(self, properties=None):
        if properties:
            self.properties = properties
        if not self.properties:
            return

        props = self.properties
        self.type = props.get("sensor type", props.get("sensor_type", "strain"))
        self.position = props.get("position", self.position)
        self.serial_no = props.get("serial number", props.get("serial_number"))
        self.nominal_wavelength = props.get(
            "nominal wavelength", props.get("nominal_wavelength", self.nominal_wavelength)
        )
        if self.type == "strain":
            self.gage_factor = props.get("gage factor", props.get("gage_factor", 1.0))
            self.gage_constant_1 = props.get("gage constant 1", props.get("gage_constant_1", 0.0))
            self.gage_constant_2 = props.get("gage constant 2", props.get("gage_constant_2", 0.0))
            self.cte_specimen = props.get("CTE of test specimen", props.get("cte_specimen"))
            self.initial_wavelength = self.nominal_wavelength
        elif self.type == "bare strain":
            self.ke = props.get("ke", 1.0)
            self.initial_wavelength = self.nominal_wavelength
        elif self.type == "temperature":
            self.temp_at_nom_wavelength = props.get(
                "temperature at nominal wavelength",
                props.get("temperature_at_nominal_wavelength"),
            )
            self.wavelength_offset = props.get("wavelength offset", props.get("wavelength_offset"))
            self.cal_coeff_0 = props.get("calibration coeff. 0", props.get("calibration_coeff_0"))
            self.cal_coeff_1 = props.get("calibration coeff. 1", props.get("calibration_coeff_1"))
            self.cal_coeff_2 = props.get("calibration coeff. 2", props.get("calibration_coeff_2"))
            self.cal_coeff_3 = props.get("calibration coeff. 3", props.get("calibration_coeff_3"))
            self.temp_sens = props.get("temp. sensitivity", props.get("temp_sensitivity"))
    
    def load_properties_from_file(self, filename="Config/fbg_properties.json"):
        """Reads the properties in JSON format from the given file."""
        with open(filename) as f:
            self.properties = json.load(f)[self.name]
        
    @property
    def strain(self):
        if self.type.lower() == "strain":
            self.wavelength_shift = self.wavelength - self.initial_wavelength
            self.thermal_output = self.temperature_change*\
                    (self.gage_constant_1/self.gage_factor + self.cte_specimen\
                    - self.gage_constant_2) 
            return (self.wavelength_shift/self.initial_wavelength)\
                    *1e6/self.gage_factor - self.thermal_output
        elif self.type.lower() == "bare strain":
            self.wavelength_shift = self.wavelength - self.initial_wavelength
            return self.wavelength_shift/self.initial_wavelength/self.ke
        else:
            return None
            
    @property
    def temperature(self):
        if self.type.lower() == "temperature":
            return self.cal_coeff_3*(self.wavelength + self.wavelength_offset)**3 \
                    + self.cal_coeff_2*(self.wavelength + self.wavelength_offset)**2 \
                    + self.cal_coeff_1*(self.wavelength + self.wavelength_offset) \
                    + self.cal_coeff_0
        else:
            return None
