"""Device registry tests — unknown devices must be rejected, modules bounded,
and optional per-device tokens honored."""
import json

import pytest


@pytest.fixture()
def manager():
    from backend.device_manager import DeviceManager
    return DeviceManager.from_config()


def test_default_boards_are_registered(manager):
    assert set(manager.known_ids()) == {
        "mat-01", "radar-01", "toilet-01", "audio-01", "tof-01"}
    assert manager.get("mat-01").module == "mat"
    assert manager.get("tof-01").module == "tof"


def test_unknown_device_is_none(manager):
    assert manager.get("hacker-99") is None


def test_config_extends_defaults(tmp_path):
    from backend.device_manager import DeviceManager
    cfg = tmp_path / "devices.json"
    cfg.write_text(json.dumps({
        "mat-01": {"token": "s3cret", "enabled": True},
        "extra-01": {"module": "toilet", "name": "Spare Toilet",
                     "firmware_version": "0.9.0", "token": "x"},
    }), encoding="utf-8")
    m = DeviceManager.from_config(str(cfg))
    assert m.get("mat-01").token == "s3cret"
    assert m.get("mat-01").module == "mat"      # defaults preserved
    assert m.get("extra-01").module == "toilet"
    assert m.get("extra-01").firmware_version == "0.9.0"
    assert set(m.known_ids()) >= {"mat-01", "extra-01"}


def test_config_rejects_unknown_module(tmp_path):
    from backend.device_manager import DeviceManager
    cfg = tmp_path / "bad.json"
    cfg.write_text(json.dumps({"x-01": {"module": "magic"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        DeviceManager.from_config(str(cfg))