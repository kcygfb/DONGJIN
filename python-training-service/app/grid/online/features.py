from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.grid.artifact_service import resolve_active_grid_package
from app.grid.datasets.builder import _features
from app.grid.offline_io import read_json
from app.grid.settings import GridSettings, get_grid_settings


class FeatureAdapterError(RuntimeError):
    pass


def snapshot_to_measurement(snapshot: dict[str, Any]) -> dict[str, Any]:
    objects: dict[str, dict[str, Any]] = {}

    def add(
        element_type: str,
        values_by_id: dict[str, Any],
        transform,
    ) -> None:
        for business_id, raw in values_by_id.items():
            objects[business_id] = {
                "businessId": business_id,
                "elementType": element_type,
                "qualityCode": "GOOD",
                "values": transform(raw),
            }

    add("bus", snapshot.get("buses", {}), lambda value: dict(value))
    add(
        "line",
        snapshot.get("lines", {}),
        lambda value: {
            **value,
            "lossMw": value.get("plMw"),
            "inService": True,
        },
    )
    add(
        "trafo",
        snapshot.get("transformers", {}),
        lambda value: {
            **value,
            "lossMw": value.get("plMw"),
            "inService": True,
        },
    )
    add(
        "switch",
        snapshot.get("switches", {}),
        lambda value: dict(value),
    )
    add(
        "load",
        snapshot.get("loads", {}),
        lambda value: {**value, "inService": True, "scaling": 1.0},
    )
    add(
        "sgen",
        snapshot.get("generators", {}),
        lambda value: {**value, "inService": True, "scaling": 1.0},
    )
    add(
        "ext_grid",
        snapshot.get("externalGrids", {}),
        lambda value: {**value, "inService": True},
    )
    return {
        "measurementSchemaVersion": "grid-measurement-v1",
        "frameId": snapshot["snapshotId"],
        "gridId": snapshot["gridId"],
        "topologyVersion": snapshot["topologyVersion"],
        "measurementTime": snapshot["simulationTime"],
        "arrivalTime": snapshot.get("publishedAt"),
        "generatedAt": datetime.now(UTC).isoformat(),
        "sourceMode": "ONLINE",
        "objects": objects,
    }


def adapt_measurement(
    measurement: dict[str, Any],
    model_manifest: dict[str, Any],
    model_artifact: dict[str, Any],
    *,
    source_mode: str,
    baseline: dict[str, Any] | None = None,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    dataset_manifest_path = Path(model_manifest["datasetManifestPath"])
    dataset_dir = dataset_manifest_path.parent
    graph = read_json(dataset_dir / "graph.json")
    if measurement.get("gridId") != model_manifest.get("gridId"):
        raise FeatureAdapterError("输入gridId与模型不一致")
    if (
        measurement.get("topologyVersion")
        != model_manifest.get("topologyVersion")
    ):
        raise FeatureAdapterError("输入topologyVersion与模型不一致")
    if graph.get("signature") != model_manifest.get("graphSignature"):
        raise FeatureAdapterError("数据集graphSignature与模型不一致")

    feature_names = list(model_artifact["featureNames"])
    measured_objects = measurement.get("objects", {})
    baseline_objects = (
        baseline.get("objects", {})
        if baseline
        else _p1_baseline_objects(settings)
    )
    rows: list[dict[str, Any]] = []
    raw_matrix: list[list[float]] = []
    missing_objects = 0
    for vertex in graph["vertices"]:
        business_id = vertex["businessId"]
        measured = measured_objects.get(business_id)
        if measured is None:
            missing_objects += 1
            measured = {"qualityCode": "MISSING", "values": {}}
        feature_values = _features(
            vertex,
            measured,
            baseline_objects.get(business_id, {"values": {}}),
        )
        try:
            ordered = [float(feature_values[name]) for name in feature_names]
        except KeyError as exc:
            raise FeatureAdapterError(
                f"在线适配器缺少模型特征：{exc.args[0]}"
            ) from exc
        raw_matrix.append(ordered)
        rows.append(
            {
                "vertexIndex": vertex["index"],
                "businessId": business_id,
                "elementType": vertex["elementType"],
                "qualityCode": measured.get(
                    "qualityCode", "MISSING"
                ),
                **{
                    f"feature.{name}": value
                    for name, value in zip(feature_names, ordered)
                },
            }
        )
    raw = np.asarray(raw_matrix, dtype=np.float32)
    means = np.asarray(
        model_artifact["normalizationMean"], dtype=np.float32
    )
    deviations = np.asarray(
        model_artifact["normalizationStandardDeviation"],
        dtype=np.float32,
    )
    deviations = np.where(deviations < 1e-8, 1.0, deviations)
    normalized = ((raw - means) / deviations).astype(np.float32)
    frame_id = (
        f"feature-{source_mode.lower()}-"
        f"{measurement.get('frameId', 'unknown')}"
    )
    return {
        "featureSchemaVersion": "grid-feature-frame-v2",
        "modelFeatureSchemaVersion": model_manifest[
            "featureSchemaVersion"
        ],
        "frameId": frame_id,
        "sourceFrameId": measurement.get("frameId"),
        "sourceMode": source_mode,
        "gridId": measurement["gridId"],
        "topologyVersion": measurement["topologyVersion"],
        "graphSignature": graph["signature"],
        "simulationTime": measurement.get(
            "measurementTime",
            measurement.get("simulationTime"),
        ),
        "generatedAt": datetime.now(UTC).isoformat(),
        "featureNames": feature_names,
        "rows": rows,
        "rawMatrix": raw,
        "normalizedMatrix": normalized,
        "graph": graph,
        "missingObjectCount": missing_objects,
    }


def feature_frame_json(frame: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in frame.items()
        if key not in {"rawMatrix", "normalizedMatrix", "graph"}
    }


def _p1_baseline_objects(
    settings: GridSettings,
) -> dict[str, dict[str, Any]]:
    _, artifact_dir = resolve_active_grid_package(settings)
    baseline = read_json(artifact_dir / "baseline-results.json")
    mappings = {
        "res_bus": (
            "bus",
            {
                "vm_pu": "vmPu",
                "va_degree": "vaDegree",
                "p_mw": "pMw",
                "q_mvar": "qMvar",
            },
        ),
        "res_line": (
            "line",
            {
                "p_from_mw": "pFromMw",
                "q_from_mvar": "qFromMvar",
                "p_to_mw": "pToMw",
                "q_to_mvar": "qToMvar",
                "i_from_ka": "iFromKa",
                "i_to_ka": "iToKa",
                "loading_percent": "loadingPercent",
                "pl_mw": "lossMw",
            },
        ),
        "res_trafo": (
            "trafo",
            {
                "p_hv_mw": "pHvMw",
                "q_hv_mvar": "qHvMvar",
                "p_lv_mw": "pLvMw",
                "q_lv_mvar": "qLvMvar",
                "i_hv_ka": "iHvKa",
                "i_lv_ka": "iLvKa",
                "loading_percent": "loadingPercent",
                "pl_mw": "lossMw",
            },
        ),
        "res_ext_grid": (
            "ext_grid",
            {"p_mw": "pMw", "q_mvar": "qMvar"},
        ),
    }
    objects: dict[str, dict[str, Any]] = {}
    for table_name, (_, columns) in mappings.items():
        for item in baseline.get("tables", {}).get(table_name, []):
            objects[item["businessId"]] = {
                "businessId": item["businessId"],
                "values": {
                    target: item.get("values", {}).get(source)
                    for source, target in columns.items()
                },
            }
    return objects
