from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.grid.datasets.builder import (
    DatasetBuildError,
    DatasetBuildRequest,
    build_dataset,
    get_dataset,
    list_datasets,
)
from app.grid.scenarios.models import ScenarioBatchRequest
from app.grid.scenarios.service import (
    ScenarioGenerationError,
    generate_scenario_batch,
    get_scenario_batch,
    list_scenario_batches,
)
from app.grid.training.trainer import (
    OfflineTrainingError,
    OfflineTrainingRequest,
    get_offline_model,
    list_offline_models,
    train_offline_model,
)


router = APIRouter(prefix="/offline", tags=["offline-training"])


@router.post("/scenario-batches/generate")
def generate_batch(request: ScenarioBatchRequest) -> dict[str, Any]:
    try:
        return generate_scenario_batch(request)
    except ScenarioGenerationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/scenario-batches")
def scenario_batches() -> list[dict[str, Any]]:
    return list_scenario_batches()


@router.get("/scenario-batches/{batch_id}")
def scenario_batch(batch_id: str) -> dict[str, Any]:
    try:
        return get_scenario_batch(batch_id)
    except ScenarioGenerationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/datasets/build")
def create_dataset(request: DatasetBuildRequest) -> dict[str, Any]:
    try:
        return build_dataset(request)
    except (DatasetBuildError, ScenarioGenerationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/datasets")
def datasets() -> list[dict[str, Any]]:
    return list_datasets()


@router.get("/datasets/{dataset_id}")
def dataset(dataset_id: str) -> dict[str, Any]:
    try:
        return get_dataset(dataset_id)
    except DatasetBuildError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/datasets/{dataset_id}/preview")
def dataset_preview(
    dataset_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    try:
        metadata = get_dataset(dataset_id)
    except DatasetBuildError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = Path(metadata["previewPath"])
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = []
        for index, row in enumerate(csv.DictReader(stream)):
            if index >= limit:
                break
            rows.append(row)
    return {
        "datasetId": dataset_id,
        "sourceFile": str(path),
        "limit": limit,
        "rows": rows,
    }


@router.post("/training/run")
def run_training(
    request: OfflineTrainingRequest,
) -> dict[str, Any]:
    try:
        return train_offline_model(request)
    except (OfflineTrainingError, DatasetBuildError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/models")
def models() -> list[dict[str, Any]]:
    return list_offline_models()


@router.get("/models/{model_id}")
def model(model_id: str) -> dict[str, Any]:
    try:
        return get_offline_model(model_id)
    except OfflineTrainingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
