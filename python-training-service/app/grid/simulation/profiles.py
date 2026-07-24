from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import pandapower as pp
import simbench as sb

from app.grid.artifact_service import GridPackageError
from app.grid.settings import GridSettings


ProfileStrategy = Literal["hold", "linear"]


@dataclass(frozen=True)
class ProfilePosition:
    lower_index: int
    upper_index: int
    fraction: float
    source_time: datetime
    lower_time: datetime
    upper_time: datetime
    wrapped: bool


class SimBenchProfileDriver:
    """Applies deterministic SimBench absolute values to one pandapower network."""

    def __init__(
        self,
        network_path: Path,
        grid_id: str,
        settings: GridSettings,
    ) -> None:
        try:
            self.net = pp.from_json(str(network_path))
            self.absolute_values = sb.get_absolute_values(
                self.net,
                profiles_instead_of_study_cases=True,
            )
        except Exception as exc:
            raise GridPackageError(
                f"无法加载SimBench曲线：{type(exc).__name__}: {exc}"
            ) from exc
        self.settings = settings
        self.grid_id = grid_id
        self.profile_start_time = _as_utc(settings.profile_start_time)
        self.time_step_count = _time_step_count(self.absolute_values)
        if self.time_step_count <= 0:
            raise GridPackageError("活动电网包没有可用的SimBench时间曲线")
        self.profile_end_time = self.profile_start_time + timedelta(
            seconds=(
                (self.time_step_count - 1)
                * settings.profile_interval_seconds
            )
        )
        self._validate_dynamic_tables()

    def apply(
        self,
        simulation_time: datetime,
        strategy: ProfileStrategy,
    ) -> dict[str, Any]:
        position = self.resolve_position(simulation_time, strategy)
        applied: dict[str, dict[str, Any]] = {}
        for (table_name, column_name), values in self.absolute_values.items():
            if not isinstance(values, pd.DataFrame) or values.empty:
                continue
            if table_name not in {"load", "sgen", "gen", "storage"}:
                continue
            current = _interpolate(values, position)
            table = getattr(self.net, table_name)
            if len(current) != len(table):
                raise GridPackageError(
                    f"曲线列数与{table_name}设备数量不一致"
                )
            if len(table):
                table.loc[:, column_name] = current
            applied[f"{table_name}.{column_name}"] = {
                "deviceCount": len(current),
                "unit": _unit_for(column_name),
            }

        return {
            "strategy": strategy,
            "profileSourceTime": position.source_time.isoformat(),
            "lowerSourceTime": position.lower_time.isoformat(),
            "upperSourceTime": position.upper_time.isoformat(),
            "lowerIndex": position.lower_index,
            "upperIndex": position.upper_index,
            "fraction": position.fraction,
            "wrapped": position.wrapped,
            "profileStartTime": self.profile_start_time.isoformat(),
            "profileEndTime": self.profile_end_time.isoformat(),
            "profileIntervalSeconds": self.settings.profile_interval_seconds,
            "endBehavior": self.settings.profile_end_behavior,
            "appliedInputs": applied,
        }

    def resolve_position(
        self,
        simulation_time: datetime,
        strategy: ProfileStrategy,
    ) -> ProfilePosition:
        current = _as_utc(simulation_time)
        raw_position = (
            (current - self.profile_start_time).total_seconds()
            / self.settings.profile_interval_seconds
        )
        if raw_position < 0:
            raise GridPackageError(
                "simulationTime早于配置的SimBench曲线起始时间"
            )

        wrapped = False
        if raw_position > self.time_step_count - 1:
            if self.settings.profile_end_behavior == "stop":
                raise GridPackageError("simulationTime已超出SimBench曲线末尾")
            raw_position %= self.time_step_count
            wrapped = True

        lower = math.floor(raw_position)
        fraction = raw_position - lower
        if strategy == "hold" or fraction == 0:
            upper = lower
            fraction = 0.0
        else:
            upper = lower + 1
            if upper >= self.time_step_count:
                if self.settings.profile_end_behavior == "wrap":
                    upper = 0
                    wrapped = True
                else:
                    upper = lower
                    fraction = 0.0

        lower_time = self.profile_start_time + timedelta(
            seconds=lower * self.settings.profile_interval_seconds
        )
        upper_time = self.profile_start_time + timedelta(
            seconds=upper * self.settings.profile_interval_seconds
        )
        source_time = lower_time + timedelta(
            seconds=(
                fraction * self.settings.profile_interval_seconds
            )
        )
        return ProfilePosition(
            lower_index=lower,
            upper_index=upper,
            fraction=fraction,
            source_time=source_time,
            lower_time=lower_time,
            upper_time=upper_time,
            wrapped=wrapped,
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "profileStartTime": self.profile_start_time.isoformat(),
            "profileEndTime": self.profile_end_time.isoformat(),
            "profileIntervalSeconds": self.settings.profile_interval_seconds,
            "timeSteps": self.time_step_count,
            "strategies": ["hold", "linear"],
            "configuredStrategy": self.settings.interpolation_strategy,
            "endBehavior": self.settings.profile_end_behavior,
            "dynamicInputs": {
                f"{table}.{column}": {
                    "shape": list(values.shape),
                    "unit": _unit_for(column),
                }
                for (table, column), values in self.absolute_values.items()
                if isinstance(values, pd.DataFrame) and not values.empty
            },
            "deviceProfiles": {
                _business_id(self.grid_id, table_name, index): {
                    "elementType": table_name,
                    "sourceIndex": _source_index(index),
                    "profile": str(row["profile"]),
                    "inputFields": [
                        column
                        for table, column in self.absolute_values
                        if table == table_name
                        and not self.absolute_values[(table, column)].empty
                    ],
                    "units": {
                        column: _unit_for(column)
                        for table, column in self.absolute_values
                        if table == table_name
                        and not self.absolute_values[(table, column)].empty
                    },
                }
                for table_name in ("load", "sgen", "gen", "storage")
                for index, row in getattr(self.net, table_name).iterrows()
            },
        }

    def _validate_dynamic_tables(self) -> None:
        required = {
            ("load", "p_mw"): len(self.net.load),
            ("load", "q_mvar"): len(self.net.load),
            ("sgen", "p_mw"): len(self.net.sgen),
        }
        for key, expected_columns in required.items():
            values = self.absolute_values.get(key)
            if (
                not isinstance(values, pd.DataFrame)
                or values.shape
                != (self.time_step_count, expected_columns)
            ):
                raise GridPackageError(
                    f"SimBench绝对值曲线不完整：{key[0]}.{key[1]}"
                )
            numeric = values.to_numpy(dtype=float)
            if not np.isfinite(numeric).all():
                raise GridPackageError(
                    f"SimBench绝对值曲线包含非法数值：{key[0]}.{key[1]}"
                )
        for table_name in ("load", "sgen", "gen", "storage"):
            table = getattr(self.net, table_name)
            if table.empty:
                continue
            if "profile" not in table or table["profile"].isna().any():
                raise GridPackageError(
                    f"{table_name}存在没有曲线且未配置固定值回退的设备"
                )
            values = self.absolute_values.get((table_name, "p_mw"))
            if (
                not isinstance(values, pd.DataFrame)
                or values.shape
                != (self.time_step_count, len(table))
            ):
                raise GridPackageError(
                    f"{table_name}没有完整的有功曲线，禁止静默回退"
                )


def _interpolate(
    values: pd.DataFrame,
    position: ProfilePosition,
) -> np.ndarray:
    lower = values.iloc[position.lower_index].to_numpy(dtype=float)
    if position.upper_index == position.lower_index:
        return lower
    upper = values.iloc[position.upper_index].to_numpy(dtype=float)
    return lower + (upper - lower) * position.fraction


def _time_step_count(values: dict[tuple[str, str], pd.DataFrame]) -> int:
    lengths = {
        len(table)
        for table in values.values()
        if isinstance(table, pd.DataFrame) and not table.empty
    }
    if len(lengths) != 1:
        raise GridPackageError(
            f"SimBench曲线时间长度不一致：{sorted(lengths)}"
        )
    return next(iter(lengths), 0)


def _unit_for(column_name: str) -> str:
    if column_name == "p_mw":
        return "MW"
    if column_name == "q_mvar":
        return "Mvar"
    return ""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _business_id(grid_id: str, element_type: str, index: Any) -> str:
    return f"{grid_id}:{element_type}:{_source_index(index)}"


def _source_index(index: Any) -> int | float | str:
    if isinstance(index, (np.integer, int)):
        return int(index)
    if isinstance(index, (np.floating, float)) and float(index).is_integer():
        return int(index)
    return str(index)
