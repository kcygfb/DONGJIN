from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from scipy.sparse import csr_matrix
from sklearn.metrics import classification_report, confusion_matrix

from app.grid.datasets.builder import get_dataset
from app.grid.offline_io import (
    file_manifest,
    read_json,
    safe_child,
    write_csv,
    write_json,
)
from app.grid.settings import GridSettings, get_grid_settings


MODEL_SCHEMA = "grid-gnn-model-v1"


class OfflineTrainingError(RuntimeError):
    pass


class OfflineTrainingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_id: str = Field(alias="datasetId", min_length=1)
    model_id: str | None = Field(default=None, alias="modelId")
    random_seed: int = Field(default=20260723, alias="randomSeed")
    hidden_units: int = Field(default=32, alias="hiddenUnits", ge=8, le=256)
    maximum_epochs: int = Field(
        default=120,
        alias="maximumEpochs",
        ge=10,
        le=1000,
    )
    learning_rate: float = Field(
        default=0.01,
        alias="learningRate",
        gt=0,
        le=1,
    )
    minimum_target_macro_f1: float = Field(
        default=0.7,
        alias="minimumTargetMacroF1",
        ge=0,
        le=1,
    )
    minimum_location_top5_accuracy: float = Field(
        default=0.7,
        alias="minimumLocationTop5Accuracy",
        ge=0,
        le=1,
    )
    minimum_exact_diagnosis_accuracy: float = Field(
        default=0.65,
        alias="minimumExactDiagnosisAccuracy",
        ge=0,
        le=1,
    )
    minimum_normal_graph_accuracy: float = Field(
        default=0.8,
        alias="minimumNormalGraphAccuracy",
        ge=0,
        le=1,
    )
    minimum_fault_detection_recall: float = Field(
        default=0.75,
        alias="minimumFaultDetectionRecall",
        ge=0,
        le=1,
    )


def train_offline_model(
    request: OfflineTrainingRequest,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    dataset = get_dataset(request.dataset_id, settings)
    dataset_dir = Path(dataset["artifactPath"])
    graph = read_json(dataset_dir / "graph.json")
    feature_schema = read_json(
        dataset_dir / "feature-schema.json"
    )
    label_schema = read_json(dataset_dir / "label-schema.json")
    normalization = read_json(dataset_dir / "normalization.json")
    feature_names = dataset["featureNames"]
    feature_columns = [f"feature.{name}" for name in feature_names]
    label_order = ["NORMAL"] + sorted(
        label
        for label in dataset["labelOrder"]
        if label != "NORMAL"
    )
    label_to_index = {
        label: index for index, label in enumerate(label_order)
    }
    splits = {
        name: _load_split(
            dataset_dir / f"{name}.parquet",
            feature_columns,
            label_to_index,
            len(graph["vertices"]),
        )
        for name in ("train", "validation", "test")
    }
    if not splits["train"]["features"].size:
        raise OfflineTrainingError("训练集为空")

    means = np.asarray(
        [
            normalization["features"][name]["mean"]
            for name in feature_names
        ],
        dtype=np.float32,
    )
    standard_deviations = np.asarray(
        [
            normalization["features"][name]["standardDeviation"]
            for name in feature_names
        ],
        dtype=np.float32,
    )
    standard_deviations = np.where(
        standard_deviations < 1e-8,
        1.0,
        standard_deviations,
    )
    for split in splits.values():
        split["features"] = (
            (split["features"] - means) / standard_deviations
        ).astype(np.float32)

    propagation = _propagation(graph)
    state, history = _train_gcn(
        splits["train"]["features"],
        splits["train"]["labels"],
        splits["validation"]["features"],
        splits["validation"]["labels"],
        propagation,
        len(label_order),
        request,
    )
    validation_probabilities = _predict(
        splits["validation"]["features"],
        propagation,
        state,
    )
    training_probabilities = _predict(
        splits["train"]["features"],
        propagation,
        state,
    )
    test_probabilities = _predict(
        splits["test"]["features"],
        propagation,
        state,
    )
    anomaly_threshold = _calibrate_anomaly_threshold(
        [
            (splits["train"], training_probabilities),
            (splits["validation"], validation_probabilities),
        ],
        label_order,
    )
    validation_metrics, validation_predictions = _evaluate(
        splits["validation"],
        validation_probabilities,
        label_order,
        anomaly_threshold,
    )
    test_metrics, test_predictions = _evaluate(
        splits["test"],
        test_probabilities,
        label_order,
        anomaly_threshold,
    )
    qualified = (
        test_metrics["targetMacroF1"]
        >= request.minimum_target_macro_f1
        and test_metrics["locationTop5Accuracy"]
        >= request.minimum_location_top5_accuracy
        and test_metrics["exactDiagnosisAccuracy"]
        >= request.minimum_exact_diagnosis_accuracy
        and test_metrics["normalGraphAccuracy"]
        >= request.minimum_normal_graph_accuracy
        and test_metrics["faultDetectionRecall"]
        >= request.minimum_fault_detection_recall
    )
    model_id = request.model_id or (
        f"offline-gcn-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    root = settings.resolved_offline_model_dir
    root.mkdir(parents=True, exist_ok=True)
    try:
        final_dir = safe_child(root, model_id)
    except ValueError as exc:
        raise OfflineTrainingError(str(exc)) from exc
    if final_dir.exists():
        raise OfflineTrainingError(f"模型产物已存在：{model_id}")
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{model_id}-", dir=root)
    )
    try:
        joblib.dump(
            {
                "modelSchemaVersion": MODEL_SCHEMA,
                "modelType": "GCN_NODE_CLASSIFIER",
                "state": state,
                "labelOrder": label_order,
                "featureNames": feature_names,
                "normalizationMean": means,
                "normalizationStandardDeviation": standard_deviations,
                "anomalyThreshold": anomaly_threshold,
                "graphSignature": dataset["graphSignature"],
                "gridId": dataset["gridId"],
                "topologyVersion": dataset["topologyVersion"],
            },
            temporary_dir / "model.joblib",
        )
        write_json(
            temporary_dir / "metrics.json",
            {
                "validation": validation_metrics,
                "test": test_metrics,
                "qualificationThresholds": {
                    "minimumTargetMacroF1": (
                        request.minimum_target_macro_f1
                    ),
                    "minimumLocationTop5Accuracy": (
                        request.minimum_location_top5_accuracy
                    ),
                    "minimumExactDiagnosisAccuracy": (
                        request.minimum_exact_diagnosis_accuracy
                    ),
                    "minimumNormalGraphAccuracy": (
                        request.minimum_normal_graph_accuracy
                    ),
                    "minimumFaultDetectionRecall": (
                        request.minimum_fault_detection_recall
                    ),
                },
                "validationCalibratedAnomalyThreshold": (
                    anomaly_threshold
                ),
                "qualified": qualified,
            },
        )
        write_json(
            temporary_dir / "training-config.json",
            request.model_dump(by_alias=True),
        )
        write_json(
            temporary_dir / "feature-schema.json",
            feature_schema,
        )
        write_json(
            temporary_dir / "label-schema.json",
            label_schema,
        )
        write_json(
            temporary_dir / "normalization.json",
            normalization,
        )
        write_csv(
            temporary_dir / "training-history.csv",
            history,
            fieldnames=[
                "epoch",
                "trainingLoss",
                "validationLoss",
                "best",
            ],
        )
        write_csv(
            temporary_dir / "validation-predictions.csv",
            validation_predictions,
            fieldnames=_prediction_columns(),
        )
        write_csv(
            temporary_dir / "test-predictions.csv",
            test_predictions,
            fieldnames=_prediction_columns(),
        )
        (temporary_dir / "README.md").write_text(
            _model_readme(
                model_id,
                dataset,
                request,
                validation_metrics,
                test_metrics,
                qualified,
            ),
            encoding="utf-8",
        )
        manifest = {
            "modelId": model_id,
            "modelSchemaVersion": MODEL_SCHEMA,
            "modelType": "GCN_NODE_CLASSIFIER",
            "status": "QUALIFIED" if qualified else "REJECTED",
            "qualifiedForOnlineIntegration": qualified,
            "createdAt": datetime.now(UTC).isoformat(),
            "datasetId": dataset["datasetId"],
            "datasetManifestPath": dataset["manifestPath"],
            "datasetSchemaVersion": dataset["datasetSchemaVersion"],
            "gridId": dataset["gridId"],
            "topologyVersion": dataset["topologyVersion"],
            "graphSignature": dataset["graphSignature"],
            "featureSchemaVersion": feature_schema[
                "featureSchemaVersion"
            ],
            "labelSchemaVersion": label_schema[
                "labelSchemaVersion"
            ],
            "randomSeed": request.random_seed,
            "anomalyThreshold": anomaly_threshold,
            "featureCount": len(feature_names),
            "labelOrder": label_order,
            "architecture": {
                "type": "two-layer-gcn",
                "inputUnits": len(feature_names),
                "hiddenUnits": request.hidden_units,
                "outputUnits": len(label_order),
                "messagePassingLayers": 2,
            },
            "epochsCompleted": len(history),
            "validationMetrics": validation_metrics,
            "testMetrics": test_metrics,
            "whiteBoxFiles": {
                "readme": "README.md",
                "metrics": "metrics.json",
                "trainingConfig": "training-config.json",
                "trainingHistory": "training-history.csv",
                "validationPredictions": (
                    "validation-predictions.csv"
                ),
                "testPredictions": "test-predictions.csv",
                "featureSchema": "feature-schema.json",
                "labelSchema": "label-schema.json",
                "normalization": "normalization.json",
            },
            "modelFile": "model.joblib",
            "files": file_manifest(temporary_dir),
        }
        write_json(temporary_dir / "manifest.json", manifest)
        os.replace(temporary_dir, final_dir)
        return _model_response(manifest, final_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def list_offline_models(
    settings: GridSettings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_grid_settings()
    root = settings.resolved_offline_model_dir
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.iterdir(), reverse=True):
        manifest_path = path / "manifest.json"
        if manifest_path.is_file():
            result.append(
                _model_response(read_json(manifest_path), path)
            )
    return result


def get_offline_model(
    model_id: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    try:
        path = safe_child(
            settings.resolved_offline_model_dir,
            model_id,
        )
    except ValueError as exc:
        raise OfflineTrainingError(str(exc)) from exc
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise OfflineTrainingError(f"模型不存在：{model_id}")
    return _model_response(read_json(manifest_path), path)


def _load_split(
    path: Path,
    feature_columns: list[str],
    label_to_index: dict[str, int],
    vertex_count: int,
) -> dict[str, Any]:
    frame = pd.read_parquet(path)
    if frame.empty:
        return {
            "features": np.empty(
                (0, vertex_count, len(feature_columns)),
                dtype=np.float32,
            ),
            "labels": np.empty(
                (0, vertex_count),
                dtype=np.int64,
            ),
            "targets": np.empty(
                (0, vertex_count),
                dtype=bool,
            ),
            "sampleIds": [],
            "scenarioLabels": [],
            "businessIds": [],
        }
    groups = []
    for sample_id, group in frame.groupby("sampleId", sort=True):
        ordered = group.sort_values("vertexIndex")
        if len(ordered) != vertex_count:
            raise OfflineTrainingError(
                f"样本{sample_id}顶点数量不一致"
            )
        groups.append((sample_id, ordered))
    business_ids = (
        groups[0][1]["businessId"].astype(str).tolist()
    )
    return {
        "features": np.stack(
            [
                group[feature_columns].to_numpy(dtype=np.float32)
                for _, group in groups
            ]
        ),
        "labels": np.stack(
            [
                np.asarray(
                    [
                        label_to_index[value]
                        for value in group["nodeLabel"].astype(str)
                    ],
                    dtype=np.int64,
                )
                for _, group in groups
            ]
        ),
        "targets": np.stack(
            [
                group["isTarget"].astype(bool).to_numpy()
                for _, group in groups
            ]
        ),
        "sampleIds": [sample_id for sample_id, _ in groups],
        "scenarioLabels": [
            str(group["scenarioLabel"].iloc[0])
            for _, group in groups
        ],
        "businessIds": business_ids,
    }


def _propagation(graph: dict[str, Any]) -> csr_matrix:
    count = len(graph["vertices"])
    adjacency = np.eye(count, dtype=np.float32)
    for edge in graph["edges"]:
        source = int(edge["sourceIndex"])
        target = int(edge["targetIndex"])
        adjacency[source, target] = 1.0
        adjacency[target, source] = 1.0
    degrees = adjacency.sum(axis=1)
    inverse_sqrt = np.power(
        np.maximum(degrees, 1.0),
        -0.5,
    )
    normalized = (
        inverse_sqrt[:, None]
        * adjacency
        * inverse_sqrt[None, :]
    )
    return csr_matrix(normalized)


def _train_gcn(
    training_features: np.ndarray,
    training_labels: np.ndarray,
    validation_features: np.ndarray,
    validation_labels: np.ndarray,
    propagation: csr_matrix,
    class_count: int,
    request: OfflineTrainingRequest,
) -> tuple[dict[str, np.ndarray], list[dict[str, Any]]]:
    rng = np.random.default_rng(request.random_seed)
    input_units = training_features.shape[-1]
    state = {
        "w1": (
            rng.standard_normal(
                (input_units, request.hidden_units)
            )
            * np.sqrt(2.0 / max(input_units, 1))
        ).astype(np.float32),
        "b1": np.zeros(request.hidden_units, dtype=np.float32),
        "w2": (
            rng.standard_normal(
                (request.hidden_units, class_count)
            )
            * np.sqrt(2.0 / request.hidden_units)
        ).astype(np.float32),
        "b2": np.zeros(class_count, dtype=np.float32),
    }
    counts = np.bincount(
        training_labels.reshape(-1),
        minlength=class_count,
    ).astype(np.float32)
    weights = (
        training_labels.size
        / np.maximum(counts * class_count, 1.0)
    )
    weights = np.clip(weights, 0.1, 200.0).astype(np.float32)
    optimizer = _Adam(state, request.learning_rate)
    best_state = {
        name: value.copy() for name, value in state.items()
    }
    best_validation = float("inf")
    stale = 0
    history: list[dict[str, Any]] = []
    batch_size = min(8, len(training_features))
    for epoch in range(1, request.maximum_epochs + 1):
        order = rng.permutation(len(training_features))
        losses: list[float] = []
        for start in range(0, len(order), batch_size):
            indexes = order[start : start + batch_size]
            loss, gradients = _loss_and_gradients(
                training_features[indexes],
                training_labels[indexes],
                propagation,
                state,
                weights,
            )
            optimizer.step(state, gradients)
            losses.append(loss)
        training_loss = float(np.mean(losses))
        validation_loss = _loss_only(
            validation_features,
            validation_labels,
            propagation,
            state,
            weights,
        )
        improved = validation_loss < best_validation - 1e-5
        if improved:
            best_validation = validation_loss
            best_state = {
                name: value.copy()
                for name, value in state.items()
            }
            stale = 0
        else:
            stale += 1
        history.append(
            {
                "epoch": epoch,
                "trainingLoss": round(training_loss, 8),
                "validationLoss": round(validation_loss, 8),
                "best": improved,
            }
        )
        if epoch >= 40 and stale >= 20:
            break
    return best_state, history


def _forward(
    features: np.ndarray,
    propagation: csr_matrix,
    state: dict[str, np.ndarray],
) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    aggregated_input = _aggregate(features, propagation)
    hidden_linear = (
        aggregated_input @ state["w1"] + state["b1"]
    )
    hidden = np.maximum(hidden_linear, 0.0)
    aggregated_hidden = _aggregate(hidden, propagation)
    logits = aggregated_hidden @ state["w2"] + state["b2"]
    return logits, (
        aggregated_input,
        hidden_linear,
        aggregated_hidden,
    )


def _loss_and_gradients(
    features: np.ndarray,
    labels: np.ndarray,
    propagation: csr_matrix,
    state: dict[str, np.ndarray],
    class_weights: np.ndarray,
) -> tuple[float, dict[str, np.ndarray]]:
    logits, cache = _forward(features, propagation, state)
    probabilities = _softmax(logits)
    flat_labels = labels.reshape(-1)
    flat_probabilities = probabilities.reshape(
        -1,
        probabilities.shape[-1],
    )
    indexes = np.arange(len(flat_labels))
    sample_weights = class_weights[flat_labels]
    normalizer = float(sample_weights.sum())
    loss = -float(
        np.sum(
            sample_weights
            * np.log(
                np.maximum(
                    flat_probabilities[indexes, flat_labels],
                    1e-9,
                )
            )
        )
        / normalizer
    )
    gradient_logits = flat_probabilities.copy()
    gradient_logits[indexes, flat_labels] -= 1.0
    gradient_logits *= (
        sample_weights / normalizer
    )[:, None]
    gradient_logits = gradient_logits.reshape(
        probabilities.shape
    )
    aggregated_input, hidden_linear, aggregated_hidden = cache
    gradient_w2 = np.einsum(
        "bnh,bnc->hc",
        aggregated_hidden,
        gradient_logits,
    )
    gradient_b2 = gradient_logits.sum(axis=(0, 1))
    gradient_aggregated_hidden = (
        gradient_logits @ state["w2"].T
    )
    gradient_hidden = _aggregate(
        gradient_aggregated_hidden,
        propagation,
    )
    gradient_hidden_linear = (
        gradient_hidden * (hidden_linear > 0)
    )
    gradient_w1 = np.einsum(
        "bnf,bnh->fh",
        aggregated_input,
        gradient_hidden_linear,
    )
    gradient_b1 = gradient_hidden_linear.sum(axis=(0, 1))
    return loss, {
        "w1": gradient_w1.astype(np.float32),
        "b1": gradient_b1.astype(np.float32),
        "w2": gradient_w2.astype(np.float32),
        "b2": gradient_b2.astype(np.float32),
    }


def _loss_only(
    features: np.ndarray,
    labels: np.ndarray,
    propagation: csr_matrix,
    state: dict[str, np.ndarray],
    class_weights: np.ndarray,
) -> float:
    if not features.size:
        return float("inf")
    probabilities = _softmax(
        _forward(features, propagation, state)[0]
    )
    flat_labels = labels.reshape(-1)
    flat_probabilities = probabilities.reshape(
        -1,
        probabilities.shape[-1],
    )
    indexes = np.arange(len(flat_labels))
    weights = class_weights[flat_labels]
    return -float(
        np.sum(
            weights
            * np.log(
                np.maximum(
                    flat_probabilities[indexes, flat_labels],
                    1e-9,
                )
            )
        )
        / float(weights.sum())
    )


def _predict(
    features: np.ndarray,
    propagation: csr_matrix,
    state: dict[str, np.ndarray],
) -> np.ndarray:
    if not features.size:
        return np.empty(
            (*features.shape[:2], len(state["b2"])),
            dtype=np.float32,
        )
    return _softmax(_forward(features, propagation, state)[0])


def _evaluate(
    split: dict[str, Any],
    probabilities: np.ndarray,
    label_order: list[str],
    anomaly_threshold: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not split["sampleIds"]:
        empty = {
            "scenarioCount": 0,
            "faultScenarioCount": 0,
            "targetMacroF1": 0.0,
            "targetClassificationAccuracy": 0.0,
            "locationTop1Accuracy": 0.0,
            "locationTop5Accuracy": 0.0,
            "exactDiagnosisAccuracy": 0.0,
            "normalGraphAccuracy": 0.0,
            "faultDetectionRecall": 0.0,
            "labels": label_order,
            "confusionMatrix": [],
            "perClass": {},
        }
        return empty, []
    normal_index = label_order.index("NORMAL")
    predicted = probabilities.argmax(axis=-1)
    expected_target_labels: list[int] = []
    predicted_target_labels: list[int] = []
    location_top1 = 0
    location_top5 = 0
    exact = 0
    fault_count = 0
    normal_count = 0
    normal_correct = 0
    fault_detected_count = 0
    prediction_rows: list[dict[str, Any]] = []
    for sample_index, sample_id in enumerate(split["sampleIds"]):
        scenario_label = split["scenarioLabels"][sample_index]
        anomaly_scores = 1.0 - probabilities[
            sample_index,
            :,
            normal_index,
        ]
        ranked = np.argsort(-anomaly_scores)
        if scenario_label == "NORMAL":
            normal_count += 1
            graph_prediction = (
                "NORMAL"
                if float(anomaly_scores.max()) < anomaly_threshold
                else label_order[
                    int(
                        predicted[
                            sample_index,
                            int(ranked[0]),
                        ]
                    )
                ]
            )
            if graph_prediction == "NORMAL":
                normal_correct += 1
            prediction_rows.append(
                {
                    "sampleId": sample_id,
                    "expectedLabel": "NORMAL",
                    "targetBusinessId": "",
                    "predictedBusinessId": (
                        split["businessIds"][int(ranked[0])]
                    ),
                    "predictedLabel": graph_prediction,
                    "targetProbability": "",
                    "anomalyScore": round(
                        float(anomaly_scores.max()),
                        8,
                    ),
                    "locationTop1Hit": "",
                    "locationTop5Hit": "",
                    "exactHit": graph_prediction == "NORMAL",
                }
            )
            continue
        fault_count += 1
        target_indexes = np.flatnonzero(
            split["targets"][sample_index]
        )
        if len(target_indexes) != 1:
            raise OfflineTrainingError(
                f"异常样本{sample_id}必须有且仅有一个根因顶点"
            )
        target = int(target_indexes[0])
        fault_detected = (
            float(anomaly_scores.max()) >= anomaly_threshold
        )
        fault_detected_count += int(fault_detected)
        expected_label = int(
            split["labels"][sample_index, target]
        )
        predicted_label = int(predicted[sample_index, target])
        expected_target_labels.append(expected_label)
        predicted_target_labels.append(predicted_label)
        top1_hit = int(ranked[0]) == target
        top5_hit = target in set(
            int(value) for value in ranked[:5]
        )
        exact_hit = (
            fault_detected
            and top1_hit
            and predicted_label == expected_label
        )
        location_top1 += int(top1_hit)
        location_top5 += int(top5_hit)
        exact += int(exact_hit)
        prediction_rows.append(
            {
                "sampleId": sample_id,
                "expectedLabel": scenario_label,
                "targetBusinessId": split["businessIds"][target],
                "predictedBusinessId": split["businessIds"][
                    int(ranked[0])
                ],
                "predictedLabel": (
                    label_order[
                        int(
                            predicted[
                                sample_index,
                                int(ranked[0]),
                            ]
                        )
                    ]
                    if fault_detected
                    else "NORMAL"
                ),
                "targetProbability": round(
                    float(
                        probabilities[
                            sample_index,
                            target,
                            expected_label,
                        ]
                    ),
                    8,
                ),
                "anomalyScore": round(
                    float(anomaly_scores[target]),
                    8,
                ),
                "locationTop1Hit": top1_hit,
                "locationTop5Hit": top5_hit,
                "exactHit": exact_hit,
            }
        )
    if expected_target_labels:
        report = classification_report(
            expected_target_labels,
            predicted_target_labels,
            labels=list(range(len(label_order))),
            target_names=label_order,
            output_dict=True,
            zero_division=0,
        )
        matrix = confusion_matrix(
            expected_target_labels,
            predicted_target_labels,
            labels=list(range(len(label_order))),
        ).tolist()
        macro_f1 = float(report["macro avg"]["f1-score"])
        target_accuracy = float(
            np.mean(
                np.asarray(expected_target_labels)
                == np.asarray(predicted_target_labels)
            )
        )
        per_class = {
            label: {
                "precision": round(
                    float(report[label]["precision"]),
                    6,
                ),
                "recall": round(
                    float(report[label]["recall"]),
                    6,
                ),
                "f1": round(
                    float(report[label]["f1-score"]),
                    6,
                ),
                "support": int(report[label]["support"]),
            }
            for label in label_order
        }
    else:
        macro_f1 = 0.0
        target_accuracy = 0.0
        matrix = []
        per_class = {}
    denominator = max(fault_count, 1)
    return {
        "scenarioCount": len(split["sampleIds"]),
        "faultScenarioCount": fault_count,
        "targetMacroF1": round(macro_f1, 6),
        "targetClassificationAccuracy": round(
            target_accuracy,
            6,
        ),
        "locationTop1Accuracy": round(
            location_top1 / denominator,
            6,
        ),
        "locationTop5Accuracy": round(
            location_top5 / denominator,
            6,
        ),
        "exactDiagnosisAccuracy": round(
            exact / denominator,
            6,
        ),
        "normalGraphAccuracy": round(
            normal_correct / max(normal_count, 1),
            6,
        ),
        "faultDetectionRecall": round(
            fault_detected_count / denominator,
            6,
        ),
        "anomalyThreshold": round(anomaly_threshold, 8),
        "labels": label_order,
        "confusionMatrix": matrix,
        "perClass": per_class,
    }, prediction_rows


def _calibrate_anomaly_threshold(
    sources: list[tuple[dict[str, Any], np.ndarray]],
    label_order: list[str],
) -> float:
    normal_index = label_order.index("NORMAL")
    normal_scores: list[float] = []
    fault_scores: list[float] = []
    for split, probabilities in sources:
        for sample_index, scenario_label in enumerate(
            split["scenarioLabels"]
        ):
            anomaly = (
                1.0
                - probabilities[sample_index, :, normal_index]
            )
            score = float(anomaly.max())
            if scenario_label == "NORMAL":
                normal_scores.append(score)
            else:
                fault_scores.append(score)
    if not normal_scores or not fault_scores:
        return 0.5
    values = sorted(set(normal_scores + fault_scores))
    candidates = [0.05, 0.999999]
    candidates.extend(
        (left + right) / 2.0
        for left, right in zip(values, values[1:])
    )
    best: tuple[float, float, float, float] | None = None
    for threshold in candidates:
        normal_accuracy = float(
            np.mean(
                np.asarray(normal_scores) < threshold
            )
        )
        fault_recall = float(
            np.mean(
                np.asarray(fault_scores) >= threshold
            )
        )
        balanced = (normal_accuracy + fault_recall) / 2.0
        candidate = (
            balanced,
            min(normal_accuracy, fault_recall),
            normal_accuracy,
            threshold,
        )
        if best is None or candidate > best:
            best = candidate
    return float(np.clip(best[3], 0.05, 0.999999))


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=-1, keepdims=True)
    probabilities = np.exp(shifted)
    return probabilities / probabilities.sum(
        axis=-1,
        keepdims=True,
    )


def _aggregate(
    features: np.ndarray,
    propagation: csr_matrix,
) -> np.ndarray:
    return np.stack(
        [
            propagation @ graph_features
            for graph_features in features
        ]
    ).astype(np.float32)


class _Adam:
    def __init__(
        self,
        state: dict[str, np.ndarray],
        learning_rate: float,
    ) -> None:
        self.learning_rate = learning_rate
        self.first = {
            name: np.zeros_like(value)
            for name, value in state.items()
        }
        self.second = {
            name: np.zeros_like(value)
            for name, value in state.items()
        }
        self.step_count = 0

    def step(
        self,
        state: dict[str, np.ndarray],
        gradients: dict[str, np.ndarray],
    ) -> None:
        self.step_count += 1
        for name, gradient in gradients.items():
            self.first[name] = (
                0.9 * self.first[name] + 0.1 * gradient
            )
            self.second[name] = (
                0.999 * self.second[name]
                + 0.001 * np.square(gradient)
            )
            first = self.first[name] / (
                1.0 - 0.9**self.step_count
            )
            second = self.second[name] / (
                1.0 - 0.999**self.step_count
            )
            state[name] -= (
                self.learning_rate
                * first
                / (np.sqrt(second) + 1e-8)
            )


def _prediction_columns() -> list[str]:
    return [
        "sampleId",
        "expectedLabel",
        "targetBusinessId",
        "predictedBusinessId",
        "predictedLabel",
        "targetProbability",
        "anomalyScore",
        "locationTop1Hit",
        "locationTop5Hit",
        "exactHit",
    ]


def _model_response(
    manifest: dict[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    return {
        **manifest,
        "artifactPath": str(artifact_dir.resolve()),
        "manifestPath": str(
            (artifact_dir / "manifest.json").resolve()
        ),
        "readmePath": str((artifact_dir / "README.md").resolve()),
        "metricsPath": str(
            (artifact_dir / "metrics.json").resolve()
        ),
        "testPredictionsPath": str(
            (
                artifact_dir / "test-predictions.csv"
            ).resolve()
        ),
    }


def _model_readme(
    model_id: str,
    dataset: dict[str, Any],
    request: OfflineTrainingRequest,
    validation: dict[str, Any],
    test: dict[str, Any],
    qualified: bool,
) -> str:
    return f"""# DONGJIN离线GNN模型

- 模型：`{model_id}`
- 数据集：`{dataset["datasetId"]}`
- 电网：`{dataset["gridId"]}`
- 拓扑版本：`{dataset["topologyVersion"]}`
- 模型类型：两层GCN节点分类器
- 训练状态：`{"QUALIFIED" if qualified else "REJECTED"}`

## 测试集主要指标

- 根因类型Macro-F1：{test["targetMacroF1"]}
- 根因位置Top-1：{test["locationTop1Accuracy"]}
- 根因位置Top-5：{test["locationTop5Accuracy"]}
- 位置和类型同时正确：{test["exactDiagnosisAccuracy"]}

## 白箱文件

- `training-config.json`：训练参数和随机种子。
- `training-history.csv`：每轮训练与验证损失。
- `metrics.json`：验证集、测试集指标和合格门槛。
- `validation-predictions.csv`：验证集逐场景结果。
- `test-predictions.csv`：测试集逐场景结果。
- `feature-schema.json`：模型输入含义和单位。
- `label-schema.json`：模型输出标签含义。
- `normalization.json`：训练集归一化参数。
- `manifest.json`：数据集、拓扑、Schema和文件校验和。

`model.joblib`是机器权重；其输入、标签、训练过程和评估结果均已通过上述文件暴露。
本模型尚未自动激活到在线错误研判。
"""
