from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.grid.settings import GridSettings
from app.grid.simulation import engine as engine_module
from app.grid.simulation.engine import (
    GridSimulationEngine,
    SimulationStateError,
)


class RecordingPublisher:
    def __init__(self):
        self.snapshots = []
        self.statuses = []
        self.first_snapshot = threading.Event()

    def current(self):
        return None

    def publish(self, snapshot):
        self.snapshots.append(snapshot)
        self.first_snapshot.set()
        return snapshot.model_dump(by_alias=True), {
            "serializationDurationMs": 0.1,
            "redisPublishDurationMs": 0.1,
        }

    def write_simulation_status(self, status):
        self.statuses.append(dict(status))


def test_state_machine_prevents_duplicate_loops_and_controls_time(
    generated_grid,
    monkeypatch,
) -> None:
    _, result = generated_grid
    artifact_dir = Path(result["artifactPath"])
    settings = GridSettings(
        data_dir=artifact_dir.parents[1],
        simulation_interval_seconds=0.1,
        snapshot_ttl_seconds=60,
    )
    manifest = {
        "gridId": result["gridId"],
        "topologyVersion": result["topologyVersion"],
        "schemaVersion": result["schemaVersion"],
    }
    monkeypatch.setattr(
        engine_module,
        "resolve_active_grid_package",
        lambda _settings: (manifest, artifact_dir),
    )
    monkeypatch.setattr(
        engine_module,
        "assert_active_neo4j_grid",
        lambda *_args, **_kwargs: None,
    )
    publisher = RecordingPublisher()
    engine = GridSimulationEngine(settings)
    engine.publisher = publisher

    started = engine.start(profile_strategy="linear")
    assert started["state"] == "RUNNING"
    with pytest.raises(SimulationStateError, match="第二个"):
        engine.start()
    assert publisher.first_snapshot.wait(10)
    _wait_until(lambda: engine.status()["step"] >= 1)

    paused = engine.pause()
    assert paused["state"] == "PAUSED"
    time.sleep(0.4)
    paused_step = engine.status()["step"]
    time.sleep(0.3)
    assert engine.status()["step"] == paused_step

    resumed = engine.resume()
    assert resumed["state"] == "RUNNING"
    _wait_until(lambda: engine.status()["step"] > paused_step)
    stopped = engine.stop()
    assert stopped["state"] == "STOPPED"
    assert stopped["workerAlive"] is False
    assert len(publisher.snapshots) >= 2


def test_non_convergence_does_not_publish_snapshot(
    generated_grid,
    monkeypatch,
) -> None:
    _, result = generated_grid
    artifact_dir = Path(result["artifactPath"])
    settings = GridSettings(
        data_dir=artifact_dir.parents[1],
        simulation_interval_seconds=0.1,
        snapshot_ttl_seconds=60,
    )
    manifest = {
        "gridId": result["gridId"],
        "topologyVersion": result["topologyVersion"],
        "schemaVersion": result["schemaVersion"],
    }
    monkeypatch.setattr(
        engine_module,
        "resolve_active_grid_package",
        lambda _settings: (manifest, artifact_dir),
    )
    monkeypatch.setattr(
        engine_module,
        "assert_active_neo4j_grid",
        lambda *_args, **_kwargs: None,
    )

    def fail_power_flow(net, **_kwargs):
        net.converged = False

    monkeypatch.setattr(engine_module.pp, "runpp", fail_power_flow)
    publisher = RecordingPublisher()
    engine = GridSimulationEngine(settings)
    engine.publisher = publisher
    engine.start()
    _wait_until(lambda: engine.status()["state"] == "ERROR")

    assert publisher.snapshots == []
    assert "未收敛" in engine.status()["lastError"]


def _wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not reached before timeout")
