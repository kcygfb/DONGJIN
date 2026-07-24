from __future__ import annotations

from pathlib import Path

import pytest
from neo4j import GraphDatabase
from redis import Redis

from app.grid.artifact_service import initialize_grid_package
from app.grid.settings import GridSettings


@pytest.fixture(scope="session")
def generated_grid(tmp_path_factory: pytest.TempPathFactory):
    data_dir = tmp_path_factory.mktemp("p6-grid")
    settings = GridSettings(data_dir=data_dir)
    result = initialize_grid_package(
        simbench_code="1-MV-urban--0-sw",
        topology_version="v1",
        force=True,
        settings=settings,
    )
    return settings, result


@pytest.fixture
def integration_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> GridSettings:
    if not _integration_enabled():
        pytest.skip("设置DONGJIN_RUN_INTEGRATION=1后执行数据库集成测试")
    settings = GridSettings(
        data_dir=tmp_path / "grids",
        neo4j_uri="bolt://127.0.0.1:17687",
        neo4j_username="neo4j",
        neo4j_password="p6-test-only",
        redis_url="redis://127.0.0.1:16379/15",
        simulation_interval_seconds=0.2,
        snapshot_ttl_seconds=60,
        health_timeout_seconds=5,
    )
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(
            settings.neo4j_username,
            settings.neo4j_password.get_secret_value(),
        ),
    )
    try:
        with driver.session(database=settings.neo4j_database) as session:
            session.run("MATCH (node) DETACH DELETE node").consume()
    finally:
        driver.close()
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        redis.flushdb()
    finally:
        redis.close()
    initialize_grid_package(
        simbench_code="1-MV-urban--0-sw",
        topology_version="v1",
        force=True,
        settings=settings,
    )
    return settings


def _integration_enabled() -> bool:
    import os

    return os.getenv("DONGJIN_RUN_INTEGRATION") == "1"
