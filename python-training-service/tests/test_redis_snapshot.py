from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.grid.publishers.redis_snapshot import (
    ACTIVE_SNAPSHOT_KEY,
    RedisSnapshotPublisher,
    SNAPSHOT_KEY_PREFIX,
)
from app.grid.settings import GridSettings
from app.grid.simulation.models import GridSnapshot


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []

    def set(self, key, value, ex=None):
        self.commands.append(("set", key, value, ex))
        return self

    def execute(self):
        if self.client.fail_execute:
            raise ConnectionError("injected transaction failure")
        for _, key, value, ex in self.commands:
            self.client.values[key] = value
            self.client.ttls[key] = ex
        self.client.last_transaction = list(self.commands)
        return [True] * len(self.commands)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.fail_execute = False
        self.last_transaction = []

    def pipeline(self, transaction=True):
        assert transaction is True
        return FakePipeline(self)

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value
        self.ttls[key] = ex
        return True

    def delete(self, key):
        self.values.pop(key, None)

    def close(self):
        return None


def test_snapshot_publish_is_complete_ttl_bound_and_pointer_last(
    monkeypatch,
) -> None:
    settings = GridSettings(snapshot_ttl_seconds=120)
    publisher = RedisSnapshotPublisher(settings)
    redis = FakeRedis()
    monkeypatch.setattr(publisher, "_client", lambda: redis)
    snapshot = _snapshot("snapshot-1")

    payload, performance = publisher.publish(snapshot)

    snapshot_key = f"{SNAPSHOT_KEY_PREFIX}snapshot-1"
    assert redis.ttls[snapshot_key] == 120
    assert redis.ttls[ACTIVE_SNAPSHOT_KEY] == 120
    assert redis.last_transaction[-1][1] == ACTIVE_SNAPSHOT_KEY
    assert redis.values[ACTIVE_SNAPSHOT_KEY] == "snapshot-1"
    assert payload["buses"]["grid:bus:0"]["vmPu"] == 1.0
    assert performance["redisPublishDurationMs"] >= 0


def test_failed_transaction_keeps_previous_active_pointer(monkeypatch) -> None:
    publisher = RedisSnapshotPublisher(GridSettings())
    redis = FakeRedis()
    redis.values[ACTIVE_SNAPSHOT_KEY] = "previous"
    redis.fail_execute = True
    monkeypatch.setattr(publisher, "_client", lambda: redis)

    with pytest.raises(Exception, match="Redis快照发布失败"):
        publisher.publish(_snapshot("broken"))

    assert redis.values[ACTIVE_SNAPSHOT_KEY] == "previous"
    assert f"{SNAPSHOT_KEY_PREFIX}broken" not in redis.values


def test_snapshot_schema_rejects_non_finite_values() -> None:
    data = _snapshot("invalid").model_dump(by_alias=True)
    data["buses"]["grid:bus:0"]["vmPu"] = float("nan")
    with pytest.raises(ValidationError):
        GridSnapshot.model_validate(data)


def _snapshot(snapshot_id: str) -> GridSnapshot:
    return GridSnapshot.model_validate(
        {
            "snapshotId": snapshot_id,
            "gridId": "grid",
            "topologyVersion": "v1",
            "schemaVersion": "grid-snapshot-v1",
            "simulationTime": "2016-01-01T00:00:00+00:00",
            "profileSourceTime": "2016-01-01T00:00:00+00:00",
            "publishedAt": "2026-07-23T00:00:00+00:00",
            "converged": True,
            "calculationDurationMs": 10.0,
            "profileStrategy": "linear",
            "profileProvenance": {},
            "performance": {
                "inputPreparationDurationMs": 1.0,
                "powerFlowDurationMs": 8.0,
                "snapshotBuildDurationMs": 1.0,
                "serializationDurationMs": 0.0,
            },
            "buses": {
                "grid:bus:0": {
                    "vmPu": 1.0,
                    "vaDegree": 0.0,
                    "pMw": 1.0,
                    "qMvar": 0.1,
                }
            },
            "lines": {},
            "transformers": {},
            "switches": {},
            "loads": {},
            "generators": {},
            "externalGrids": {},
        }
    )
