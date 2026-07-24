from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.grid.datasets.builder import get_dataset
from app.grid.offline_io import read_json, write_csv, write_json
from app.grid.settings import GridSettings, get_grid_settings
from app.grid.training.trainer import (
    _evaluate,
    _load_split,
    _prediction_columns,
    _predict,
    _propagation,
    get_offline_model,
)


class ModelEvaluationError(RuntimeError):
    pass


def evaluate_offline_model(
    model_id: str,
    dataset_id: str,
    *,
    split: str = "test",
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    if split not in {"train", "validation", "test"}:
        raise ModelEvaluationError("split必须是train、validation或test")
    model = get_offline_model(model_id, settings)
    dataset = get_dataset(dataset_id, settings)
    model_dir = Path(model["artifactPath"])
    dataset_dir = Path(dataset["artifactPath"])
    artifact = joblib.load(model_dir / "model.joblib")
    if artifact["graphSignature"] != dataset["graphSignature"]:
        raise ModelEvaluationError("模型与评估数据集图签名不一致")
    feature_names = list(artifact["featureNames"])
    label_order = list(artifact["labelOrder"])
    label_to_index = {
        label: index for index, label in enumerate(label_order)
    }
    graph = read_json(dataset_dir / "graph.json")
    loaded = _load_split(
        dataset_dir / f"{split}.parquet",
        [f"feature.{name}" for name in feature_names],
        label_to_index,
        len(graph["vertices"]),
    )
    means = np.asarray(artifact["normalizationMean"], dtype=np.float32)
    deviations = np.asarray(
        artifact["normalizationStandardDeviation"], dtype=np.float32
    )
    deviations = np.where(deviations < 1e-8, 1.0, deviations)
    loaded["features"] = (
        (loaded["features"] - means) / deviations
    ).astype(np.float32)
    probabilities = _predict(
        loaded["features"],
        _propagation(graph),
        artifact["state"],
    )
    metrics, predictions = _evaluate(
        loaded,
        probabilities,
        label_order,
        float(artifact["anomalyThreshold"]),
    )
    evaluation_id = (
        f"evaluation-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    root = settings.resolved_evaluation_dir / evaluation_id
    root.mkdir(parents=True, exist_ok=False)
    result = {
        "evaluationSchemaVersion": "grid-model-evaluation-v1",
        "evaluationId": evaluation_id,
        "modelId": model_id,
        "datasetId": dataset_id,
        "split": split,
        "metrics": metrics,
        "artifactPath": str(root),
        "generatedAt": datetime.now(UTC).isoformat(),
        "selectionChanged": False,
    }
    write_json(root / "result.json", result)
    write_csv(
        root / "predictions.csv",
        predictions,
        fieldnames=_prediction_columns(),
    )
    (root / "README.md").write_text(
        "# 独立模型评估\n\n"
        f"- 模型：`{model_id}`\n"
        f"- 数据集：`{dataset_id}`\n"
        f"- 数据分区：`{split}`\n\n"
        "本操作只生成评估文件，不会修改当前在线模型选择。\n",
        encoding="utf-8",
    )
    return result
