from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.grid.online.diagnosis import (
    OnlineDiagnosisError,
    diagnose_snapshot,
)
from app.grid.online.model_manager import (
    InferenceModelError,
    check_model_compatibility,
    get_selected_model,
    list_inference_models,
    model_selection_history,
    rollback_inference_model,
    select_inference_model,
)
from app.grid.online.shadow import (
    ShadowSessionError,
    close_shadow_session,
    create_shadow_session,
    diagnose_shadow_session,
    get_shadow_session,
    list_shadow_sessions,
    reveal_shadow_session,
)
from app.grid.online.short_circuit import (
    ShortCircuitAnalysisError,
    get_short_circuit_analysis,
    run_short_circuit_analysis,
)
from app.grid.online.monitor import (
    DiagnosisMonitorError,
    get_diagnosis_monitor,
)
from app.grid.scenarios.models import EventType
from app.grid.simulation.engine import get_simulation_engine
from app.grid.publishers.redis_snapshot import RedisSnapshotError
from app.grid.offline_io import read_json
from app.grid.settings import get_grid_settings
from pathlib import Path


router = APIRouter(tags=["online-diagnosis"])


class ModelSelectionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    model_id: str = Field(alias="modelId", min_length=1)
    actor: str = Field(default="api-user", min_length=1)


class ShadowCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    event_type: EventType | None = Field(
        default=None, alias="eventType"
    )
    target_business_id: str | None = Field(
        default=None, alias="targetBusinessId"
    )
    random_seed: int = Field(default=20260724, alias="randomSeed")


class ShortCircuitRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    target_business_id: str = Field(
        alias="targetBusinessId", min_length=1
    )
    fault_type: Literal["3ph", "2ph", "1ph"] = Field(
        default="3ph", alias="faultType"
    )
    case: Literal["max", "min"] = "max"
    r_fault_ohm: float = Field(default=0.0, alias="rFaultOhm", ge=0)
    x_fault_ohm: float = Field(default=0.0, alias="xFaultOhm", ge=0)
    s_sc_mva: float | None = Field(
        default=None, alias="sScMva", gt=0
    )
    rx: float | None = Field(default=None, gt=0)


class MonitorStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    interval_seconds: float = Field(
        default=5.0, alias="intervalSeconds", ge=1
    )


@router.get("/inference/models")
def inference_models() -> dict[str, Any]:
    return list_inference_models()


@router.get("/inference/model")
def inference_model() -> dict[str, Any] | None:
    return get_selected_model(required=False)


@router.get("/inference/model/history")
def inference_model_history() -> list[dict[str, Any]]:
    return model_selection_history()


@router.post("/inference/model/check/{model_id}")
def check_inference_model(model_id: str) -> dict[str, Any]:
    try:
        return check_model_compatibility(model_id)
    except InferenceModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/inference/model/select")
def select_model(request: ModelSelectionRequest) -> dict[str, Any]:
    try:
        return select_inference_model(
            request.model_id, actor=request.actor
        )
    except InferenceModelError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/inference/model/reload")
def reload_model() -> dict[str, Any]:
    try:
        current = get_selected_model(required=True)
        return {
            "status": "RELOADED",
            "selection": current,
            "compatibility": check_model_compatibility(
                current["modelId"]
            ),
        }
    except InferenceModelError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/inference/model/rollback")
def rollback_model() -> dict[str, Any]:
    try:
        return rollback_inference_model(actor="api-user")
    except InferenceModelError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/diagnosis/current")
def diagnose_current() -> dict[str, Any]:
    snapshot = get_simulation_engine().current_snapshot()
    if snapshot is None:
        raise HTTPException(
            status_code=404, detail="Redis中尚无当前动态快照"
        )
    try:
        return diagnose_snapshot(snapshot)
    except (OnlineDiagnosisError, InferenceModelError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/diagnosis/snapshots/{snapshot_id}")
def diagnose_named_snapshot(snapshot_id: str) -> dict[str, Any]:
    try:
        snapshot = get_simulation_engine().publisher.get(snapshot_id)
    except RedisSnapshotError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="指定快照不存在或已过期")
    try:
        return diagnose_snapshot(snapshot)
    except (OnlineDiagnosisError, InferenceModelError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/diagnosis/{diagnosis_id}")
def diagnosis_result(diagnosis_id: str) -> dict[str, Any]:
    if not diagnosis_id or Path(diagnosis_id).name != diagnosis_id:
        raise HTTPException(status_code=400, detail="diagnosisId无效")
    path = (
        get_grid_settings().resolved_diagnosis_dir
        / diagnosis_id
        / "result.json"
    )
    if not path.is_file():
        raise HTTPException(status_code=404, detail="研判结果不存在")
    return read_json(path)


@router.post("/diagnosis/monitor/start")
def start_diagnosis_monitor(
    request: MonitorStartRequest | None = None,
) -> dict[str, Any]:
    request = request or MonitorStartRequest()
    try:
        return get_diagnosis_monitor().start(request.interval_seconds)
    except DiagnosisMonitorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/diagnosis/monitor/status")
def diagnosis_monitor_status() -> dict[str, Any]:
    return get_diagnosis_monitor().status()


@router.post("/diagnosis/monitor/stop")
def stop_diagnosis_monitor() -> dict[str, Any]:
    return get_diagnosis_monitor().stop()


@router.get("/shadow-sessions")
def shadow_sessions() -> list[dict[str, Any]]:
    return list_shadow_sessions()


@router.post("/shadow-sessions")
def create_shadow(request: ShadowCreateRequest) -> dict[str, Any]:
    try:
        return create_shadow_session(
            request.event_type,
            target_business_id=request.target_business_id,
            random_seed=request.random_seed,
        )
    except ShadowSessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/shadow-sessions/{session_id}")
def shadow_session(session_id: str) -> dict[str, Any]:
    try:
        return get_shadow_session(session_id)
    except ShadowSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/shadow-sessions/{session_id}/diagnose")
def diagnose_shadow(session_id: str) -> dict[str, Any]:
    try:
        return diagnose_shadow_session(session_id)
    except (ShadowSessionError, InferenceModelError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/shadow-sessions/{session_id}/reveal")
def reveal_shadow(session_id: str) -> dict[str, Any]:
    try:
        return reveal_shadow_session(session_id)
    except ShadowSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/shadow-sessions/{session_id}")
def close_shadow(session_id: str) -> dict[str, Any]:
    try:
        return close_shadow_session(session_id)
    except ShadowSessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/short-circuit-analyses")
def short_circuit(request: ShortCircuitRequest) -> dict[str, Any]:
    try:
        return run_short_circuit_analysis(
            request.target_business_id,
            fault_type=request.fault_type,
            case=request.case,
            r_fault_ohm=request.r_fault_ohm,
            x_fault_ohm=request.x_fault_ohm,
            s_sc_mva=request.s_sc_mva,
            rx=request.rx,
        )
    except ShortCircuitAnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/short-circuit-analyses/{analysis_id}")
def short_circuit_result(analysis_id: str) -> dict[str, Any]:
    try:
        return get_short_circuit_analysis(analysis_id)
    except ShortCircuitAnalysisError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
