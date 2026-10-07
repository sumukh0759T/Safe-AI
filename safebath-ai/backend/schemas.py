"""
Pydantic models mirroring the FINAL MQTT data contract
(SafeBath_AI_Final_Master_Guide.md Section 3 — supersedes SOFTWARE_BUILD_SPEC
Section 2 where they differ).

Differences vs the older spec, all deliberate:
 - mat frame stays a 64-int CSV but is 16 rows x 4 cols (row-major)
 - toilet reading has NO temp_c (DS18B20 not in final wiring); kept optional
   so re-adding it later is a one-line change (Master Guide Section 12.1)
 - audio events are JSON {"label", "energy"} — MAX4466 is analog, energy is
   useful for Voice Bridge reply detection

Every backend consumer imports from here; malformed payloads must fail loudly.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# ---- grid geometry (single source of truth) -------------------------------
MAT_ROWS = 16          # CD4067 channels
MAT_COLS = 4           # 74HC595 Q1-Q4
MAT_CELL_COUNT = MAT_ROWS * MAT_COLS   # 64 — same count as old 8x8 plan
ADC_MAX = 4095         # 12-bit ESP32 ADC


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MatFramePayload(StrictModel):
    """64 comma-separated ints, row-major over a 16x4 Velostat grid."""
    values: List[int] = Field(..., min_length=MAT_CELL_COUNT,
                              max_length=MAT_CELL_COUNT)

    @field_validator("values")
    @classmethod
    def _adc_range(cls, v):
        for x in v:
            if not (0 <= x <= ADC_MAX):
                raise ValueError(f"mat cell value {x} outside 0..{ADC_MAX}")
        return v

    def as_grid(self) -> List[List[int]]:
        return [self.values[r * MAT_COLS:(r + 1) * MAT_COLS]
                for r in range(MAT_ROWS)]

    @classmethod
    def from_csv(cls, raw: str) -> "MatFramePayload":
        try:
            values = [int(x) for x in raw.strip().split(",")]
        except ValueError as e:
            raise ValueError(f"mat frame is not a CSV of ints: {raw[:80]!r}") from e
        return cls(values=values)


class MatPirPayload(StrictModel):
    entry: bool


class RadarStatusPayload(StrictModel):
    presence: bool
    motion: bool
    stillness_ms: int = Field(..., ge=0)
    possible_collapse: bool


class ToiletReadingPayload(StrictModel):
    gas_ppm: float = Field(..., ge=0)
    voc_ppm: float = Field(..., ge=0)
    turbidity: float = Field(..., ge=0)
    hour_of_day: int = Field(..., ge=0, le=23)
    temp_c: Optional[float] = None   # pending DS18B20 decision — see module docstring


AudioLabel = Literal["silence", "water", "impact_thud", "speech_or_cry"]


class AudioEventPayload(StrictModel):
    label: AudioLabel
    energy: float = Field(..., ge=0)


class TofThermalStatusPayload(StrictModel):
    avg_thermal_c: float
    flow_pulses: int = Field(..., ge=0)
    tof_collapse: bool
    unattended_hot_fill: bool


class VoiceTriggerPayload(StrictModel):
    prompt: str


VoiceReply = Literal["fine", "help", "no_response"]


class VoiceReplyPayload(StrictModel):
    reply: VoiceReply


# ---- topic registry --------------------------------------------------------
TOPICS = {
    "sensor/mat/frame": MatFramePayload,
    "sensor/mat/pir": MatPirPayload,
    "sensor/radar/status": RadarStatusPayload,
    "sensor/toilet/reading": ToiletReadingPayload,
    "sensor/audio/event": AudioEventPayload,
    "sensor/tof_thermal/status": TofThermalStatusPayload,
    "voice/trigger": VoiceTriggerPayload,
    "voice/reply": VoiceReplyPayload,
}


def parse_for_topic(topic: str, raw_payload: str) -> BaseModel:
    """
    Parse + validate an MQTT payload against its topic's schema.

    `sensor/mat/frame` accepts the CSV string form (contract) OR a JSON body;
    everything else is JSON. Raises ValueError/ValidationError loudly on any
    mismatch — callers must never silently swallow this.
    """
    import json

    model = TOPICS.get(topic)
    if model is None:
        raise ValueError(f"no schema registered for topic {topic!r}")
    if model is MatFramePayload:
        stripped = raw_payload.strip()
        if stripped.startswith("{"):
            return MatFramePayload.model_validate_json(stripped)
        return MatFramePayload.from_csv(stripped)
    return model.model_validate_json(raw_payload)
