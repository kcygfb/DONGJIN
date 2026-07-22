from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from scipy.sparse import csr_matrix
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler


BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = Path(os.getenv("DONGJIN_MODEL_DIR", BASE_DIR / "artifacts")).resolve()
ACTIVE_MODEL_FILE = MODEL_DIR / "active-model.json"
MODEL_LOCK = threading.RLock()
PRIMARY_MODEL_TYPE = "GNN_GCN"
NORMAL_LABEL = "NORMAL"

app = FastAPI(
    title="Dongjin Grid Fault GNN Training Service",
    version="2.0.0",
    description="使用 GCN 图神经网络对完整电网拓扑执行故障定位与分类。",
)


class FaultSample(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    fault_type: str = Field(alias="faultType", min_length=1)
    target_id: str = Field(alias="targetId", min_length=1)
    target_kind: str = Field(alias="targetKind", pattern="^(NODE|EDGE)$")
    features: dict[str, float] = Field(min_length=1)


class TopologyNode(BaseModel):
    id: str = Field(min_length=1)


class TopologyEdge(BaseModel):
    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)


class GraphTopology(BaseModel):
    nodes: list[TopologyNode] = Field(min_length=1)
    edges: list[TopologyEdge] = Field(default_factory=list)


class TrainingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_name: str = Field(alias="datasetName", min_length=1, max_length=200)
    samples: list[FaultSample] = Field(min_length=8)
    topology: GraphTopology
    random_state: int = Field(default=42, alias="randomState")


class Observation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target_kind: str = Field(alias="targetKind", pattern="^(NODE|EDGE)$")
    target_id: str = Field(alias="targetId", min_length=1)
    features: dict[str, float] = Field(min_length=1)


class BatchPredictionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    observations: list[Observation] = Field(min_length=1, max_length=5000)
    topology: GraphTopology
    top_k: int = Field(default=4, alias="topK", ge=1, le=10)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "serviceVersion": "2.0.0",
        "primaryModelType": PRIMARY_MODEL_TYPE,
        "activeModel": _read_active_metadata(required=False),
    }


@app.post("/train")
def train(request: TrainingRequest) -> dict[str, Any]:
    labels = [sample.fault_type for sample in request.samples]
    distribution = Counter(labels)
    if NORMAL_LABEL not in distribution:
        raise HTTPException(status_code=400, detail="GNN 训练必须包含 NORMAL 正常样本")
    if len(distribution) < 2:
        raise HTTPException(status_code=400, detail="至少需要两种不同的样本类别")

    graph = _build_graph(request.topology)
    _validate_sample_targets(request.samples, graph["targetIndex"])
    train_indices, test_indices, evaluation_note = _split_indices(request.samples, request.random_state)

    vectorizer = DictVectorizer(sparse=False)
    vectorizer.fit([sample.features for sample in request.samples])
    feature_names = vectorizer.get_feature_names_out().tolist()
    snapshots, graph_labels = _build_training_snapshots(
        request.samples,
        graph,
        vectorizer,
        request.random_state,
        sorted(distribution),
        train_indices,
    )
    label_order = sorted(distribution)
    label_to_index = {label: index for index, label in enumerate(label_order)}

    scaler = StandardScaler()
    scaler.fit(snapshots[train_indices].reshape(-1, snapshots.shape[-1]))
    scaled_snapshots = scaler.transform(snapshots.reshape(-1, snapshots.shape[-1])).reshape(snapshots.shape)

    gnn_state, training_history = _train_gcn(
        scaled_snapshots[train_indices],
        graph_labels[train_indices],
        graph["propagation"],
        len(label_order),
        request.random_state,
    )
    gnn_probabilities = _gcn_predict(
        scaled_snapshots[test_indices], graph["propagation"], gnn_state
    )
    gnn_metrics = _evaluate_graph_predictions(
        request.samples,
        test_indices,
        graph_labels[test_indices],
        gnn_probabilities,
        label_order,
        graph["targets"],
    )

    model_version = f"gnn-fault-model-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
    trained_at = datetime.now(timezone.utc).isoformat()
    model_path = MODEL_DIR / f"{model_version}.joblib"
    metadata_path = MODEL_DIR / f"{model_version}.json"
    metadata = {
        "modelVersion": model_version,
        "primaryModelType": PRIMARY_MODEL_TYPE,
        "primaryModelName": "两层图卷积网络（GCN）",
        "featureSchemaVersion": "grid-fault-gnn-v2",
        "datasetName": request.dataset_name,
        "trainedAt": trained_at,
        "sampleCount": len(request.samples),
        "trainingSampleCount": len(train_indices),
        "evaluationSampleCount": len(test_indices),
        "graphObjectCount": len(graph["targets"]),
        "graphLinkCount": int(graph["linkCount"]),
        "topologySignature": graph["signature"],
        "labelDistribution": dict(sorted(distribution.items())),
        "featureNames": feature_names,
        "metrics": gnn_metrics,
        "trainingConfig": {
            "architecture": "GCN(input)-32-ReLU-GCN(5)-Softmax",
            "epochsCompleted": training_history["epochsCompleted"],
            "finalLoss": training_history["finalLoss"],
            "messagePassingLayers": 2,
            "randomState": request.random_state,
        },
        "evaluationNote": evaluation_note + " accuracy 表示故障位置和类型同时正确的整图研判准确率。",
        "modelFile": model_path.name,
    }

    with MODEL_LOCK:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "primaryModelType": PRIMARY_MODEL_TYPE,
                "gnnState": gnn_state,
                "vectorizer": vectorizer,
                "scaler": scaler,
                "labels": label_order,
                "modelVersion": model_version,
                "trainedAt": trained_at,
            },
            model_path,
        )
        _write_json_atomic(metadata_path, metadata)
        _write_json_atomic(ACTIVE_MODEL_FILE, metadata)

    return metadata


@app.post("/predict/batch")
def predict_batch(request: BatchPredictionRequest) -> dict[str, Any]:
    with MODEL_LOCK:
        metadata, artifact = _load_active_artifact()
        if artifact.get("primaryModelType") != PRIMARY_MODEL_TYPE:
            raise HTTPException(status_code=409, detail="当前激活的是旧版非 GNN 模型，请重置后重新生成样本并训练")

        graph = _build_graph(request.topology)
        observation_by_key = {
            _target_key(item.target_kind, item.target_id): item for item in request.observations
        }
        expected = set(graph["targetIndex"])
        if set(observation_by_key) != expected:
            raise HTTPException(status_code=400, detail="GNN 推理必须提交与当前拓扑完全一致的全量观测")

        vectorizer: DictVectorizer = artifact["vectorizer"]
        scaler: StandardScaler = artifact["scaler"]
        labels: list[str] = artifact["labels"]
        ordered_observations = [observation_by_key[key] for key in graph["targets"]]
        feature_matrix = vectorizer.transform([item.features for item in ordered_observations]).astype(np.float32)
        scaled_matrix = scaler.transform(feature_matrix).astype(np.float32)
        probabilities = _gcn_predict(
            scaled_matrix[np.newaxis, :, :], graph["propagation"], artifact["gnnState"]
        )[0]

    predictions = _format_predictions(ordered_observations, probabilities, labels, request.top_k)
    return {
        "modelVersion": metadata["modelVersion"],
        "modelType": PRIMARY_MODEL_TYPE,
        "topologySignature": graph["signature"],
        "observationCount": len(request.observations),
        "predictions": predictions,
    }


@app.get("/models/active")
def active_model() -> dict[str, Any]:
    return _read_active_metadata(required=True)


@app.delete("/reset")
def reset_training() -> dict[str, Any]:
    deleted_files: list[str] = []
    deleted_model_count = 0
    with MODEL_LOCK:
        if MODEL_DIR.exists():
            model_files = list(MODEL_DIR.glob("*-fault-model-*.joblib")) + list(MODEL_DIR.glob("fault-model-*.joblib"))
            metadata_files = list(MODEL_DIR.glob("*-fault-model-*.json")) + list(MODEL_DIR.glob("fault-model-*.json"))
            temporary_files = list(MODEL_DIR.glob("*.tmp"))
            paths = [*model_files, *metadata_files, *temporary_files, ACTIVE_MODEL_FILE]
            for path in dict.fromkeys(paths):
                if path.is_file():
                    path.unlink()
                    deleted_files.append(path.name)
            deleted_model_count = len(set(model_files))

    return {
        "status": "reset",
        "resetAt": datetime.now(timezone.utc).isoformat(),
        "deletedModelCount": deleted_model_count,
        "deletedFiles": sorted(set(deleted_files)),
    }


def _build_graph(topology: GraphTopology) -> dict[str, Any]:
    node_ids = [node.id for node in topology.nodes]
    edge_ids = [edge.id for edge in topology.edges]
    if len(node_ids) != len(set(node_ids)) or len(edge_ids) != len(set(edge_ids)):
        raise HTTPException(status_code=400, detail="拓扑中存在重复的节点或线路 ID")
    node_id_set = set(node_ids)
    for edge in topology.edges:
        if edge.source not in node_id_set or edge.target not in node_id_set:
            raise HTTPException(status_code=400, detail=f"线路 {edge.id} 的端点不属于当前拓扑")

    targets = [f"NODE:{node_id}" for node_id in node_ids] + [f"EDGE:{edge_id}" for edge_id in edge_ids]
    target_index = {key: index for index, key in enumerate(targets)}
    adjacency = np.eye(len(targets), dtype=np.float32)
    for edge in topology.edges:
        edge_index = target_index[f"EDGE:{edge.id}"]
        for node_id in (edge.source, edge.target):
            node_index = target_index[f"NODE:{node_id}"]
            adjacency[node_index, edge_index] = 1.0
            adjacency[edge_index, node_index] = 1.0

    degree = adjacency.sum(axis=1)
    inverse_sqrt_degree = np.power(np.maximum(degree, 1.0), -0.5)
    normalized = inverse_sqrt_degree[:, None] * adjacency * inverse_sqrt_degree[None, :]
    propagation = csr_matrix(normalized, dtype=np.float32)
    graph_distances = _shortest_distances(adjacency > 0.0)
    target_degrees = [
        int(np.count_nonzero(adjacency[index]) - 1) if target.startswith("NODE:") else 2
        for index, target in enumerate(targets)
    ]
    signature_payload = "|".join(targets) + "#" + "|".join(
        f"{edge.id}:{edge.source}>{edge.target}" for edge in topology.edges
    )
    return {
        "targets": targets,
        "targetIndex": target_index,
        "adjacency": normalized.astype(np.float32),
        "propagation": propagation,
        "distances": graph_distances,
        "targetDegrees": target_degrees,
        "linkCount": len(topology.edges) * 2,
        "signature": hashlib.sha256(signature_payload.encode("utf-8")).hexdigest()[:16],
    }


def _build_training_snapshots(
    samples: list[FaultSample],
    graph: dict[str, Any],
    vectorizer: DictVectorizer,
    random_state: int,
    label_order: list[str],
    normal_source_indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    normal_by_kind: dict[str, list[dict[str, float]]] = {"NODE": [], "EDGE": []}
    for sample_index in normal_source_indices:
        sample = samples[int(sample_index)]
        if sample.fault_type == NORMAL_LABEL:
            normal_by_kind[sample.target_kind].append(sample.features)
    for kind, rows in normal_by_kind.items():
        if not rows:
            raise HTTPException(status_code=400, detail=f"GNN 训练缺少 {kind} 类型的 NORMAL 正常样本")

    rng = np.random.default_rng(random_state)
    target_count = len(graph["targets"])
    feature_count = len(vectorizer.get_feature_names_out())
    snapshots = np.empty((len(samples), target_count, feature_count), dtype=np.float32)
    label_to_index = {label: index for index, label in enumerate(label_order)}
    normal_index = label_to_index[NORMAL_LABEL]
    graph_labels = np.full((len(samples), target_count), normal_index, dtype=np.int64)

    for sample_index, sample in enumerate(samples):
        background: list[dict[str, float]] = []
        for target in graph["targets"]:
            kind = target.split(":", 1)[0]
            pool = normal_by_kind[kind]
            normal_features = dict(pool[int(rng.integers(0, len(pool)))])
            target_position = len(background)
            normal_features["targetIsEdge"] = 1.0 if kind == "EDGE" else 0.0
            normal_features["topologyDegree"] = float(graph["targetDegrees"][target_position])
            background.append(normal_features)
        target_key = _target_key(sample.target_kind, sample.target_id)
        target_index = graph["targetIndex"][target_key]
        background[target_index] = sample.features
        if sample.fault_type != NORMAL_LABEL:
            _apply_propagation(background, target_index, sample.features, graph["distances"])
        snapshots[sample_index] = vectorizer.transform(background).astype(np.float32)
        graph_labels[sample_index, target_index] = label_to_index[sample.fault_type]
    return snapshots, graph_labels


def _shortest_distances(adjacency: np.ndarray) -> np.ndarray:
    size = len(adjacency)
    distances = np.full((size, size), size + 1, dtype=np.int16)
    for source in range(size):
        distances[source, source] = 0
        frontier = [source]
        while frontier:
            current = frontier.pop(0)
            for neighbor in np.flatnonzero(adjacency[current]):
                if distances[source, neighbor] > distances[source, current] + 1:
                    distances[source, neighbor] = distances[source, current] + 1
                    frontier.append(int(neighbor))
    return distances


def _apply_propagation(
    background: list[dict[str, float]],
    target_index: int,
    fault_features: dict[str, float],
    distances: np.ndarray,
) -> None:
    protected = {"targetIsEdge", "topologyDegree"}
    for index, current in enumerate(background):
        distance = int(distances[target_index, index])
        factor = 0.22 if distance == 1 else 0.08 if distance == 2 else 0.0
        if factor == 0.0:
            continue
        for name, fault_value in fault_features.items():
            if name in protected or name not in current:
                continue
            mixed = float(current[name]) * (1.0 - factor) + float(fault_value) * factor
            if name == "alarmCount":
                mixed = round(mixed)
            elif name == "connectivityRatio":
                mixed = min(1.0, max(0.0, mixed))
            current[name] = float(mixed)


def _train_gcn(
    features: np.ndarray,
    labels: np.ndarray,
    propagation: csr_matrix,
    class_count: int,
    random_state: int,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    rng = np.random.default_rng(random_state)
    input_count = features.shape[-1]
    hidden_count = 32
    state = {
        "w1": (rng.standard_normal((input_count, hidden_count)) * np.sqrt(2.0 / input_count)).astype(np.float32),
        "b1": np.zeros(hidden_count, dtype=np.float32),
        "w2": (rng.standard_normal((hidden_count, class_count)) * np.sqrt(2.0 / hidden_count)).astype(np.float32),
        "b2": np.zeros(class_count, dtype=np.float32),
    }
    optimizer = _Adam(state, learning_rate=0.012)
    counts = np.bincount(labels.reshape(-1), minlength=class_count).astype(np.float32)
    class_weights = labels.size / np.maximum(counts * class_count, 1.0)
    class_weights = np.clip(class_weights, 0.2, 12.0).astype(np.float32)

    batch_size = min(32, len(features))
    epochs = 180
    final_loss = 0.0
    best_loss = float("inf")
    best_state = {name: value.copy() for name, value in state.items()}
    stale_epochs = 0
    completed = 0
    for epoch in range(epochs):
        order = rng.permutation(len(features))
        epoch_losses: list[float] = []
        for start in range(0, len(order), batch_size):
            indices = order[start:start + batch_size]
            loss, gradients = _gcn_loss_and_gradients(
                features[indices], labels[indices], propagation, state, class_weights
            )
            optimizer.step(state, gradients)
            epoch_losses.append(loss)
        final_loss = float(np.mean(epoch_losses))
        completed = epoch + 1
        if final_loss < best_loss - 1e-5:
            best_loss = final_loss
            best_state = {name: value.copy() for name, value in state.items()}
            stale_epochs = 0
        else:
            stale_epochs += 1
        if stale_epochs >= 25 and epoch >= 60:
            break
    return best_state, {"epochsCompleted": completed, "finalLoss": round(best_loss, 6)}


def _gcn_forward(
    features: np.ndarray,
    propagation: csr_matrix,
    state: dict[str, np.ndarray],
) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    aggregated_input = _graph_aggregate(features, propagation)
    hidden_linear = aggregated_input @ state["w1"] + state["b1"]
    hidden = np.maximum(hidden_linear, 0.0)
    aggregated_hidden = _graph_aggregate(hidden, propagation)
    logits = aggregated_hidden @ state["w2"] + state["b2"]
    return logits, (aggregated_input, hidden_linear, aggregated_hidden)


def _gcn_loss_and_gradients(
    features: np.ndarray,
    labels: np.ndarray,
    propagation: csr_matrix,
    state: dict[str, np.ndarray],
    class_weights: np.ndarray,
) -> tuple[float, dict[str, np.ndarray]]:
    logits, cache = _gcn_forward(features, propagation, state)
    shifted = logits - logits.max(axis=-1, keepdims=True)
    probabilities = np.exp(shifted)
    probabilities /= probabilities.sum(axis=-1, keepdims=True)
    flat_labels = labels.reshape(-1)
    flat_probabilities = probabilities.reshape(-1, probabilities.shape[-1])
    row_indices = np.arange(len(flat_labels))
    sample_weights = class_weights[flat_labels]
    normalizer = float(sample_weights.sum())
    loss = -float(np.sum(sample_weights * np.log(np.maximum(flat_probabilities[row_indices, flat_labels], 1e-9))) / normalizer)

    gradient_logits = probabilities.copy().reshape(-1, probabilities.shape[-1])
    gradient_logits[row_indices, flat_labels] -= 1.0
    gradient_logits *= (sample_weights / normalizer)[:, None]
    gradient_logits = gradient_logits.reshape(probabilities.shape)
    aggregated_input, hidden_linear, aggregated_hidden = cache
    gradient_w2 = np.einsum("bnh,bnc->hc", aggregated_hidden, gradient_logits)
    gradient_b2 = gradient_logits.sum(axis=(0, 1))
    gradient_aggregated_hidden = gradient_logits @ state["w2"].T
    gradient_hidden = _graph_aggregate(gradient_aggregated_hidden, propagation)
    gradient_hidden_linear = gradient_hidden * (hidden_linear > 0.0)
    gradient_w1 = np.einsum("bnf,bnh->fh", aggregated_input, gradient_hidden_linear)
    gradient_b1 = gradient_hidden_linear.sum(axis=(0, 1))
    return loss, {
        "w1": gradient_w1.astype(np.float32),
        "b1": gradient_b1.astype(np.float32),
        "w2": gradient_w2.astype(np.float32),
        "b2": gradient_b2.astype(np.float32),
    }


def _gcn_predict(
    features: np.ndarray,
    propagation: csr_matrix,
    state: dict[str, np.ndarray],
) -> np.ndarray:
    logits, _ = _gcn_forward(features, propagation, state)
    shifted = logits - logits.max(axis=-1, keepdims=True)
    probabilities = np.exp(shifted)
    return probabilities / probabilities.sum(axis=-1, keepdims=True)


def _graph_aggregate(
    features: np.ndarray, propagation: csr_matrix
) -> np.ndarray:
    return np.stack([propagation @ graph_features for graph_features in features]).astype(np.float32)


class _Adam:
    def __init__(self, state: dict[str, np.ndarray], learning_rate: float) -> None:
        self.learning_rate = learning_rate
        self.first = {name: np.zeros_like(value) for name, value in state.items()}
        self.second = {name: np.zeros_like(value) for name, value in state.items()}
        self.step_count = 0

    def step(self, state: dict[str, np.ndarray], gradients: dict[str, np.ndarray]) -> None:
        self.step_count += 1
        for name, gradient in gradients.items():
            self.first[name] = 0.9 * self.first[name] + 0.1 * gradient
            self.second[name] = 0.999 * self.second[name] + 0.001 * np.square(gradient)
            first_corrected = self.first[name] / (1.0 - 0.9 ** self.step_count)
            second_corrected = self.second[name] / (1.0 - 0.999 ** self.step_count)
            state[name] -= self.learning_rate * first_corrected / (np.sqrt(second_corrected) + 1e-8)


def _evaluate_graph_predictions(
    samples: list[FaultSample],
    test_indices: np.ndarray,
    expected_labels: np.ndarray,
    probabilities: np.ndarray,
    label_order: list[str],
    graph_targets: list[str],
) -> dict[str, Any]:
    normal_index = label_order.index(NORMAL_LABEL)
    predicted_labels = probabilities.argmax(axis=-1)
    target_expected: list[int] = []
    target_predicted: list[int] = []
    location_hits = 0
    exact_hits = 0
    fault_graph_count = 0
    target_index_by_key = {key: index for index, key in enumerate(graph_targets)}

    for result_index, sample_index in enumerate(test_indices):
        sample = samples[int(sample_index)]
        target_index = target_index_by_key[_target_key(sample.target_kind, sample.target_id)]
        target_expected.append(int(expected_labels[result_index, target_index]))
        target_predicted.append(int(predicted_labels[result_index, target_index]))
        if sample.fault_type == NORMAL_LABEL:
            continue
        fault_graph_count += 1
        anomaly_scores = 1.0 - probabilities[result_index, :, normal_index]
        located_index = int(np.argmax(anomaly_scores))
        if located_index == target_index:
            location_hits += 1
            if int(predicted_labels[result_index, located_index]) == label_order.index(sample.fault_type):
                exact_hits += 1

    report = classification_report(
        target_expected,
        target_predicted,
        labels=list(range(len(label_order))),
        target_names=label_order,
        output_dict=True,
        zero_division=0,
    )
    matrix = confusion_matrix(
        target_expected, target_predicted, labels=list(range(len(label_order)))
    ).tolist()
    denominator = max(fault_graph_count, 1)
    exact_accuracy = exact_hits / denominator
    return {
        "accuracy": round(float(exact_accuracy), 6),
        "exactDiagnosisAccuracy": round(float(exact_accuracy), 6),
        "locationAccuracy": round(float(location_hits / denominator), 6),
        "targetClassificationAccuracy": round(float(accuracy_score(target_expected, target_predicted)), 6),
        "macroPrecision": round(float(report["macro avg"]["precision"]), 6),
        "macroRecall": round(float(report["macro avg"]["recall"]), 6),
        "macroF1": round(float(report["macro avg"]["f1-score"]), 6),
        "evaluatedFaultGraphs": fault_graph_count,
        "labels": label_order,
        "confusionMatrix": matrix,
        "perClass": {
            label: {
                "precision": round(float(report[label]["precision"]), 6),
                "recall": round(float(report[label]["recall"]), 6),
                "f1": round(float(report[label]["f1-score"]), 6),
                "support": int(report[label]["support"]),
            }
            for label in label_order
        },
    }


def _format_predictions(
    observations: list[Observation],
    probabilities: np.ndarray,
    labels: list[str],
    top_k: int,
) -> list[dict[str, Any]]:
    if NORMAL_LABEL not in labels:
        raise HTTPException(status_code=409, detail="当前模型没有 NORMAL 类别，请重新训练")
    normal_index = labels.index(NORMAL_LABEL)
    predictions: list[dict[str, Any]] = []
    for observation, row in zip(observations, probabilities):
        fault_ranked = sorted(
            ((label, float(row[index])) for index, label in enumerate(labels) if label != NORMAL_LABEL),
            key=lambda item: item[1],
            reverse=True,
        )[:top_k]
        predictions.append({
            "targetKind": observation.target_kind,
            "targetId": observation.target_id,
            "predictedFaultType": fault_ranked[0][0],
            "confidence": round(fault_ranked[0][1], 6),
            "normalProbability": round(float(row[normal_index]), 6),
            "anomalyScore": round(float(1.0 - row[normal_index]), 6),
            "candidates": [
                {"faultType": label, "confidence": round(probability, 6)}
                for label, probability in fault_ranked
            ],
        })
    return predictions


def _split_indices(samples: list[FaultSample], random_state: int) -> tuple[np.ndarray, np.ndarray, str]:
    labels = [sample.fault_type for sample in samples]
    strata = [
        f"{NORMAL_LABEL}_{sample.target_kind}" if sample.fault_type == NORMAL_LABEL else sample.fault_type
        for sample in samples
    ]
    distribution = Counter(strata)
    class_count = len(distribution)
    indices = np.arange(len(labels))
    enough_for_holdout = len(labels) >= max(12, class_count * 4) and min(distribution.values()) >= 2
    if not enough_for_holdout:
        return indices, indices, "样本较少，指标基于训练集回代；正式使用前应增加样本。"
    test_count = max(class_count, round(len(labels) * 0.2))
    test_count = min(test_count, len(labels) - class_count)
    train_indices, test_indices = train_test_split(
        indices,
        test_size=test_count,
        random_state=random_state,
        stratify=strata,
    )
    return np.asarray(train_indices), np.asarray(test_indices), "指标基于按故障类型分层的整图留出测试集。"


def _validate_sample_targets(samples: list[FaultSample], target_index: dict[str, int]) -> None:
    for sample in samples:
        key = _target_key(sample.target_kind, sample.target_id)
        if key not in target_index:
            raise HTTPException(status_code=400, detail=f"训练样本 {sample.id} 的目标 {key} 不属于训练拓扑")


def _target_key(kind: str, target_id: str) -> str:
    return f"{kind}:{target_id}"


def _load_active_artifact() -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = _read_active_metadata(required=True)
    model_path = MODEL_DIR / metadata["modelFile"]
    if not model_path.exists():
        raise HTTPException(status_code=503, detail="当前模型文件不存在，请重新训练")
    return metadata, joblib.load(model_path)


def _read_active_metadata(required: bool) -> dict[str, Any] | None:
    if not ACTIVE_MODEL_FILE.exists():
        if required:
            raise HTTPException(status_code=404, detail="尚未训练并激活模型")
        return None
    try:
        return json.loads(ACTIVE_MODEL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exception:
        if required:
            raise HTTPException(status_code=503, detail=f"当前模型元数据不可用：{exception}") from exception
        return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)
