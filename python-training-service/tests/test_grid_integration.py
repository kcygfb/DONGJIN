from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pandapower as pp
import pytest
from neo4j import GraphDatabase
from pydantic import ValidationError
from redis import Redis

from app.grid.artifact_service import initialize_grid_package
from app.grid.publishers.neo4j import (
    MANAGED_BY,
    get_active_neo4j_grid,
    publish_active_grid_to_neo4j,
)
from app.grid.publishers.redis_snapshot import (
    ACTIVE_SNAPSHOT_KEY,
    RedisSnapshotPublisher,
)
from app.grid.simulation.engine import (
    GridSimulationEngine,
    _collect_snapshot,
)
from app.grid.simulation.models import GridSnapshot
from app.grid.simulation.profiles import SimBenchProfileDriver


pytestmark = pytest.mark.integration


def test_neo4j_projection_is_complete_idempotent_and_version_safe(
    integration_settings,
) -> None:
    settings = integration_settings
    first = publish_active_grid_to_neo4j(settings.grid_id, settings)
    second = publish_active_grid_to_neo4j(settings.grid_id, settings)

    assert first["verified"] is True
    assert second["nodeCounts"] == first["nodeCounts"]
    assert second["relationshipCounts"] == first["relationshipCounts"]
    assert sum(first["nodeCounts"].values()) == 874
    assert first["nodeCounts"]["bus"] == 144
    assert first["relationshipCounts"]["FROM_TERMINAL"] == 147

    topology_path = (
        settings.resolved_data_dir
        / settings.grid_id
        / "v1"
        / "topology.json"
    )
    topology = json.loads(topology_path.read_text(encoding="utf-8"))
    source_line = next(
        item for item in topology["elements"]
        if item["elementType"] == "line"
    )
    driver = _neo4j_driver(settings)
    try:
        with driver.session(database=settings.neo4j_database) as session:
            record = session.run(
                """
                MATCH (line:Line {
                    businessId: $businessId,
                    topologyVersion: "v1"
                })
                RETURN line.length_km AS lengthKm,
                       line.r_ohm_per_km AS resistance,
                       line.managedBy AS managedBy
                """,
                businessId=source_line["businessId"],
            ).single()
            assert record is not None
            assert record["lengthKm"] == pytest.approx(
                source_line["parameters"]["length_km"]
            )
            assert record["resistance"] == pytest.approx(
                source_line["parameters"]["r_ohm_per_km"]
            )
            assert record["managedBy"] == MANAGED_BY

            session.run(
                "CREATE (:UserNode {name: 'p6-preserve-me'})"
            ).consume()
    finally:
        driver.close()

    initialize_grid_package(
        simbench_code=settings.simbench_code,
        topology_version="v2",
        force=True,
        settings=settings,
    )
    publish_active_grid_to_neo4j(settings.grid_id, settings)
    active = get_active_neo4j_grid(settings)
    assert active["topologyVersion"] == "v2"

    driver = _neo4j_driver(settings)
    try:
        with driver.session(database=settings.neo4j_database) as session:
            versions = session.run(
                """
                MATCH (grid:GridModel {managedBy: $managedBy})
                RETURN grid.topologyVersion AS version,
                       grid.active AS active
                ORDER BY version
                """,
                managedBy=MANAGED_BY,
            ).data()
            assert versions == [
                {"version": "v1", "active": False},
                {"version": "v2", "active": True},
            ]
            assert session.run(
                "MATCH (node:UserNode {name: 'p6-preserve-me'}) "
                "RETURN count(node) AS count"
            ).single()["count"] == 1
    finally:
        driver.close()


def test_redis_snapshot_is_atomic_ttl_bound_and_restart_readable(
    integration_settings,
) -> None:
    settings = integration_settings
    snapshot = _real_snapshot(settings, "integration-redis-1")
    publisher = RedisSnapshotPublisher(settings)
    payload, _ = publisher.publish(snapshot)

    client = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
    )
    try:
        assert client.get(ACTIVE_SNAPSHOT_KEY) == snapshot.snapshot_id
        ttl = client.ttl(f"dongjin:snapshot:{snapshot.snapshot_id}")
        assert 0 < ttl <= settings.snapshot_ttl_seconds
        assert payload["topologyVersion"] == "v1"
    finally:
        client.close()

    restarted_publisher = RedisSnapshotPublisher(settings)
    restored = restarted_publisher.current()
    assert restored["snapshotId"] == snapshot.snapshot_id
    assert len(restored["buses"]) == 144
    assert len(restored["lines"]) == 147

    invalid = snapshot.model_dump(by_alias=True)
    invalid["lines"][next(iter(invalid["lines"]))][
        "loadingPercent"
    ] = float("nan")
    with pytest.raises(ValidationError):
        GridSnapshot.model_validate(invalid)
    assert restarted_publisher.current()["snapshotId"] == snapshot.snapshot_id


def test_end_to_end_static_and_dynamic_ids_are_consistent(
    integration_settings,
) -> None:
    settings = integration_settings
    publish_active_grid_to_neo4j(settings.grid_id, settings)
    engine = GridSimulationEngine(settings)
    started = engine.start(profile_strategy="linear")
    assert started["state"] == "RUNNING"
    try:
        _wait_until(lambda: engine.status()["step"] >= 3)
        snapshot = engine.current_snapshot()
        assert snapshot["gridId"] == settings.grid_id
        assert snapshot["topologyVersion"] == "v1"
        assert snapshot["converged"] is True
        line_id = next(iter(snapshot["lines"]))

        driver = _neo4j_driver(settings)
        try:
            with driver.session(
                database=settings.neo4j_database
            ) as session:
                record = session.run(
                    """
                    MATCH (grid:GridModel {
                        active: true,
                        managedBy: $managedBy
                    })-[:CONTAINS]->(line:Line {
                        businessId: $businessId
                    })
                    RETURN grid.topologyVersion AS topologyVersion,
                           line.length_km AS lengthKm
                    """,
                    managedBy=MANAGED_BY,
                    businessId=line_id,
                ).single()
                assert record is not None
                assert record["topologyVersion"] == snapshot[
                    "topologyVersion"
                ]
                assert record["lengthKm"] > 0
        finally:
            driver.close()
    finally:
        engine.stop()


def _real_snapshot(settings, snapshot_id):
    manifest_path = (
        settings.resolved_data_dir
        / settings.grid_id
        / "v1"
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    network_path = manifest_path.parent / "network.json"
    profile_driver = SimBenchProfileDriver(
        network_path,
        settings.grid_id,
        settings,
    )
    simulation_time = datetime(2016, 1, 1, tzinfo=UTC)
    provenance = profile_driver.apply(simulation_time, "linear")
    started = time.perf_counter()
    pp.runpp(profile_driver.net, numba=False)
    duration = (time.perf_counter() - started) * 1000
    return _collect_snapshot(
        profile_driver.net,
        manifest,
        snapshot_id,
        simulation_time,
        provenance,
        "linear",
        0.0,
        duration,
    )


def _neo4j_driver(settings):
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(
            settings.neo4j_username,
            settings.neo4j_password.get_secret_value(),
        ),
    )


def _wait_until(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("condition was not reached before timeout")
