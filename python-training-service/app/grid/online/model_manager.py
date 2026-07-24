from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib

from app.grid.artifact_service import resolve_active_grid_package
from app.grid.datasets.builder import _build_graph
from app.grid.offline_io import read_json, sha256, write_json
from app.grid.settings import GridSettings, get_grid_settings
from app.grid.training.trainer import (
    OfflineTrainingError,
    get_offline_model,
    list_offline_models,
)


REQUIRED_MODEL_FILES = (
    "model.joblib",
    "manifest.json",
    "feature-schema.json",
    "label-schema.json",
    "normalization.json",
)


class InferenceModelError(RuntimeError):
    pass


def list_inference_models(
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    current = get_selected_model(settings, required=False)
    selected_id = current["modelId"] if current else None
    models: list[dict[str, Any]] = []
    for model in list_offline_models(settings):
        try:
            compatibility = check_model_compatibility(
                model["modelId"], settings
            )
        except InferenceModelError as exc:
            compatibility = {
                "compatible": False,
                "errors": [str(exc)],
                "warnings": [],
            }
        models.append(
            {
                **model,
                "selected": model["modelId"] == selected_id,
                "compatibility": compatibility,
            }
        )
    return {"selectedModel": current, "models": models}


def check_model_compatibility(
    model_id: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    try:
        model = get_offline_model(model_id, settings)
    except OfflineTrainingError as exc:
        raise InferenceModelError(str(exc)) from exc
    model_dir = Path(model["artifactPath"])
    errors: list[str] = []
    warnings: list[str] = []
    missing = [
        name for name in REQUIRED_MODEL_FILES
        if not (model_dir / name).is_file()
    ]
    if missing:
        errors.append("缺少模型文件：" + "、".join(missing))
        return {
            "modelId": model_id,
            "compatible": False,
            "errors": errors,
            "warnings": warnings,
            "artifactPath": str(model_dir),
        }

    manifest = read_json(model_dir / "manifest.json")
    feature_schema = read_json(model_dir / "feature-schema.json")
    label_schema = read_json(model_dir / "label-schema.json")
    normalization = read_json(model_dir / "normalization.json")
    artifact = joblib.load(model_dir / "model.joblib")
    grid_manifest, grid_dir = resolve_active_grid_package(settings)
    dataset = read_json(Path(manifest["datasetManifestPath"]))
    current_graph = _build_graph(read_json(grid_dir / "topology.json"))

    expected = {
        "gridId": grid_manifest["gridId"],
        "topologyVersion": grid_manifest["topologyVersion"],
        "graphSignature": current_graph["signature"],
        "featureSchemaVersion": feature_schema["featureSchemaVersion"],
    }
    actual = {
        "gridId": manifest.get("gridId"),
        "topologyVersion": manifest.get("topologyVersion"),
        "graphSignature": manifest.get("graphSignature"),
        "featureSchemaVersion": manifest.get("featureSchemaVersion"),
    }
    for key, expected_value in expected.items():
        if actual.get(key) != expected_value:
            errors.append(
                f"{key}不兼容：模型={actual.get(key)!r}，当前={expected_value!r}"
            )

    feature_names = [
        item["name"] for item in feature_schema.get("features", [])
    ]
    if feature_names != artifact.get("featureNames"):
        errors.append("feature-schema.json与model.joblib特征顺序不一致")
    if len(feature_names) != manifest.get("featureCount"):
        errors.append("模型Manifest中的featureCount不一致")
    normalized_names = list(normalization.get("features", {}))
    if normalized_names != feature_names:
        errors.append("normalization.json特征顺序与Feature Schema不一致")
    label_order = manifest.get("labelOrder", [])
    if label_order != artifact.get("labelOrder"):
        errors.append("Manifest与model.joblib标签顺序不一致")
    schema_labels = label_schema.get("labels", [])
    if set(schema_labels) != set(label_order):
        errors.append("label-schema.json与模型标签集合不一致")
    expected_hash = (
        manifest.get("files", {})
        .get("model.joblib", {})
        .get("sha256")
    )
    actual_hash = sha256(model_dir / "model.joblib")
    if expected_hash and expected_hash != actual_hash:
        errors.append("model.joblib的SHA-256与Manifest不一致")
    if not manifest.get("qualifiedForOnlineIntegration", False):
        warnings.append("该模型未通过离线合格门槛")
    if dataset.get("graphSignature") != current_graph["signature"]:
        errors.append("模型数据集图签名与当前P1拓扑不一致")

    return {
        "modelId": model_id,
        "compatible": not errors,
        "errors": errors,
        "warnings": warnings,
        "artifactPath": str(model_dir),
        "gridId": actual["gridId"],
        "topologyVersion": actual["topologyVersion"],
        "graphSignature": actual["graphSignature"],
        "featureSchemaVersion": actual["featureSchemaVersion"],
        "featureCount": len(feature_names),
        "labelOrder": label_order,
        "modelSha256": actual_hash,
    }


def select_inference_model(
    model_id: str,
    *,
    actor: str = "manual",
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    compatibility = check_model_compatibility(model_id, settings)
    if not compatibility["compatible"]:
        raise InferenceModelError(
            "模型不兼容：" + "；".join(compatibility["errors"])
        )
    previous = get_selected_model(settings, required=False)
    selection = {
        "selectionSchemaVersion": "manual-model-selection-v1",
        "modelId": model_id,
        "modelDir": compatibility["artifactPath"],
        "selectedAt": datetime.now(UTC).isoformat(),
        "selectedBy": actor,
        "compatibility": compatibility,
    }
    config_path = settings.resolved_inference_model_config
    config_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(config_path, selection)
    audit = _write_history(
        "SELECT",
        previous,
        selection,
        actor,
        settings,
    )
    return {**selection, "historyPath": audit}


def rollback_inference_model(
    *,
    actor: str = "manual",
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    current = get_selected_model(settings, required=True)
    history = _read_history(settings)
    previous_model_id: str | None = None
    for item in reversed(history):
        candidate = item.get("previousModelId")
        if candidate and candidate != current["modelId"]:
            previous_model_id = candidate
            break
    if previous_model_id is None:
        raise InferenceModelError("没有可回滚的上一次人工模型选择")
    compatibility = check_model_compatibility(
        previous_model_id, settings
    )
    if not compatibility["compatible"]:
        raise InferenceModelError(
            "回滚目标已不兼容：" + "；".join(compatibility["errors"])
        )
    result = {
        "selectionSchemaVersion": "manual-model-selection-v1",
        "modelId": previous_model_id,
        "modelDir": compatibility["artifactPath"],
        "selectedAt": datetime.now(UTC).isoformat(),
        "selectedBy": actor,
        "compatibility": compatibility,
    }
    config_path = settings.resolved_inference_model_config
    config_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(config_path, result)
    result["historyPath"] = _write_history(
        "ROLLBACK", current, result, actor, settings
    )
    return result


def get_selected_model(
    settings: GridSettings | None = None,
    *,
    required: bool = True,
) -> dict[str, Any] | None:
    settings = settings or get_grid_settings()
    override = os.getenv("DONGJIN_INFERENCE_MODEL_DIR")
    if override:
        path = Path(override).expanduser().resolve()
        if not (path / "manifest.json").is_file():
            raise InferenceModelError(
                "DONGJIN_INFERENCE_MODEL_DIR不包含manifest.json"
            )
        manifest = read_json(path / "manifest.json")
        return {
            "selectionSchemaVersion": "manual-model-selection-v1",
            "modelId": manifest["modelId"],
            "modelDir": str(path),
            "selectedAt": None,
            "selectedBy": "environment-override",
        }
    path = settings.resolved_inference_model_config
    if not path.is_file():
        if required:
            raise InferenceModelError(
                "尚未选择在线模型，请先执行select_inference_model.py"
            )
        return None
    selection = read_json(path)
    model_dir = Path(selection["modelDir"])
    if not model_dir.is_dir():
        raise InferenceModelError(
            f"已选模型目录不存在：{model_dir}"
        )
    return selection


def load_selected_model(
    settings: GridSettings | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    settings = settings or get_grid_settings()
    selection = get_selected_model(settings, required=True)
    compatibility = check_model_compatibility(
        selection["modelId"], settings
    )
    if not compatibility["compatible"]:
        raise InferenceModelError(
            "当前模型不兼容：" + "；".join(compatibility["errors"])
        )
    model_dir = Path(selection["modelDir"])
    return (
        selection,
        read_json(model_dir / "manifest.json"),
        joblib.load(model_dir / "model.joblib"),
    )


def model_selection_history(
    settings: GridSettings | None = None,
) -> list[dict[str, Any]]:
    return _read_history(settings or get_grid_settings())


def _write_history(
    action: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    actor: str,
    settings: GridSettings,
) -> str:
    root = settings.resolved_model_history_dir
    root.mkdir(parents=True, exist_ok=True)
    record = {
        "historySchemaVersion": "model-selection-history-v1",
        "historyId": f"model-history-{uuid.uuid4().hex}",
        "action": action,
        "recordedAt": datetime.now(UTC).isoformat(),
        "actor": actor,
        "previousModelId": previous.get("modelId") if previous else None,
        "modelId": current["modelId"],
        "modelDir": current["modelDir"],
    }
    path = root / f"{record['recordedAt'].replace(':', '')}-{record['historyId']}.json"
    write_json(path, record)
    return str(path)


def _read_history(settings: GridSettings) -> list[dict[str, Any]]:
    root = settings.resolved_model_history_dir
    if not root.is_dir():
        return []
    return [
        read_json(path)
        for path in sorted(root.glob("*.json"))
    ]
