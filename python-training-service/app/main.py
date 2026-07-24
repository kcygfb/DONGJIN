from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from scipy.sparse import csr_matrix
from sklearn.feature_extraction import DictVectorizer
from sklearn.preprocessing import StandardScaler

from app.grid.health import build_grid_health
from app.grid.api import router as grid_router
from app.grid.simulation.api import router as grid_runtime_router
from app.grid.simulation.engine import get_simulation_engine
from app.grid.offline_api import router as offline_training_router


BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = Path(os.getenv("DONGJIN_MODEL_DIR", BASE_DIR / "artifacts")).resolve()
ACTIVE_MODEL_FILE = MODEL_DIR / "active-model.json"
MODEL_LOCK = threading.RLock()
PRIMARY_MODEL_TYPE = "GNN_GCN"
NORMAL_LABEL = "NORMAL"

app = FastAPI(
    title="Dongjin Python Compute Service",
    version="2.1.0",
    description="统一提供标准电网计算、GCN训练和故障定位能力。",
)
app.include_router(grid_router)
app.include_router(grid_runtime_router)
app.include_router(offline_training_router)


@app.on_event("shutdown")
def shutdown_grid_simulation() -> None:
    get_simulation_engine().shutdown()


class TopologyNode(BaseModel):
    id: str = Field(min_length=1)


class TopologyEdge(BaseModel):
    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)


class GraphTopology(BaseModel):
    nodes: list[TopologyNode] = Field(min_length=1)
    edges: list[TopologyEdge] = Field(default_factory=list)


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
        "serviceVersion": "2.1.0",
        "primaryModelType": PRIMARY_MODEL_TYPE,
        "activeModel": _read_active_metadata(required=False),
        "gridData": build_grid_health(),
    }


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
    signature_payload = "|".join(targets) + "#" + "|".join(
        f"{edge.id}:{edge.source}>{edge.target}" for edge in topology.edges
    )
    return {
        "targets": targets,
        "targetIndex": target_index,
        "propagation": propagation,
        "signature": hashlib.sha256(signature_payload.encode("utf-8")).hexdigest()[:16],
    }


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
