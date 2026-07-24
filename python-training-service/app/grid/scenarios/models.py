from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventType(StrEnum):
    NORMAL = "NORMAL"
    LINE_OUTAGE = "LINE_OUTAGE"
    TRANSFORMER_OUTAGE = "TRANSFORMER_OUTAGE"
    SWITCH_MISOPERATION = "SWITCH_MISOPERATION"
    LOAD_SURGE = "LOAD_SURGE"
    GENERATION_DROP = "GENERATION_DROP"
    MEASUREMENT_BIAS = "MEASUREMENT_BIAS"
    MEASUREMENT_DROPOUT = "MEASUREMENT_DROPOUT"
    MEASUREMENT_FROZEN = "MEASUREMENT_FROZEN"
    MEASUREMENT_DRIFT = "MEASUREMENT_DRIFT"
    MEASUREMENT_DELAY = "MEASUREMENT_DELAY"
    MEASUREMENT_QUANTIZATION = "MEASUREMENT_QUANTIZATION"
    TAP_POSITION_ANOMALY = "TAP_POSITION_ANOMALY"


class QualityCode(StrEnum):
    GOOD = "GOOD"
    SUSPECT = "SUSPECT"
    BAD = "BAD"
    MISSING = "MISSING"
    SUBSTITUTED = "SUBSTITUTED"
    DELAYED = "DELAYED"
    FROZEN = "FROZEN"


class ScenarioEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId", min_length=1)
    event_type: EventType = Field(alias="eventType")
    target_business_id: str | None = Field(
        default=None,
        alias="targetBusinessId",
    )
    parameters: dict[str, Any] = Field(default_factory=dict)


class ScenarioDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scenario_id: str = Field(alias="scenarioId", min_length=1)
    scenario_run_id: str = Field(alias="scenarioRunId", min_length=1)
    scenario_schema_version: str = Field(
        default="grid-scenario-v1",
        alias="scenarioSchemaVersion",
    )
    grid_id: str = Field(alias="gridId", min_length=1)
    topology_version: str = Field(alias="topologyVersion", min_length=1)
    simulation_time: datetime = Field(alias="simulationTime")
    random_seed: int = Field(alias="randomSeed")
    event: ScenarioEvent

    @model_validator(mode="after")
    def normalize_time(self) -> "ScenarioDefinition":
        if self.simulation_time.tzinfo is None:
            self.simulation_time = self.simulation_time.replace(tzinfo=UTC)
        return self


class ScenarioBatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    batch_id: str | None = Field(default=None, alias="batchId")
    samples_per_type: int = Field(
        default=8,
        alias="samplesPerType",
        ge=3,
        le=500,
    )
    event_types: list[EventType] = Field(
        default_factory=lambda: list(EventType),
        alias="eventTypes",
        min_length=2,
    )
    random_seed: int = Field(default=20260723, alias="randomSeed")
    start_time: datetime = Field(
        default=datetime(2016, 1, 1, tzinfo=UTC),
        alias="startTime",
    )
    time_step_seconds: int = Field(
        default=900,
        alias="timeStepSeconds",
        ge=1,
    )
    measurement_noise_relative_std: float = Field(
        default=0.002,
        alias="measurementNoiseRelativeStd",
        ge=0,
        le=0.2,
    )

    @model_validator(mode="after")
    def validate_event_types(self) -> "ScenarioBatchRequest":
        unique = list(dict.fromkeys(self.event_types))
        if EventType.NORMAL not in unique:
            raise ValueError("离线训练批次必须包含NORMAL正常场景")
        self.event_types = unique
        if self.start_time.tzinfo is None:
            self.start_time = self.start_time.replace(tzinfo=UTC)
        return self
