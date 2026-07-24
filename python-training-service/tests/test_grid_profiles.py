from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from app.grid.artifact_service import GridPackageError
from app.grid.settings import GridSettings
from app.grid.simulation.profiles import SimBenchProfileDriver


def test_profile_driver_maps_every_dynamic_device_and_supports_interpolation(
    generated_grid,
) -> None:
    settings, result = generated_grid
    driver = SimBenchProfileDriver(
        Path(result["artifactPath"]) / "network.json",
        result["gridId"],
        settings,
    )
    metadata = driver.metadata()

    assert metadata["timeSteps"] == 35136
    assert len(metadata["deviceProfiles"]) == 273
    assert set(metadata["strategies"]) == {"hold", "linear"}
    assert all(
        device["profile"]
        for device in metadata["deviceProfiles"].values()
    )

    start = datetime(2016, 1, 1, tzinfo=UTC)
    first = driver.absolute_values[("load", "p_mw")].iloc[0].to_numpy()
    second = driver.absolute_values[("load", "p_mw")].iloc[1].to_numpy()

    hold = driver.apply(start + timedelta(minutes=7, seconds=30), "hold")
    assert hold["lowerIndex"] == hold["upperIndex"] == 0
    assert np.allclose(driver.net.load.p_mw.to_numpy(), first)

    linear = driver.apply(
        start + timedelta(minutes=7, seconds=30),
        "linear",
    )
    assert linear["lowerIndex"] == 0
    assert linear["upperIndex"] == 1
    assert linear["fraction"] == pytest.approx(0.5)
    assert np.allclose(
        driver.net.load.p_mw.to_numpy(),
        (first + second) / 2,
    )


def test_profile_time_boundaries_are_explicit(generated_grid) -> None:
    settings, result = generated_grid
    network_path = Path(result["artifactPath"]) / "network.json"
    driver = SimBenchProfileDriver(
        network_path,
        result["gridId"],
        settings,
    )

    with pytest.raises(GridPackageError, match="早于"):
        driver.resolve_position(
            datetime(2015, 12, 31, 23, 59, tzinfo=UTC),
            "linear",
        )

    wrapped = driver.resolve_position(
        driver.profile_end_time + timedelta(minutes=15),
        "linear",
    )
    assert wrapped.lower_index == 0
    assert wrapped.wrapped is True

    stop_settings = settings.model_copy(
        update={"profile_end_behavior": "stop"}
    )
    stop_driver = SimBenchProfileDriver(
        network_path,
        result["gridId"],
        stop_settings,
    )
    with pytest.raises(GridPackageError, match="末尾"):
        stop_driver.resolve_position(
            stop_driver.profile_end_time + timedelta(seconds=1),
            "linear",
        )
