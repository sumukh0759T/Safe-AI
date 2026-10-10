"""
Authorized device registry for the SafeBath v1 device telemetry protocol.

A device_id must be registered here before any of its messages are accepted;
unknown device_ids are rejected with 403 and recorded in invalid_events.

The default registry mirrors the five physical boards. It can be augmented /
overridden via SAFEBATH_DEVICES env var pointing at a JSON file keyed by
device_id:

    {
      "mat-01": {"module": "mat", "name": "Pressure Mat",
                 "firmware_version": "0.1.0", "token": "secret", "enabled": true}
    }

`token` enables per-device bearer auth (X-Node-Token header). Leave unset /
empty for no auth. Modules are limited to the five protocol modules.
"""
import json
import os
from typing import Dict, Optional

CONTRACT_MODULES = ("mat", "radar", "toilet", "audio", "tof")


class Device:
    __slots__ = ("device_id", "module", "name", "firmware_version",
                 "token", "enabled")

    def __init__(self, device_id: str, module: str, name: str,
                 firmware_version: str = "0.1.0", token: Optional[str] = None,
                 enabled: bool = True) -> None:
        self.device_id = device_id
        self.module = module
        self.name = name
        self.firmware_version = firmware_version
        self.token = token
        self.enabled = enabled

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (f"Device({self.device_id!r}, module={self.module!r}, "
                f"name={self.name!r}, enabled={self.enabled})")


DEFAULT_DEVICES: Dict[str, Device] = {
    "mat-01": Device("mat-01", "mat", "Pressure Mat"),
    "radar-01": Device("radar-01", "radar", "Presence Radar"),
    "toilet-01": Device("toilet-01", "toilet", "Toilet Biomarker"),
    "audio-01": Device("audio-01", "audio", "Acoustic Distress"),
    "tof-01": Device("tof-01", "tof", "ToF / Thermal / Voice"),
}


class DeviceManager:
    def __init__(self, devices: Optional[Dict[str, Device]] = None) -> None:
        self.devices: Dict[str, Device] = dict(devices or {})

    def get(self, device_id: str) -> Optional[Device]:
        return self.devices.get(device_id)

    def known_ids(self):
        return sorted(self.devices)

    @classmethod
    def from_config(cls, config_path: Optional[str] = None) -> "DeviceManager":
        devices = {k: Device(k, d.module, d.name, d.firmware_version, d.token,
                             d.enabled)
                   for k, d in DEFAULT_DEVICES.items()}
        path = config_path or os.environ.get("SAFEBATH_DEVICES")
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                extra = json.load(fh)
            if not isinstance(extra, dict):
                raise ValueError("SAFEBATH_DEVICES must map device_id -> config")
            for device_id, cfg in extra.items():
                base = devices.get(device_id)
                module = cfg.get("module", base.module if base else None)
                if module not in CONTRACT_MODULES:
                    raise ValueError(
                        f"device {device_id!r}: unknown module {module!r}; "
                        f"allowed = {CONTRACT_MODULES}")
                devices[device_id] = Device(
                    device_id=device_id,
                    module=module,
                    name=cfg.get("name", base.name if base else device_id),
                    firmware_version=cfg.get(
                        "firmware_version",
                        base.firmware_version if base else "0.1.0"),
                    token=cfg.get("token"),
                    enabled=bool(cfg.get("enabled", True)),
                )
        return cls(devices)


device_manager = DeviceManager.from_config()