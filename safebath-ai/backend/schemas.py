"""
Pydantic models mirroring the FINAL MQTT data contract
(SafeBath_AI_Final_Master_Guide.md Section 3 — supersedes SOFTWARE_BUILD_SPEC
Section 2 where they differ).

Differences vs the older spec, all deliberate:
 - the mat is a four-zone Velostat sensor (2x2). A frame is exactly four
   finite, non-negative readings in the documented MAT_ZONE_ORDER
   (zone_1_top_left, zone_2_top_right, zone_3_bottom_left,
   zone_4_bottom_right). It travels as Pressure4ZonePayload on its own topic
   (MAT_ZONE_TOPIC) so it never runs through a 72-cell length check.
 - a LEGACY 12x6 / 72-cell grid frame (MatFramePayload) is still accepted
   under "sensor/mat/frame" for explicit backward compatibility only; it is a
   separate, versioned shape and is never padded to or from four-zone data
 - toilet reading has NO temp_c (DS18B20 not in final wiring); kept optional
   so re-adding it later is a one-line change (Master Guide Section 12.1)
 - audio events are JSON {"label", "energy"} — MAX4466 is analog, energy is
   useful for Voice Bridge reply detection

Every backend consumer imports from here; malformed payloads must fail loudly.
"""
import math
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# ---- grid geometry (single source of truth) -------------------------------
MAT_ROWS = 12          # CD4067 channels 0-11
MAT_COLS = 6           # 74HC595 Q1-Q6
MAT_CELL_COUNT = MAT_ROWS * MAT_COLS   # 72
ADC_MAX = 4095         # 12-bit ESP32 ADC

# ---- four-zone mat geometry ------------------------------------------------
# The physical prototype changed from a 12x6 Velostat grid to four larger
# sensing zones (2x2). Each zone is an independent electrode pair, so the
# payload is four readings, NOT 72 cells. Keep this separate from the grid
# constants above so nothing accidentally validates one against the other.
MAT_ZONE_COUNT = 4
MAT_ZONE_TOPIC = "sensor/mat/zones"    # distinct legacy topic for zone frames
MAT_ZONE_SENSOR_TYPE = "pressure_4zone"
# Canonical, documented zone order (the single source of truth). A frame's four
# readings are positional and MUST be sent in exactly this order. The frontend
# and every test import this so nothing can silently reorder the zones.
MAT_ZONE_ORDER = (
    "zone_1_top_left",
    "zone_2_top_right",
    "zone_3_bottom_left",
    "zone_4_bottom_right",
)
# Version the four-zone contract independently of CONTRACT_VERSION so a future
# change to zone semantics is an explicit, detectable break rather than a silent
# reinterpretation of stored frames.
MAT_ZONE_CONTRACT_VERSION = 1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MatFramePayload(StrictModel):
    """72 comma-separated ints, row-major over a 12x6 Velostat grid."""
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


class Pressure4ZonePayload(StrictModel):
    """Four-zone Velostat mat frame: one reading per zone.

    Exactly four numeric, finite, non-negative ADC readings. `sensor_type`
    is the explicit discriminator that keeps four-zone telemetry off the
    72-cell validation path (see MAT_ZONE_TOPIC).

    Values are raw ADC counts (ESP32 12-bit: 0..4095) unless a calibration
    layer converts them; the contract deliberately does NOT impose an upper
    bound so calibrated/scaled values remain representable. The frontend
    labels them "ADC" and only shows a percentage when calibration exists.
    """
    sensor_type: Literal["pressure_4zone"] = MAT_ZONE_SENSOR_TYPE
    zones: List[float] = Field(..., min_length=MAT_ZONE_COUNT,
                               max_length=MAT_ZONE_COUNT)

    @field_validator("zones")
    @classmethod
    def _valid_readings(cls, v):
        for i, x in enumerate(v):
            if not math.isfinite(x):
                raise ValueError(f"zone {i + 1} is not finite: {x!r}")
            if x < 0:
                raise ValueError(f"zone {i + 1} is negative: {x!r}")
        return v

    @classmethod
    def from_csv(cls, raw: str) -> "Pressure4ZonePayload":
        try:
            zones = [float(x) for x in raw.strip().split(",")]
        except ValueError as e:
            raise ValueError(
                f"zone frame is not a CSV of numbers: {raw[:80]!r}") from e
        return cls(zones=zones)


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


AudioLabel = Literal[
    "silence",
    "background_noise",
    "impact_thud",
    "cry_or_scream",
    "ordinary_speech",
    "water_sound",
]


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
    MAT_ZONE_TOPIC: Pressure4ZonePayload,
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
    if model is Pressure4ZonePayload:
        stripped = raw_payload.strip()
        if stripped.startswith("{"):
            return Pressure4ZonePayload.model_validate_json(stripped)
        return Pressure4ZonePayload.from_csv(stripped)
    return model.model_validate_json(raw_payload)
