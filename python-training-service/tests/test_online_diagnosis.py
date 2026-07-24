from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
import pytest

from app.grid.offline_io import read_json
from app.grid.online.diagnosis import diagnose_measurement
from app.grid.online.evaluation import evaluate_offline_model
from app.grid.online.features import adapt_measurement
from app.grid.online.model_manager import (
    check_model_compatibility,
    get_selected_model,
    model_selection_history,
    rollback_inference_model,
    select_inference_model,
)
from app.grid.online.shadow import (
    create_shadow_session,
    diagnose_shadow_session,
    reveal_shadow_session,
)
from app.grid.online.short_circuit import run_short_circuit_analysis
from app.grid.scenarios.models import EventType
from app.grid.settings import get_grid_settings


MODEL_ID = "p1-v1-gcn-model-v3"
DATASET_ID = "p1-v1-gnn-dataset-v1"


def _settings(tmp_path: Path):
    base = get_grid_settings()
    return base.model_copy(
        update={
            "diagnosis_dir": tmp_path / "diagnoses",
            "shadow_dir": tmp_path / "shadow",
            "short_circuit_dir": tmp_path / "short-circuit",
            "model_history_dir": tmp_path / "model-history",
            "evaluation_dir": tmp_path / "evaluations",
            "inference_model_config": tmp_path / "inference-model.json",
        }
    )


def _archived_frame(settings):
    dataset_dir = settings.resolved_dataset_dir / DATASET_ID
    source = read_json(dataset_dir / "source-scenarios.json")
    run = source["scenarioRuns"][0]
    batch_dir = (
        settings.resolved_scenario_dir
        / source["batchId"]
        / "runs"
        / run["scenarioRunId"]
    )
    return (
        run,
        read_json(batch_dir / "measurements" / "frame.json"),
        read_json(batch_dir / "truth" / "baseline.json"),
        dataset_dir,
    )


def test_manual_selection_is_compatible_and_audited(tmp_path):
    settings = _settings(tmp_path)
    compatibility = check_model_compatibility(MODEL_ID, settings)
    assert compatibility["compatible"] is True

    selected = select_inference_model(
        MODEL_ID, actor="pytest", settings=settings
    )
    assert selected["modelId"] == MODEL_ID
    assert get_selected_model(settings)["selectedBy"] == "pytest"
    history = model_selection_history(settings)
    assert len(history) == 1
    assert history[0]["action"] == "SELECT"


def test_manual_rollback_restores_previous_selection(tmp_path):
    settings = _settings(tmp_path)
    select_inference_model(
        "p1-v1-gcn-model-v2", actor="pytest", settings=settings
    )
    select_inference_model(MODEL_ID, actor="pytest", settings=settings)
    rolled_back = rollback_inference_model(
        actor="pytest", settings=settings
    )
    assert rolled_back["modelId"] == "p1-v1-gcn-model-v2"
    assert model_selection_history(settings)[-1]["action"] == "ROLLBACK"


def test_online_adapter_matches_frozen_dataset_features(tmp_path):
    settings = _settings(tmp_path)
    run, measurement, baseline, dataset_dir = _archived_frame(settings)
    model_dir = settings.resolved_offline_model_dir / MODEL_ID
    manifest = read_json(model_dir / "manifest.json")
    artifact = joblib.load(model_dir / "model.joblib")
    frame = adapt_measurement(
        measurement,
        manifest,
        artifact,
        source_mode="REPLAY",
        baseline=baseline,
        settings=settings,
    )
    frozen = pd.read_parquet(dataset_dir / f"{run['split']}.parquet")
    frozen = frozen.loc[
        frozen["scenarioRunId"] == run["scenarioRunId"]
    ].sort_values("vertexIndex")
    for row, (_, expected) in zip(frame["rows"], frozen.iterrows()):
        assert row["businessId"] == expected["businessId"]
        for name in frame["featureNames"]:
            assert row[f"feature.{name}"] == pytest.approx(
                expected[f"feature.{name}"], abs=1e-6
            )


def test_replay_diagnosis_writes_visible_white_box_files(tmp_path):
    settings = _settings(tmp_path)
    select_inference_model(MODEL_ID, actor="pytest", settings=settings)
    _, measurement, baseline, _ = _archived_frame(settings)
    result = diagnose_measurement(
        measurement,
        source_mode="REPLAY",
        baseline=baseline,
        settings=settings,
    )
    artifact_path = Path(result["artifactPath"])
    assert result["modelId"] == MODEL_ID
    assert (artifact_path / "input-measurement.json").is_file()
    assert (artifact_path / "feature-frame.csv").is_file()
    assert (artifact_path / "probabilities.csv").is_file()
    assert (artifact_path / "result.json").is_file()


def test_manual_evaluation_does_not_change_selection(tmp_path):
    settings = _settings(tmp_path)
    select_inference_model(MODEL_ID, actor="pytest", settings=settings)
    before = get_selected_model(settings)
    result = evaluate_offline_model(
        MODEL_ID, DATASET_ID, settings=settings
    )
    after = get_selected_model(settings)
    assert result["selectionChanged"] is False
    assert result["metrics"]["scenarioCount"] > 0
    assert before["modelId"] == after["modelId"] == MODEL_ID


def test_shadow_truth_is_hidden_until_reveal(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    select_inference_model(MODEL_ID, actor="pytest", settings=settings)
    monkeypatch.setattr(
        "app.grid.online.shadow._publish_shadow_measurement",
        lambda *args, **kwargs: None,
    )
    session = create_shadow_session(
        EventType.LINE_OUTAGE,
        random_seed=20260724,
        settings=settings,
    )
    assert "groundTruth" not in session
    assert "eventType" not in session
    assert "targetBusinessId" not in session
    prediction = diagnose_shadow_session(
        session["sessionId"], settings=settings
    )
    assert prediction["mode"] == "SHADOW"
    comparison = reveal_shadow_session(
        session["sessionId"], settings=settings
    )
    assert comparison["groundTruth"]["primaryLabel"] == "LINE_OUTAGE"


def test_short_circuit_is_archived_outside_snapshots(tmp_path):
    settings = _settings(tmp_path)
    target = f"{settings.grid_id}:bus:0"
    result = run_short_circuit_analysis(
        target,
        fault_type="3ph",
        s_sc_mva=1000.0,
        rx=0.1,
        settings=settings,
    )
    assert result["status"] == "COMPLETED"
    assert result["targetBusinessId"] == target
    assert Path(result["artifactPath"]).is_dir()
    assert all(
        "snapshot" not in str(path).lower()
        for path in Path(result["artifactPath"]).iterdir()
    )
