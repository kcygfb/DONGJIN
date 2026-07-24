from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from app.grid.datasets.builder import (
    DatasetBuildRequest,
    build_dataset,
)
from app.grid.scenarios.models import (
    EventType,
    ScenarioBatchRequest,
)
from app.grid.scenarios.service import generate_scenario_batch
from app.grid.training.trainer import (
    OfflineTrainingRequest,
    train_offline_model,
)


def test_offline_pipeline_exposes_white_box_files(
    generated_grid,
    tmp_path: Path,
) -> None:
    base_settings, _ = generated_grid
    settings = base_settings.model_copy(
        update={
            "scenario_dir": tmp_path / "scenarios",
            "dataset_dir": tmp_path / "datasets",
            "offline_model_dir": tmp_path / "models",
        }
    )
    batch = generate_scenario_batch(
        ScenarioBatchRequest(
            batchId="test-white-box-batch",
            samplesPerType=3,
            eventTypes=[
                EventType.NORMAL,
                EventType.LINE_OUTAGE,
                EventType.MEASUREMENT_DROPOUT,
            ],
            randomSeed=1234,
        ),
        settings,
    )
    batch_dir = Path(batch["artifactPath"])
    assert batch["scenarioCount"] == 9
    assert (batch_dir / "scenario-index.csv").is_file()
    run_id = pd.read_csv(
        batch_dir / "scenario-index.csv"
    )["scenarioRunId"].iloc[0]
    run_dir = batch_dir / "runs" / run_id
    expected = {
        "scenario.json",
        "truth/baseline.json",
        "truth/event.json",
        "measurements/frame.json",
        "measurements/transform-audit.jsonl",
        "labels.json",
        "summary.md",
        "validation.json",
    }
    assert {
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file()
    } == expected
    validation = json.loads(
        (run_dir / "validation.json").read_text(encoding="utf-8")
    )
    assert validation["status"] == "passed"

    dataset = build_dataset(
        DatasetBuildRequest(
            batchId=batch["batchId"],
            datasetId="test-white-box-dataset",
            randomSeed=1234,
        ),
        settings,
    )
    dataset_dir = Path(dataset["artifactPath"])
    assert dataset["scenarioCount"] == 9
    assert dataset["rowCount"] == (
        dataset["scenarioCount"] * dataset["vertexCount"]
    )
    assert (dataset_dir / "samples.csv").is_file()
    assert (dataset_dir / "samples.jsonl").is_file()
    assert (dataset_dir / "train.parquet").is_file()
    assert (dataset_dir / "validation.parquet").is_file()
    assert (dataset_dir / "test.parquet").is_file()
    summary = pd.read_csv(dataset_dir / "sample-summary.csv")
    split_sets = {
        split: set(group["scenarioRunId"])
        for split, group in summary.groupby("split")
    }
    assert split_sets["train"].isdisjoint(split_sets["validation"])
    assert split_sets["train"].isdisjoint(split_sets["test"])
    assert split_sets["validation"].isdisjoint(split_sets["test"])

    model = train_offline_model(
        OfflineTrainingRequest(
            datasetId=dataset["datasetId"],
            modelId="test-white-box-model",
            randomSeed=1234,
            maximumEpochs=10,
            minimumTargetMacroF1=0,
            minimumLocationTop5Accuracy=0,
            minimumExactDiagnosisAccuracy=0,
            minimumNormalGraphAccuracy=0,
            minimumFaultDetectionRecall=0,
        ),
        settings,
    )
    model_dir = Path(model["artifactPath"])
    assert model["status"] == "QUALIFIED"
    assert (model_dir / "model.joblib").is_file()
    assert (model_dir / "training-history.csv").is_file()
    assert (model_dir / "metrics.json").is_file()
    assert (model_dir / "test-predictions.csv").is_file()


def test_offline_batch_requires_normal_samples() -> None:
    with pytest.raises(ValidationError, match="NORMAL"):
        ScenarioBatchRequest(
            samplesPerType=3,
            eventTypes=[
                EventType.LINE_OUTAGE,
                EventType.LOAD_SURGE,
            ],
        )
