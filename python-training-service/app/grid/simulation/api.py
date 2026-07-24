from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.grid.artifact_service import GridPackageError
from app.grid.publishers.neo4j import Neo4jProjectionError
from app.grid.publishers.redis_snapshot import RedisSnapshotError
from app.grid.simulation.engine import (
    SimulationStateError,
    get_simulation_engine,
)


router = APIRouter(tags=["grid-runtime"])


class SimulationStartRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    start_time: datetime | None = Field(default=None, alias="startTime")
    speed_factor: float = Field(default=1.0, alias="speedFactor", gt=0)
    profile_strategy: Literal["hold", "linear"] | None = Field(
        default=None,
        alias="profileStrategy",
    )


@router.post("/simulation/start")
def start_simulation(request: SimulationStartRequest | None = None):
    request = request or SimulationStartRequest()
    try:
        return get_simulation_engine().start(
            start_time=request.start_time,
            speed_factor=request.speed_factor,
            profile_strategy=request.profile_strategy,
        )
    except SimulationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (GridPackageError, Neo4jProjectionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/simulation/pause")
def pause_simulation():
    try:
        return get_simulation_engine().pause()
    except SimulationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/simulation/resume")
def resume_simulation():
    try:
        return get_simulation_engine().resume()
    except SimulationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/simulation/stop")
def stop_simulation():
    return get_simulation_engine().stop()


@router.get("/simulation/status")
def simulation_status():
    return get_simulation_engine().status()


@router.get("/simulation/profiles")
def simulation_profiles():
    try:
        return get_simulation_engine().profile_metadata()
    except GridPackageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/snapshots/current")
def current_snapshot():
    try:
        snapshot = get_simulation_engine().current_snapshot()
    except RedisSnapshotError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="尚无有效的实时潮流快照")
    return snapshot
