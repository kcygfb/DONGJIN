from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from redis import Redis

from app.grid.artifact_service import resolve_active_grid_package
from app.grid.offline_io import read_json, write_json
from app.grid.online.diagnosis import diagnose_measurement
from app.grid.scenarios.models import EventType
from app.grid.scenarios.service import _generate_one
from app.grid.settings import GridSettings, get_grid_settings
from app.grid.simulation.engine import get_simulation_engine
from app.grid.simulation.profiles import SimBenchProfileDriver


class ShadowSessionError(RuntimeError):
    pass


def create_shadow_session(
    event_type: EventType | None,
    *,
    target_business_id: str | None = None,
    random_seed: int = 20260724,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    if event_type == EventType.NORMAL:
        raise ShadowSessionError("影子错误会话不能选择NORMAL")
    if event_type is None:
        candidates = [
            item for item in EventType if item != EventType.NORMAL
        ]
        event_type = candidates[
            int(np.random.default_rng(random_seed).integers(0, len(candidates)))
        ]
    active = [
        item for item in list_shadow_sessions(settings)
        if item.get("state") not in {"CLOSED", "FAILED"}
    ]
    if len(active) >= 3:
        raise ShadowSessionError("本机最多同时保留3个活动影子会话")
    manifest, artifact_dir = resolve_active_grid_package(settings)
    session_id = (
        f"shadow-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    root = settings.resolved_shadow_dir / session_id
    root.mkdir(parents=True, exist_ok=False)
    simulation_time = _current_simulation_time(settings)
    driver = SimBenchProfileDriver(
        artifact_dir / "network.json",
        manifest["gridId"],
        settings,
    )
    state = {
        "shadowSessionSchemaVersion": "grid-shadow-session-v1",
        "sessionId": session_id,
        "state": "CREATED",
        "eventType": event_type.value,
        "targetBusinessId": target_business_id,
        "randomSeed": random_seed,
        "simulationTime": simulation_time.isoformat(),
        "createdAt": datetime.now(UTC).isoformat(),
        "artifactPath": str(root),
        "diagnosisId": None,
        "lastError": None,
    }
    write_json(root / "session.json", state)
    try:
        row = _generate_one(
            root,
            session_id,
            1,
            0,
            event_type,
            simulation_time,
            random_seed,
            np.random.default_rng(random_seed),
            driver,
            manifest,
            0.002,
            target_business_id,
        )
        run_dir = root / "runs" / row["scenarioRunId"]
        measurement = read_json(
            run_dir / "measurements" / "frame.json"
        )
        state.update(
            {
                "state": "INJECTED",
                "scenarioRunId": row["scenarioRunId"],
                "targetBusinessId": row["targetBusinessId"],
                "measurementFrameId": measurement["frameId"],
            }
        )
        _publish_shadow_measurement(session_id, measurement, settings)
        write_json(root / "session.json", state)
        return _public_state(state)
    except Exception as exc:
        state["state"] = "FAILED"
        state["lastError"] = f"{type(exc).__name__}: {exc}"
        write_json(root / "session.json", state)
        raise ShadowSessionError(state["lastError"]) from exc


def diagnose_shadow_session(
    session_id: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    state, root = _load_session(session_id, settings)
    if state["state"] not in {"INJECTED", "DIAGNOSED", "REVEALED"}:
        raise ShadowSessionError(
            f"当前状态不能研判：{state['state']}"
        )
    run_dir = root / "runs" / state["scenarioRunId"]
    result = diagnose_measurement(
        read_json(run_dir / "measurements" / "frame.json"),
        source_mode="SHADOW",
        baseline=read_json(run_dir / "truth" / "baseline.json"),
        truth_reference=str(run_dir / "labels.json"),
        settings=settings,
    )
    state["state"] = "DIAGNOSED"
    state["diagnosisId"] = result["diagnosisId"]
    state["diagnosedAt"] = datetime.now(UTC).isoformat()
    write_json(root / "session.json", state)
    return result


def reveal_shadow_session(
    session_id: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    state, root = _load_session(session_id, settings)
    if state.get("diagnosisId") is None:
        raise ShadowSessionError("必须先完成盲判，才能揭示真值")
    run_dir = root / "runs" / state["scenarioRunId"]
    truth = read_json(run_dir / "labels.json")
    prediction = read_json(
        settings.resolved_diagnosis_dir
        / state["diagnosisId"]
        / "result.json"
    )
    comparison = {
        "sessionId": session_id,
        "diagnosisId": state["diagnosisId"],
        "groundTruth": truth,
        "prediction": {
            "predictedEventType": prediction["predictedEventType"],
            "targetBusinessId": prediction["targetBusinessId"],
            "confidence": prediction.get("confidence"),
        },
        "typeMatch": (
            truth["primaryLabel"]
            == prediction["predictedEventType"]
        ),
        "locationMatch": (
            truth["rootCauseBusinessId"]
            == prediction["targetBusinessId"]
        ),
        "revealedAt": datetime.now(UTC).isoformat(),
    }
    comparison["exactMatch"] = (
        comparison["typeMatch"] and comparison["locationMatch"]
    )
    write_json(root / "comparison.json", comparison)
    state["state"] = "REVEALED"
    state["revealedAt"] = comparison["revealedAt"]
    write_json(root / "session.json", state)
    return comparison


def get_shadow_session(
    session_id: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    state, _ = _load_session(
        session_id, settings or get_grid_settings()
    )
    return _public_state(state)


def list_shadow_sessions(
    settings: GridSettings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_grid_settings()
    root = settings.resolved_shadow_dir
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.iterdir(), reverse=True):
        state_path = path / "session.json"
        if state_path.is_file():
            result.append(_public_state(read_json(state_path)))
    return result


def close_shadow_session(
    session_id: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    state, root = _load_session(session_id, settings)
    state["state"] = "CLOSED"
    state["closedAt"] = datetime.now(UTC).isoformat()
    write_json(root / "session.json", state)
    client = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
    )
    try:
        client.delete(
            f"dongjin:shadow:{session_id}:snapshot:active",
            f"dongjin:shadow:{session_id}:measurement",
        )
    finally:
        client.close()
    return _public_state(state)


def _publish_shadow_measurement(
    session_id: str,
    measurement: dict[str, Any],
    settings: GridSettings,
) -> None:
    client = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=settings.health_timeout_seconds,
        socket_timeout=settings.health_timeout_seconds,
        decode_responses=True,
    )
    try:
        payload = json.dumps(
            measurement,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        pipeline = client.pipeline(transaction=True)
        pipeline.set(
            f"dongjin:shadow:{session_id}:measurement",
            payload,
            ex=settings.snapshot_ttl_seconds,
        )
        pipeline.set(
            f"dongjin:shadow:{session_id}:snapshot:active",
            measurement["frameId"],
            ex=settings.snapshot_ttl_seconds,
        )
        pipeline.execute()
    finally:
        client.close()


def _current_simulation_time(settings: GridSettings) -> datetime:
    try:
        snapshot = get_simulation_engine().current_snapshot()
    except Exception:
        snapshot = None
    if snapshot and snapshot.get("simulationTime"):
        return datetime.fromisoformat(snapshot["simulationTime"])
    return settings.profile_start_time


def _load_session(
    session_id: str,
    settings: GridSettings,
) -> tuple[dict[str, Any], Path]:
    if not session_id or Path(session_id).name != session_id:
        raise ShadowSessionError("影子会话ID无效")
    root = settings.resolved_shadow_dir / session_id
    state_path = root / "session.json"
    if not state_path.is_file():
        raise ShadowSessionError(f"影子会话不存在：{session_id}")
    return read_json(state_path), root


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    hidden = {"randomSeed", "eventType", "targetBusinessId"}
    return {
        key: value for key, value in state.items()
        if key not in hidden
    }
