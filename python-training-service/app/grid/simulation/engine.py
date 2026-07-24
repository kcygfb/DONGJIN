from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandapower as pp

from app.grid.artifact_service import (
    GridPackageError,
    resolve_active_grid_package,
)
from app.grid.publishers.neo4j import assert_active_neo4j_grid
from app.grid.publishers.redis_snapshot import (
    RedisSnapshotError,
    RedisSnapshotPublisher,
)
from app.grid.settings import GridSettings, get_grid_settings
from app.grid.simulation.models import (
    BranchMeasurement,
    BusMeasurement,
    ExternalGridMeasurement,
    GridSnapshot,
    PowerMeasurement,
    SwitchMeasurement,
    TransformerMeasurement,
)
from app.grid.simulation.profiles import (
    ProfileStrategy,
    SimBenchProfileDriver,
)


class SimulationStateError(RuntimeError):
    pass


class GridSimulationEngine:
    def __init__(self, settings: GridSettings | None = None) -> None:
        self.settings = settings or get_grid_settings()
        self.publisher = RedisSnapshotPublisher(self.settings)
        self._lock = threading.RLock()
        self._run_gate = threading.Event()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._profile_driver: SimBenchProfileDriver | None = None
        self._manifest: dict[str, Any] | None = None
        self._state = "STOPPED"
        self._simulation_time: datetime | None = None
        self._speed_factor = 1.0
        self._profile_strategy: ProfileStrategy = (
            self.settings.interpolation_strategy
        )
        self._step = 0
        self._last_snapshot_id: str | None = None
        self._last_error: str | None = None
        self._last_performance: dict[str, float] = {}

    def start(
        self,
        *,
        start_time: datetime | None = None,
        speed_factor: float = 1.0,
        profile_strategy: ProfileStrategy | None = None,
    ) -> dict[str, Any]:
        if speed_factor <= 0:
            raise SimulationStateError("speedFactor必须大于0")
        with self._lock:
            if self._state in {"RUNNING", "PAUSED"}:
                raise SimulationStateError("仿真已经启动，不能创建第二个计算循环")

            manifest, artifact_dir = resolve_active_grid_package(
                self.settings
            )
            assert_active_neo4j_grid(
                manifest["gridId"],
                manifest["topologyVersion"],
                self.settings,
            )
            try:
                self.publisher.current()
            except RedisSnapshotError as exc:
                raise SimulationStateError(str(exc)) from exc

            driver = SimBenchProfileDriver(
                artifact_dir / "network.json",
                manifest["gridId"],
                self.settings,
            )
            strategy = profile_strategy or self.settings.interpolation_strategy
            initial_time = start_time or driver.profile_start_time
            driver.resolve_position(initial_time, strategy)

            self._profile_driver = driver
            self._manifest = manifest
            self._simulation_time = _as_utc(initial_time)
            self._speed_factor = speed_factor
            self._profile_strategy = strategy
            self._step = 0
            self._last_snapshot_id = None
            self._last_error = None
            self._last_performance = {}
            self._stop_event.clear()
            self._run_gate.set()
            self._state = "RUNNING"
            self._worker = threading.Thread(
                target=self._run_loop,
                name="dongjin-grid-simulation",
                daemon=True,
            )
            self._worker.start()
            status = self._status_unlocked()
        self._write_status_best_effort(status)
        return status

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self._state != "RUNNING":
                raise SimulationStateError("只有RUNNING状态可以暂停")
            self._state = "PAUSED"
            self._run_gate.clear()
            status = self._status_unlocked()
        self._write_status_best_effort(status)
        return status

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if self._state != "PAUSED":
                raise SimulationStateError("只有PAUSED状态可以继续")
            self._state = "RUNNING"
            self._run_gate.set()
            status = self._status_unlocked()
        self._write_status_best_effort(status)
        return status

    def stop(self) -> dict[str, Any]:
        with self._lock:
            worker = self._worker
            self._stop_event.set()
            self._run_gate.set()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=min(self.settings.simulation_interval_seconds + 1, 5))
        with self._lock:
            self._state = "STOPPED"
            self._worker = None
            status = self._status_unlocked()
        self._write_status_best_effort(status)
        return status

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_unlocked()

    def profile_metadata(self) -> dict[str, Any]:
        with self._lock:
            driver = self._profile_driver
        if driver is not None:
            return driver.metadata()
        manifest, artifact_dir = resolve_active_grid_package(self.settings)
        return SimBenchProfileDriver(
            artifact_dir / "network.json",
            manifest["gridId"],
            self.settings,
        ).metadata()

    def current_snapshot(self) -> dict[str, Any] | None:
        return self.publisher.current()

    def shutdown(self) -> None:
        with self._lock:
            active = self._state in {"RUNNING", "PAUSED"}
        if active:
            self.stop()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._run_gate.wait(timeout=0.25):
                continue
            if self._stop_event.is_set():
                break
            cycle_started = time.perf_counter()
            try:
                snapshot = self._calculate_snapshot()
                _, publishing_performance = self.publisher.publish(snapshot)
                with self._lock:
                    self._step += 1
                    self._last_snapshot_id = snapshot.snapshot_id
                    self._last_performance.update(publishing_performance)
                    self._simulation_time = (
                        _require(self._simulation_time)
                        + timedelta(
                            seconds=(
                                self.settings.simulation_interval_seconds
                                * self._speed_factor
                            )
                        )
                    )
                    status = self._status_unlocked()
                self._write_status_best_effort(status)
            except Exception as exc:
                with self._lock:
                    self._state = "ERROR"
                    self._last_error = (
                        f"{type(exc).__name__}: {str(exc).strip()}"
                    )
                    self._run_gate.clear()
                    status = self._status_unlocked()
                self._write_status_best_effort(status)
                return

            elapsed = time.perf_counter() - cycle_started
            remaining = self.settings.simulation_interval_seconds - elapsed
            if remaining > 0 and self._stop_event.wait(remaining):
                break

        with self._lock:
            if self._state != "ERROR":
                self._state = "STOPPED"
            self._worker = None

    def _calculate_snapshot(self) -> GridSnapshot:
        with self._lock:
            driver = _require(self._profile_driver)
            manifest = _require(self._manifest)
            simulation_time = _require(self._simulation_time)
            strategy = self._profile_strategy
            next_step = self._step + 1

        input_started = time.perf_counter()
        provenance = driver.apply(simulation_time, strategy)
        input_ms = (time.perf_counter() - input_started) * 1000

        power_flow_started = time.perf_counter()
        pp.runpp(driver.net, numba=False)
        power_flow_ms = (time.perf_counter() - power_flow_started) * 1000
        if not bool(driver.net.converged):
            raise GridPackageError("本次连续潮流计算未收敛")

        snapshot_started = time.perf_counter()
        snapshot_id = _snapshot_id(next_step)
        snapshot = _collect_snapshot(
            driver.net,
            manifest,
            snapshot_id,
            simulation_time,
            provenance,
            strategy,
            input_ms,
            power_flow_ms,
        )
        snapshot_ms = (time.perf_counter() - snapshot_started) * 1000
        snapshot.performance["snapshotBuildDurationMs"] = round(
            snapshot_ms, 3
        )
        with self._lock:
            self._last_performance = dict(snapshot.performance)
        return snapshot

    def _status_unlocked(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "gridId": self._manifest["gridId"] if self._manifest else None,
            "topologyVersion": (
                self._manifest["topologyVersion"]
                if self._manifest
                else None
            ),
            "step": self._step,
            "simulationTime": (
                self._simulation_time.isoformat()
                if self._simulation_time
                else None
            ),
            "calculationIntervalSeconds": (
                self.settings.simulation_interval_seconds
            ),
            "speedFactor": self._speed_factor,
            "profileStrategy": self._profile_strategy,
            "lastSnapshotId": self._last_snapshot_id,
            "lastPerformance": self._last_performance,
            "lastError": self._last_error,
            "workerAlive": bool(
                self._worker and self._worker.is_alive()
            ),
        }

    def _write_status_best_effort(self, status: dict[str, Any]) -> None:
        try:
            self.publisher.write_simulation_status(status)
        except RedisSnapshotError:
            if status["state"] in {"RUNNING", "PAUSED"}:
                raise


def _collect_snapshot(
    net: Any,
    manifest: dict[str, Any],
    snapshot_id: str,
    simulation_time: datetime,
    provenance: dict[str, Any],
    strategy: str,
    input_ms: float,
    power_flow_ms: float,
) -> GridSnapshot:
    buses = {
        _business_id(manifest["gridId"], "bus", index): BusMeasurement(
            vmPu=_float(row, "vm_pu"),
            vaDegree=_float(row, "va_degree"),
            pMw=_float(row, "p_mw"),
            qMvar=_float(row, "q_mvar"),
        )
        for index, row in net.res_bus.iterrows()
    }
    lines = {
        _business_id(manifest["gridId"], "line", index): BranchMeasurement(
            pFromMw=_float(row, "p_from_mw"),
            qFromMvar=_float(row, "q_from_mvar"),
            pToMw=_float(row, "p_to_mw"),
            qToMvar=_float(row, "q_to_mvar"),
            iFromKa=_float(row, "i_from_ka"),
            iToKa=_float(row, "i_to_ka"),
            loadingPercent=_float(row, "loading_percent"),
            plMw=_float(row, "pl_mw"),
            qlMvar=_float(row, "ql_mvar"),
        )
        for index, row in net.res_line.iterrows()
    }
    transformers = {
        _business_id(
            manifest["gridId"], "trafo", index
        ): TransformerMeasurement(
            pHvMw=_float(row, "p_hv_mw"),
            qHvMvar=_float(row, "q_hv_mvar"),
            pLvMw=_float(row, "p_lv_mw"),
            qLvMvar=_float(row, "q_lv_mvar"),
            iHvKa=_float(row, "i_hv_ka"),
            iLvKa=_float(row, "i_lv_ka"),
            loadingPercent=_float(row, "loading_percent"),
            plMw=_float(row, "pl_mw"),
            qlMvar=_float(row, "ql_mvar"),
            tapPosition=_optional_float(net.trafo.loc[index], "tap_pos"),
        )
        for index, row in net.res_trafo.iterrows()
    }
    switches = {
        _business_id(
            manifest["gridId"], "switch", index
        ): SwitchMeasurement(
            closed=bool(row.get("closed", True)),
            inService=bool(row.get("in_service", True)),
        )
        for index, row in net.switch.iterrows()
    }
    loads = _input_measurements(net.load, manifest["gridId"], "load")
    generators = {
        **_input_measurements(net.sgen, manifest["gridId"], "sgen"),
        **_input_measurements(net.gen, manifest["gridId"], "gen"),
    }
    external_grids = {
        _business_id(
            manifest["gridId"], "ext_grid", index
        ): ExternalGridMeasurement(
            pMw=_float(row, "p_mw"),
            qMvar=_float(row, "q_mvar"),
            busBusinessId=_business_id(
                manifest["gridId"],
                "bus",
                net.ext_grid.loc[index, "bus"],
            ),
        )
        for index, row in net.res_ext_grid.iterrows()
    }
    return GridSnapshot(
        snapshotId=snapshot_id,
        gridId=manifest["gridId"],
        topologyVersion=manifest["topologyVersion"],
        simulationTime=simulation_time.isoformat(),
        profileSourceTime=provenance["profileSourceTime"],
        publishedAt=datetime.now(UTC).isoformat(),
        converged=True,
        calculationDurationMs=round(power_flow_ms, 3),
        profileStrategy=strategy,
        profileProvenance=provenance,
        performance={
            "inputPreparationDurationMs": round(input_ms, 3),
            "powerFlowDurationMs": round(power_flow_ms, 3),
            "snapshotBuildDurationMs": 0.0,
            "serializationDurationMs": 0.0,
        },
        buses=buses,
        lines=lines,
        transformers=transformers,
        switches=switches,
        loads=loads,
        generators=generators,
        externalGrids=external_grids,
    )


def _input_measurements(
    table: Any,
    grid_id: str,
    element_type: str,
) -> dict[str, PowerMeasurement]:
    result: dict[str, PowerMeasurement] = {}
    for index, row in table.iterrows():
        scaling = float(row.get("scaling", 1.0))
        in_service = bool(row.get("in_service", True))
        factor = scaling if in_service else 0.0
        result[_business_id(grid_id, element_type, index)] = (
            PowerMeasurement(
                pMw=float(row.get("p_mw", 0.0)) * factor,
                qMvar=float(row.get("q_mvar", 0.0)) * factor,
            )
        )
    return result


def _float(row: Any, column: str) -> float:
    value = float(row.get(column, 0.0))
    if not np.isfinite(value):
        raise GridPackageError(f"潮流结果包含非法数值：{column}")
    return value


def _optional_float(row: Any, column: str) -> float | None:
    value = row.get(column)
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    result = float(value)
    if not np.isfinite(result):
        raise GridPackageError(f"设备状态包含非法数值：{column}")
    return result


def _business_id(grid_id: str, element_type: str, index: Any) -> str:
    if isinstance(index, (np.integer, int)):
        normalized = str(int(index))
    elif isinstance(index, (np.floating, float)) and float(index).is_integer():
        normalized = str(int(index))
    else:
        normalized = str(index)
    return f"{grid_id}:{element_type}:{normalized}"


def _snapshot_id(step: int) -> str:
    now = datetime.now(UTC)
    return f"{now:%Y%m%dT%H%M%S}.{now.microsecond // 1000:03d}Z-{step:06d}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require(value: Any) -> Any:
    if value is None:
        raise SimulationStateError("仿真内部状态尚未初始化")
    return value


_ENGINE: GridSimulationEngine | None = None
_ENGINE_LOCK = threading.Lock()


def get_simulation_engine() -> GridSimulationEngine:
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = GridSimulationEngine()
    return _ENGINE
