from __future__ import annotations

import json
import os
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline


BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = Path(os.getenv("DONGJIN_MODEL_DIR", BASE_DIR / "artifacts")).resolve()
ACTIVE_MODEL_FILE = MODEL_DIR / "active-model.json"
MODEL_LOCK = threading.RLock()

app = FastAPI(
    title="Dongjin Grid Fault Training Service",
    version="1.0.0",
    description="接收 Java 生成的故障样本，训练模型并为错误研判提供统一推理接口。",
)


class FaultSample(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    fault_type: str = Field(alias="faultType", min_length=1)
    features: dict[str, float] = Field(min_length=1)


class TrainingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    dataset_name: str = Field(alias="datasetName", min_length=1, max_length=200)
    samples: list[FaultSample] = Field(min_length=8)
    random_state: int = Field(default=42, alias="randomState")


class PredictionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    features: dict[str, float] = Field(min_length=1)
    top_k: int = Field(default=3, alias="topK", ge=1, le=10)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "activeModel": _read_active_metadata(required=False),
    }


@app.post("/train")
def train(request: TrainingRequest) -> dict[str, Any]:
    labels = [sample.fault_type for sample in request.samples]
    distribution = Counter(labels)
    if len(distribution) < 2:
        raise HTTPException(status_code=400, detail="至少需要两种不同的故障类型")

    feature_rows = [sample.features for sample in request.samples]
    train_features, test_features, train_labels, test_labels, evaluation_note = _split_dataset(
        feature_rows,
        labels,
        request.random_state,
    )

    pipeline = Pipeline(
        steps=[
            ("vectorizer", DictVectorizer(sparse=True)),
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=240,
                    max_depth=None,
                    min_samples_leaf=1,
                    class_weight="balanced",
                    random_state=request.random_state,
                    n_jobs=-1,
                ),
            ),
        ]
    )

    with MODEL_LOCK:
        pipeline.fit(train_features, train_labels)
        predicted = pipeline.predict(test_features)
        label_order = sorted(distribution)
        report = classification_report(
            test_labels,
            predicted,
            labels=label_order,
            output_dict=True,
            zero_division=0,
        )
        matrix = confusion_matrix(test_labels, predicted, labels=label_order).tolist()
        model_version = f"fault-model-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
        trained_at = datetime.now(timezone.utc).isoformat()
        model_path = MODEL_DIR / f"{model_version}.joblib"
        metadata_path = MODEL_DIR / f"{model_version}.json"
        vectorizer = pipeline.named_steps["vectorizer"]

        metrics = {
            "accuracy": round(float(accuracy_score(test_labels, predicted)), 6),
            "macroPrecision": round(float(report["macro avg"]["precision"]), 6),
            "macroRecall": round(float(report["macro avg"]["recall"]), 6),
            "macroF1": round(float(report["macro avg"]["f1-score"]), 6),
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
        metadata = {
            "modelVersion": model_version,
            "featureSchemaVersion": "grid-fault-v1",
            "datasetName": request.dataset_name,
            "trainedAt": trained_at,
            "sampleCount": len(request.samples),
            "trainingSampleCount": len(train_features),
            "evaluationSampleCount": len(test_features),
            "labelDistribution": dict(sorted(distribution.items())),
            "featureNames": vectorizer.get_feature_names_out().tolist(),
            "metrics": metrics,
            "evaluationNote": evaluation_note,
            "modelFile": model_path.name,
        }

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "pipeline": pipeline,
                "modelVersion": model_version,
                "trainedAt": trained_at,
                "featureNames": metadata["featureNames"],
            },
            model_path,
        )
        _write_json_atomic(metadata_path, metadata)
        _write_json_atomic(ACTIVE_MODEL_FILE, metadata)

    return metadata


@app.post("/predict")
def predict(request: PredictionRequest) -> dict[str, Any]:
    with MODEL_LOCK:
        metadata = _read_active_metadata(required=True)
        model_path = MODEL_DIR / metadata["modelFile"]
        if not model_path.exists():
            raise HTTPException(status_code=503, detail="当前模型文件不存在，请重新训练")

        artifact = joblib.load(model_path)
        pipeline: Pipeline = artifact["pipeline"]
        probabilities = pipeline.predict_proba([request.features])[0]
        classifier: RandomForestClassifier = pipeline.named_steps["classifier"]
        ranked = sorted(
            zip(classifier.classes_.tolist(), probabilities.tolist()),
            key=lambda item: item[1],
            reverse=True,
        )[: request.top_k]

    return {
        "modelVersion": metadata["modelVersion"],
        "predictedFaultType": ranked[0][0],
        "confidence": round(float(ranked[0][1]), 6),
        "candidates": [
            {"faultType": label, "confidence": round(float(probability), 6)}
            for label, probability in ranked
        ],
        "receivedFeatures": request.features,
    }


@app.get("/models/active")
def active_model() -> dict[str, Any]:
    return _read_active_metadata(required=True)


def _split_dataset(
    features: list[dict[str, float]],
    labels: list[str],
    random_state: int,
) -> tuple[list[dict[str, float]], list[dict[str, float]], list[str], list[str], str]:
    distribution = Counter(labels)
    class_count = len(distribution)
    enough_for_holdout = len(labels) >= max(12, class_count * 4) and min(distribution.values()) >= 2
    if not enough_for_holdout:
        return features, features, labels, labels, "样本较少，指标基于训练集回代；正式使用前应增加样本。"

    test_count = max(class_count, round(len(labels) * 0.2))
    test_count = min(test_count, len(labels) - class_count)
    split = train_test_split(
        features,
        labels,
        test_size=test_count,
        random_state=random_state,
        stratify=labels,
    )
    return split[0], split[1], split[2], split[3], "指标基于分层留出测试集。"


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
