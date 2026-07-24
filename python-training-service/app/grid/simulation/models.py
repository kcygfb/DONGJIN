from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SnapshotModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class BusMeasurement(SnapshotModel):
    vm_pu: float = Field(alias="vmPu", description="Voltage magnitude, pu")
    va_degree: float = Field(
        alias="vaDegree", description="Voltage angle, degree"
    )
    p_mw: float = Field(alias="pMw", description="Net active power, MW")
    q_mvar: float = Field(
        alias="qMvar", description="Net reactive power, Mvar"
    )


class BranchMeasurement(SnapshotModel):
    p_from_mw: float = Field(
        alias="pFromMw", description="From-side active power, MW"
    )
    q_from_mvar: float = Field(
        alias="qFromMvar", description="From-side reactive power, Mvar"
    )
    p_to_mw: float = Field(
        alias="pToMw", description="To-side active power, MW"
    )
    q_to_mvar: float = Field(
        alias="qToMvar", description="To-side reactive power, Mvar"
    )
    i_from_ka: float = Field(
        alias="iFromKa", description="From-side current, kA"
    )
    i_to_ka: float = Field(
        alias="iToKa", description="To-side current, kA"
    )
    loading_percent: float = Field(
        alias="loadingPercent", description="Thermal loading, percent"
    )
    pl_mw: float = Field(alias="plMw", description="Active loss, MW")
    ql_mvar: float = Field(
        alias="qlMvar", description="Reactive loss, Mvar"
    )


class TransformerMeasurement(SnapshotModel):
    p_hv_mw: float = Field(
        alias="pHvMw", description="HV-side active power, MW"
    )
    q_hv_mvar: float = Field(
        alias="qHvMvar", description="HV-side reactive power, Mvar"
    )
    p_lv_mw: float = Field(
        alias="pLvMw", description="LV-side active power, MW"
    )
    q_lv_mvar: float = Field(
        alias="qLvMvar", description="LV-side reactive power, Mvar"
    )
    i_hv_ka: float = Field(
        alias="iHvKa", description="HV-side current, kA"
    )
    i_lv_ka: float = Field(
        alias="iLvKa", description="LV-side current, kA"
    )
    loading_percent: float = Field(
        alias="loadingPercent", description="Transformer loading, percent"
    )
    pl_mw: float = Field(alias="plMw", description="Active loss, MW")
    ql_mvar: float = Field(
        alias="qlMvar", description="Reactive loss, Mvar"
    )
    tap_position: float | None = Field(
        alias="tapPosition", description="Current tap position"
    )


class SwitchMeasurement(SnapshotModel):
    closed: bool
    in_service: bool = Field(alias="inService")


class PowerMeasurement(SnapshotModel):
    p_mw: float = Field(alias="pMw", description="Active power, MW")
    q_mvar: float = Field(
        alias="qMvar", description="Reactive power, Mvar"
    )


class ExternalGridMeasurement(PowerMeasurement):
    bus_business_id: str = Field(alias="busBusinessId")


class GridSnapshot(SnapshotModel):
    snapshot_id: str = Field(alias="snapshotId")
    grid_id: str = Field(alias="gridId")
    topology_version: str = Field(alias="topologyVersion")
    schema_version: str = Field(
        default="grid-snapshot-v1", alias="schemaVersion"
    )
    simulation_time: str = Field(alias="simulationTime")
    profile_source_time: str = Field(alias="profileSourceTime")
    published_at: str = Field(alias="publishedAt")
    converged: bool
    calculation_duration_ms: float = Field(alias="calculationDurationMs")
    profile_strategy: str = Field(alias="profileStrategy")
    profile_provenance: dict[str, Any] = Field(alias="profileProvenance")
    performance: dict[str, float]
    buses: dict[str, BusMeasurement]
    lines: dict[str, BranchMeasurement]
    transformers: dict[str, TransformerMeasurement]
    switches: dict[str, SwitchMeasurement]
    loads: dict[str, PowerMeasurement]
    generators: dict[str, PowerMeasurement]
    external_grids: dict[str, ExternalGridMeasurement] = Field(
        alias="externalGrids"
    )
