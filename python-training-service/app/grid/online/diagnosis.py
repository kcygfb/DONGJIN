from __future__ import annotations

import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.grid.offline_io import write_csv, write_json
from app.grid.online.features import (
    adapt_measurement,
    feature_frame_json,
    snapshot_to_measurement,
)
from app.grid.online.model_manager import load_selected_model
from app.grid.settings import GridSettings, get_grid_settings
from app.grid.training.trainer import _predict, _propagation


class OnlineDiagnosisError(RuntimeError):
    pass


def diagnose_snapshot(
    snapshot: dict[str, Any],
    *,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    return diagnose_measurement(
        snapshot_to_measurement(snapshot),
        source_mode="ONLINE",
        settings=settings,
    )


def diagnose_measurement(
    measurement: dict[str, Any],
    *,
    source_mode: str,
    baseline: dict[str, Any] | None = None,
    truth_reference: str | None = None,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    started = time.perf_counter()
    selection, manifest, artifact = load_selected_model(settings)
    adaptation_started = time.perf_counter()
    frame = adapt_measurement(
        measurement,
        manifest,
        artifact,
        source_mode=source_mode,
        baseline=baseline,
        settings=settings,
    )
    adaptation_ms = (
        time.perf_counter() - adaptation_started
    ) * 1000
    missing_ratio = (
        frame["missingObjectCount"] / len(frame["rows"])
        if frame["rows"]
        else 1.0
    )
    diagnosis_id = (
        f"diagnosis-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    if missing_ratio > 0.35:
        result = _insufficient_result(
            diagnosis_id,
            selection,
            frame,
            missing_ratio,
            adaptation_ms,
        )
        return _archive(
            result, frame, measurement, None, settings, truth_reference
        )

    inference_started = time.perf_counter()
    probabilities = _predict(
        frame["normalizedMatrix"][np.newaxis, :, :],
        _propagation(frame["graph"]),
        artifact["state"],
    )[0]
    inference_ms = (time.perf_counter() - inference_started) * 1000
    labels = list(artifact["labelOrder"])
    normal_index = labels.index("NORMAL")
    anomaly_scores = 1.0 - probabilities[:, normal_index]
    threshold = float(artifact["anomalyThreshold"])
    ranked_indexes = np.argsort(-anomaly_scores)
    top_indexes = ranked_indexes[: min(5, len(ranked_indexes))]
    best_index = int(top_indexes[0])
    best_fault_index = max(
        (
            index for index, label in enumerate(labels)
            if label != "NORMAL"
        ),
        key=lambda index: probabilities[best_index, index],
    )
    is_anomaly = float(anomaly_scores[best_index]) >= threshold
    predicted_type = (
        labels[best_fault_index] if is_anomaly else "NORMAL"
    )
    target = (
        frame["rows"][best_index]["businessId"]
        if is_anomaly
        else None
    )
    rules = _rule_checks(measurement)
    trace = (
        _trace(frame["graph"], target, depth=4)
        if target
        else {"upstream": [], "downstream": [], "neighbors": []}
    )
    type_candidates = sorted(
        (
            {
                "eventType": label,
                "confidence": round(
                    float(probabilities[best_index, index]), 8
                ),
            }
            for index, label in enumerate(labels)
            if label != "NORMAL"
        ),
        key=lambda item: item["confidence"],
        reverse=True,
    )[:5]
    location_candidates = []
    for index in top_indexes:
        index = int(index)
        fault_index = max(
            (
                label_index
                for label_index, label in enumerate(labels)
                if label != "NORMAL"
            ),
            key=lambda label_index: probabilities[index, label_index],
        )
        location_candidates.append(
            {
                "businessId": frame["rows"][index]["businessId"],
                "elementType": frame["rows"][index]["elementType"],
                "predictedEventType": labels[fault_index],
                "anomalyScore": round(
                    float(anomaly_scores[index]), 8
                ),
                "confidence": round(
                    float(probabilities[index, fault_index]), 8
                ),
            }
        )
    latency = (time.perf_counter() - started) * 1000
    result = {
        "diagnosisSchemaVersion": "grid-diagnosis-result-v1",
        "diagnosisId": diagnosis_id,
        "status": "ANOMALY" if is_anomaly else "NORMAL",
        "modelId": selection["modelId"],
        "frameId": frame["frameId"],
        "sourceFrameId": frame["sourceFrameId"],
        "mode": source_mode,
        "isAnomaly": is_anomaly,
        "predictedEventType": predicted_type,
        "targetBusinessId": target,
        "confidence": (
            round(float(probabilities[best_index, best_fault_index]), 8)
            if is_anomaly
            else round(float(probabilities[best_index, normal_index]), 8)
        ),
        "anomalyScore": round(float(anomaly_scores[best_index]), 8),
        "anomalyThreshold": threshold,
        "locationCandidates": location_candidates,
        "typeCandidates": type_candidates,
        "modelLabelOrder": labels,
        "affectedObjects": _affected_objects(rules),
        "upstreamTrace": trace["upstream"],
        "downstreamTrace": trace["downstream"],
        "neighborTrace": trace["neighbors"],
        "ruleChecks": rules,
        "warnings": (
            [f"缺失对象比例为{missing_ratio:.2%}"]
            if missing_ratio
            else []
        ),
        "summary": _summary(
            is_anomaly,
            predicted_type,
            target,
            float(anomaly_scores[best_index]),
            rules,
        ),
        "latencyMs": round(latency, 3),
        "performance": {
            "featureAdaptationMs": round(adaptation_ms, 3),
            "inferenceMs": round(inference_ms, 3),
            "totalMs": round(latency, 3),
        },
        "generatedAt": datetime.now(UTC).isoformat(),
    }
    return _archive(
        result,
        frame,
        measurement,
        probabilities,
        settings,
        truth_reference,
    )


def _rule_checks(measurement: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for business_id, item in measurement.get("objects", {}).items():
        values = item.get("values", {})
        vm_pu = values.get("vmPu")
        loading = values.get("loadingPercent")
        quality = item.get("qualityCode", "MISSING")
        if isinstance(vm_pu, (int, float)) and (
            vm_pu < 0.95 or vm_pu > 1.05
        ):
            checks.append(
                {
                    "rule": "BUS_VOLTAGE_LIMIT",
                    "businessId": business_id,
                    "triggered": True,
                    "value": vm_pu,
                    "limits": [0.95, 1.05],
                }
            )
        if isinstance(loading, (int, float)) and loading > 100:
            checks.append(
                {
                    "rule": "THERMAL_LOADING_LIMIT",
                    "businessId": business_id,
                    "triggered": True,
                    "value": loading,
                    "limit": 100,
                }
            )
        if quality != "GOOD":
            checks.append(
                {
                    "rule": "MEASUREMENT_QUALITY",
                    "businessId": business_id,
                    "triggered": True,
                    "qualityCode": quality,
                }
            )
        if values.get("inService") is False or values.get("closed") is False:
            checks.append(
                {
                    "rule": "DEVICE_STATE",
                    "businessId": business_id,
                    "triggered": True,
                    "inService": values.get("inService"),
                    "closed": values.get("closed"),
                }
            )
    return checks


def _trace(
    graph: dict[str, Any],
    target: str,
    *,
    depth: int,
) -> dict[str, Any]:
    adjacency: dict[str, list[str]] = {
        item["businessId"]: [] for item in graph["vertices"]
    }
    for edge in graph["edges"]:
        source = edge["sourceBusinessId"]
        target_id = edge["targetBusinessId"]
        adjacency[source].append(target_id)
        adjacency[target_id].append(source)
    distances = {target: 0}
    queue = deque([target])
    neighbors: list[dict[str, Any]] = []
    while queue:
        current = queue.popleft()
        if distances[current] >= depth:
            continue
        for neighbor in adjacency.get(current, []):
            if neighbor in distances:
                continue
            distances[neighbor] = distances[current] + 1
            queue.append(neighbor)
            neighbors.append(
                {
                    "businessId": neighbor,
                    "depth": distances[neighbor],
                    "viaBusinessId": current,
                }
            )
    ext_grids = {
        item["businessId"]
        for item in graph["vertices"]
        if item["elementType"] == "ext_grid"
    }
    loads = {
        item["businessId"]
        for item in graph["vertices"]
        if item["elementType"] in {"load", "sgen"}
    }
    return {
        "upstream": [
            item for item in neighbors if item["businessId"] in ext_grids
        ],
        "downstream": [
            item for item in neighbors if item["businessId"] in loads
        ],
        "neighbors": neighbors,
    }


def _affected_objects(rules: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            item["businessId"]
            for item in rules
            if item.get("triggered")
        }
    )


def _summary(
    is_anomaly: bool,
    event_type: str,
    target: str | None,
    anomaly_score: float,
    rules: list[dict[str, Any]],
) -> str:
    if not is_anomaly:
        return (
            f"当前帧异常分数{anomaly_score:.4f}未超过模型阈值，"
            f"规则层发现{len(rules)}项需要关注的事实。"
        )
    return (
        f"模型判断最可能事件为{event_type}，根因候选为{target}，"
        f"异常分数{anomaly_score:.4f}；规则层发现{len(rules)}项事实。"
    )


def _insufficient_result(
    diagnosis_id: str,
    selection: dict[str, Any],
    frame: dict[str, Any],
    missing_ratio: float,
    adaptation_ms: float,
) -> dict[str, Any]:
    return {
        "diagnosisSchemaVersion": "grid-diagnosis-result-v1",
        "diagnosisId": diagnosis_id,
        "status": "INSUFFICIENT_DATA",
        "modelId": selection["modelId"],
        "frameId": frame["frameId"],
        "sourceFrameId": frame["sourceFrameId"],
        "mode": frame["sourceMode"],
        "isAnomaly": None,
        "predictedEventType": None,
        "targetBusinessId": None,
        "locationCandidates": [],
        "typeCandidates": [],
        "affectedObjects": [],
        "upstreamTrace": [],
        "downstreamTrace": [],
        "ruleChecks": [],
        "warnings": [f"缺失对象比例{missing_ratio:.2%}超过35%门槛"],
        "summary": "输入数据不足，系统拒绝伪造研判结论。",
        "latencyMs": round(adaptation_ms, 3),
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def _archive(
    result: dict[str, Any],
    frame: dict[str, Any],
    measurement: dict[str, Any],
    probabilities: np.ndarray | None,
    settings: GridSettings,
    truth_reference: str | None,
) -> dict[str, Any]:
    root = settings.resolved_diagnosis_dir / result["diagnosisId"]
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "input-measurement.json", measurement)
    write_json(root / "feature-frame.json", feature_frame_json(frame))
    write_csv(
        root / "feature-frame.csv",
        frame["rows"],
        fieldnames=list(frame["rows"][0]) if frame["rows"] else [],
    )
    if probabilities is not None:
        probability_rows = []
        label_names = result["modelLabelOrder"]
        for row, values in zip(frame["rows"], probabilities):
            probability_rows.append(
                {
                    "businessId": row["businessId"],
                    **{
                        f"probability.{label}": float(values[index])
                        for index, label in enumerate(label_names)
                        if index < len(values)
                    },
                }
            )
        write_csv(
            root / "probabilities.csv",
            probability_rows,
            fieldnames=(
                list(probability_rows[0]) if probability_rows else []
            ),
        )
    result["artifactPath"] = str(root)
    result["truthReference"] = truth_reference
    write_json(root / "result.json", result)
    (root / "README.md").write_text(
        "# 在线错误研判白箱档案\n\n"
        f"- 研判ID：`{result['diagnosisId']}`\n"
        f"- 模型：`{result['modelId']}`\n"
        f"- 输入帧：`{result['sourceFrameId']}`\n"
        f"- 模式：`{result['mode']}`\n\n"
        "目录保留原始量测、48维特征、逐顶点概率和最终研判结果。\n",
        encoding="utf-8",
    )
    return result
