from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPT_HEADER = ROOT / "firmware" / "tof_thermal_voice_node" / "Module5Prompt.h"
AUDIO_HEADER = ROOT / "firmware" / "tof_thermal_voice_node" / "Module5Audio.h"
SKETCH = ROOT / "firmware" / "tof_thermal_voice_node" / "tof_thermal_voice_node.ino"


def test_prompt_map_is_restricted_to_numbered_mp3_prompt_allowlist():
    source = PROMPT_HEADER.read_text(encoding="utf-8")
    assert '"are_you_ok") == 0) return 1;' in source
    assert '"help") == 0) return 2;' in source
    assert '"emergency_procedure") == 0) return 3;' in source
    assert '"safety_check") == 0) return 4;' in source
    assert "return 0;" in source
    assert "track >= 1 && track <= 4" in source


def test_driver_uses_mp3_folder_api_and_bounded_nonblocking_poll():
    source = AUDIO_HEADER.read_text(encoding="utf-8")
    assert "playMp3Folder(promptNumber)" in source
    assert "kMaxEventsPerPoll = 8" in source
    assert "retryDelayMs_ = retryDelayMs_ >= kMaxRetryMs / 2" in source
    assert "kPlaybackTimeoutMs = 120000" in source


def test_module5_keeps_existing_pending_route_and_only_dispatches_allowlisted_tracks():
    source = SKETCH.read_text(encoding="utf-8")
    assert "/api/v1/voice/pending?device_id=tof-01&role=play" in source
    assert "module5PromptTrack(promptName.c_str())" in source
    assert "module5PromptAllowed(promptNumber)" in source
    assert "dfPlayer.play(1)" not in source
