from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator


SERVICE_DIR = Path(__file__).resolve().parents[2]


class GridSettings(BaseModel):
    """Grid settings using the existing DONGJIN and NEO4J environment style."""

    simbench_code: str = "1-MV-urban--0-sw"
    data_dir: Path = SERVICE_DIR / "artifacts" / "grids"
    scenario_dir: Path = SERVICE_DIR / "artifacts" / "scenarios"
    dataset_dir: Path = SERVICE_DIR / "artifacts" / "datasets"
    offline_model_dir: Path = SERVICE_DIR / "artifacts" / "models"
    simulation_interval_seconds: float = Field(default=1.0, gt=0)
    interpolation_strategy: Literal["hold", "linear"] = "linear"
    profile_start_time: datetime = datetime(2016, 1, 1, tzinfo=UTC)
    profile_interval_seconds: int = Field(default=900, ge=1)
    profile_end_behavior: Literal["stop", "wrap"] = "wrap"
    snapshot_ttl_seconds: int = Field(default=600, ge=1)
    health_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    neo4j_uri: str = "neo4j://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("neo4j")
    neo4j_database: str = "neo4j"
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    @model_validator(mode="after")
    def validate_snapshot_retention(self) -> "GridSettings":
        if self.snapshot_ttl_seconds < self.simulation_interval_seconds:
            raise ValueError(
                "snapshot_ttl_seconds must be greater than or equal to "
                "simulation_interval_seconds"
            )
        if self.profile_start_time.tzinfo is None:
            raise ValueError("profile_start_time必须包含时区")
        return self

    @classmethod
    def from_environment(cls) -> "GridSettings":
        return cls(
            simbench_code=os.getenv("DONGJIN_SIMBENCH_CODE", "1-MV-urban--0-sw"),
            data_dir=Path(
                os.getenv(
                    "DONGJIN_GRID_DATA_DIR",
                    str(SERVICE_DIR / "artifacts" / "grids"),
                )
            ),
            scenario_dir=Path(
                os.getenv(
                    "DONGJIN_SCENARIO_DATA_DIR",
                    str(SERVICE_DIR / "artifacts" / "scenarios"),
                )
            ),
            dataset_dir=Path(
                os.getenv(
                    "DONGJIN_DATASET_DIR",
                    str(SERVICE_DIR / "artifacts" / "datasets"),
                )
            ),
            offline_model_dir=Path(
                os.getenv(
                    "DONGJIN_OFFLINE_MODEL_DIR",
                    str(SERVICE_DIR / "artifacts" / "models"),
                )
            ),
            simulation_interval_seconds=os.getenv(
                "DONGJIN_SIMULATION_INTERVAL_SECONDS", "1.0"
            ),
            interpolation_strategy=os.getenv(
                "DONGJIN_INTERPOLATION_STRATEGY", "linear"
            ),
            profile_start_time=os.getenv(
                "DONGJIN_PROFILE_START_TIME", "2016-01-01T00:00:00+00:00"
            ),
            profile_interval_seconds=os.getenv(
                "DONGJIN_PROFILE_INTERVAL_SECONDS", "900"
            ),
            profile_end_behavior=os.getenv(
                "DONGJIN_PROFILE_END_BEHAVIOR", "wrap"
            ),
            snapshot_ttl_seconds=os.getenv("DONGJIN_SNAPSHOT_TTL_SECONDS", "600"),
            health_timeout_seconds=os.getenv(
                "DONGJIN_HEALTH_TIMEOUT_SECONDS", "2.0"
            ),
            neo4j_uri=os.getenv("NEO4J_URI", "neo4j://localhost:7687"),
            neo4j_username=os.getenv("NEO4J_USERNAME", "neo4j"),
            neo4j_password=os.getenv("NEO4J_PASSWORD", "neo4j"),
            neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        )

    @property
    def grid_id(self) -> str:
        normalized = self.simbench_code.lower().replace("--", "-")
        return f"simbench-{normalized}"

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir.expanduser().resolve()

    @property
    def resolved_scenario_dir(self) -> Path:
        return self.scenario_dir.expanduser().resolve()

    @property
    def resolved_dataset_dir(self) -> Path:
        return self.dataset_dir.expanduser().resolve()

    @property
    def resolved_offline_model_dir(self) -> Path:
        return self.offline_model_dir.expanduser().resolve()


@lru_cache
def get_grid_settings() -> GridSettings:
    return GridSettings.from_environment()
