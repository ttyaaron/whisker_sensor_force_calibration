from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml


@dataclass
class SensorSettings:
    name: str
    position: int
    sensor_type: str = "strain"
    nominal_wavelength: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SensorSettings":
        metadata_keys = {"name", "position", "sensor_type", "nominal_wavelength"}
        metadata = {k: v for k, v in data.items() if k not in metadata_keys}
        return cls(
            name=data["name"],
            position=data.get("position", 0),
            sensor_type=data.get("sensor_type", data.get("sensor type", "strain")),
            nominal_wavelength=data.get(
                "nominal_wavelength", data.get("nominal wavelength", 0.0)
            ),
            metadata=metadata,
        )

    def to_interrogator_properties(self) -> Dict[str, Any]:
        props = {
            "sensor type": self.sensor_type,
            "position": self.position,
            "nominal wavelength": self.nominal_wavelength,
        }
        props.update(self.metadata)
        return props


@dataclass
class InterrogatorSettings:
    ip_address: str = "10.0.0.126"
    port: int = 1852
    data_interleave: int = 1
    num_averages: int = 1
    ch_gains: List[float] = field(default_factory=lambda: [1, 1, 1, 1])
    ch_noise_thresholds: List[float] = field(default_factory=lambda: [100, 100, 100, 100])
    sensors: List[SensorSettings] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InterrogatorSettings":
        sensors = [
            SensorSettings.from_dict(item)
            for item in data.get("sensors", data.get("fbg_properties", []))
        ]
        if not sensors and "fbg_properties" in data:
            sensors = [
                SensorSettings.from_dict({"name": name, **props})
                for name, props in data["fbg_properties"].items()
            ]
        return cls(
            ip_address=data.get("ip_address", "10.0.0.126"),
            port=data.get("port", 1852),
            data_interleave=data.get("data_interleave", 1),
            num_averages=data.get("num_averages", 1),
            ch_gains=list(data.get("ch_gains", [1, 1, 1, 1])),
            ch_noise_thresholds=list(
                data.get("ch_noise_thresholds", data.get("ch_noise_thres", [100, 100, 100, 100]))
            ),
            sensors=sensors,
        )

    def to_fbg_properties(self) -> Dict[str, Dict[str, Any]]:
        return {sensor.name: sensor.to_interrogator_properties() for sensor in self.sensors}


@dataclass
class SpectrogramSettings:
    nperseg: int = 2048
    max_freq: float = 25.0
    noverlap_ratio: float = 0.5

    @classmethod
    def from_dict(cls, data: Dict[str, Any], defaults: Optional["SpectrogramSettings"] = None) -> "SpectrogramSettings":
        base = defaults if defaults else cls()
        return cls(
            nperseg=data.get("nperseg", base.nperseg),
            max_freq=data.get("max_freq", base.max_freq),
            noverlap_ratio=data.get("noverlap_ratio", base.noverlap_ratio),
        )


@dataclass
class PlotSettings:
    window_size: List[int] = field(default_factory=lambda: [1000, 600])
    vis_height_range: float = 0.02
    plot_limit: bool = False
    history_seconds: float = 10.0
    update_interval_ms: int = 10
    high_res: SpectrogramSettings = field(default_factory=lambda: SpectrogramSettings(2048, 25.0, 0.5))
    wide_range: SpectrogramSettings = field(default_factory=lambda: SpectrogramSettings(512, 200.0, 0.25))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlotSettings":
        base = cls()
        high_res = SpectrogramSettings.from_dict(
            data.get("spectrogram", {}).get("high_res", {}),
            defaults=base.high_res,
        )
        wide_range = SpectrogramSettings.from_dict(
            data.get("spectrogram", {}).get("wide_range", {}),
            defaults=base.wide_range,
        )
        return cls(
            window_size=list(data.get("window_size", base.window_size)),
            vis_height_range=data.get("vis_height_range", base.vis_height_range),
            plot_limit=data.get("plot_limit", base.plot_limit),
            history_seconds=data.get("history_seconds", base.history_seconds),
            update_interval_ms=data.get("update_interval_ms", base.update_interval_ms),
            high_res=high_res,
            wide_range=wide_range,
        )


@dataclass
class RecordingSettings:
    save_directory: Path = Path("./data")
    file_prefix: str = "whisker"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecordingSettings":
        return cls(
            save_directory=Path(data.get("save_directory", "./data")),
            file_prefix=data.get("file_prefix", data.get("filename_prefix", "whisker")),
        )


@dataclass
class FBGConfig:
    interrogator: InterrogatorSettings = field(default_factory=InterrogatorSettings)
    plot: PlotSettings = field(default_factory=PlotSettings)
    recording: RecordingSettings = field(default_factory=RecordingSettings)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FBGConfig":
        return cls(
            interrogator=InterrogatorSettings.from_dict(data.get("interrogator", data)),
            plot=PlotSettings.from_dict(data.get("plot", data)),
            recording=RecordingSettings.from_dict(data.get("recording", data)),
        )


DEFAULT_CONFIG = FBGConfig.from_dict(
    {
        "interrogator": {
            "ip_address": "10.0.0.126",
            "port": 1852,
            "data_interleave": 1,
            "num_averages": 1,
            "ch_gains": [1, 1, 1, 1],
            "ch_noise_thresholds": [100, 100, 100, 100],
            "sensors": [
                {
                    "name": "fbg_1",
                    "position": 0,
                    "sensor_type": "strain",
                    "nominal_wavelength": -14.580973,
                },
                {
                    "name": "fbg_2",
                    "position": 1,
                    "sensor_type": "strain",
                    "nominal_wavelength": -9.541548,
                },
            ],
        },
        "plot": {
            "window_size": [1000, 600],
            "vis_height_range": 0.02,
            "plot_limit": False,
            "history_seconds": 10.0,
            "update_interval_ms": 10,
            "spectrogram": {
                "high_res": {"nperseg": 2048, "max_freq": 25, "noverlap_ratio": 0.5},
                "wide_range": {"nperseg": 512, "max_freq": 200, "noverlap_ratio": 0.25},
            },
        },
        "recording": {"save_directory": "./data", "file_prefix": "whisker"},
    }
)


def load_config(path: Optional[Path] = None) -> FBGConfig:
    """Load configuration from YAML file, falling back to defaults."""
    if path is None:
        return copy.deepcopy(DEFAULT_CONFIG)

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r") as handle:
        data = yaml.safe_load(handle) or {}

    base_dict = asdict(DEFAULT_CONFIG)
    merged = _deep_update(base_dict, data)
    return FBGConfig.from_dict(merged)


def load_and_merge_configs(paths: Iterable[Path]) -> FBGConfig:
    """Merge multiple config files, later files overriding earlier ones."""
    merged: Dict[str, Any] = asdict(DEFAULT_CONFIG)
    for path in paths:
        with Path(path).open("r") as handle:
            data = yaml.safe_load(handle) or {}
        merged = _deep_update(merged, data)
    return FBGConfig.from_dict(merged)


def _deep_update(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in updates.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_update(result[key], value)
        else:
            result[key] = value
    return result
