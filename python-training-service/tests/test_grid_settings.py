import pytest
from pydantic import ValidationError

from app.grid.settings import GridSettings


def test_defaults_define_first_simbench_grid() -> None:
    settings = GridSettings()

    assert settings.simbench_code == "1-MV-urban--0-sw"
    assert settings.grid_id == "simbench-1-mv-urban-0-sw"
    assert settings.interpolation_strategy == "linear"
    assert settings.snapshot_ttl_seconds == 600


def test_existing_neo4j_environment_names_are_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEO4J_URI", "neo4j://127.0.0.1:17687")
    monkeypatch.setenv("NEO4J_USERNAME", "dongjin")
    monkeypatch.setenv("DONGJIN_INTERPOLATION_STRATEGY", "hold")

    settings = GridSettings.from_environment()

    assert settings.neo4j_uri == "neo4j://127.0.0.1:17687"
    assert settings.neo4j_username == "dongjin"
    assert settings.interpolation_strategy == "hold"


def test_snapshot_ttl_cannot_be_shorter_than_interval() -> None:
    with pytest.raises(ValidationError):
        GridSettings(simulation_interval_seconds=5, snapshot_ttl_seconds=4)
