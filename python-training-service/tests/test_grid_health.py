from app.grid import health
from app.grid.settings import GridSettings


def test_grid_health_is_ok_when_all_dependencies_are_available(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        health,
        "_package_health",
        lambda name: {"status": "ok", "version": "test", "detail": None},
    )
    monkeypatch.setattr(
        health,
        "_neo4j_health",
        lambda settings: {"status": "ok", "version": "test", "detail": None},
    )
    monkeypatch.setattr(
        health,
        "_redis_health",
        lambda settings: {"status": "ok", "version": "test", "detail": None},
    )

    result = health.build_grid_health(GridSettings(data_dir=tmp_path))

    assert result["status"] == "ok"
    assert result["activeGrid"]["status"] == "not_initialized"
    assert result["activeGrid"]["simbenchCode"] == "1-MV-urban--0-sw"


def test_grid_health_is_degraded_when_redis_is_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        health,
        "_package_health",
        lambda name: {"status": "ok", "version": "test", "detail": None},
    )
    monkeypatch.setattr(
        health,
        "_neo4j_health",
        lambda settings: {"status": "ok", "version": "test", "detail": None},
    )
    monkeypatch.setattr(
        health,
        "_redis_health",
        lambda settings: {
            "status": "unavailable",
            "version": "test",
            "detail": "connection refused",
        },
    )

    result = health.build_grid_health(GridSettings(data_dir=tmp_path))

    assert result["status"] == "degraded"
    assert result["components"]["redis"]["status"] == "unavailable"
