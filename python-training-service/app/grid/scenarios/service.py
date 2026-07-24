from __future__ import annotations

import copy
import json
import math
import os
import shutil
import tempfile
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandapower as pp

from app.grid.artifact_service import resolve_active_grid_package
from app.grid.offline_io import (
    file_manifest,
    read_json,
    safe_child,
    write_csv,
    write_json,
    write_jsonl,
)
from app.grid.scenarios.models import (
    EventType,
    QualityCode,
    ScenarioBatchRequest,
    ScenarioDefinition,
    ScenarioEvent,
)
from app.grid.settings import GridSettings, get_grid_settings
from app.grid.simulation.profiles import SimBenchProfileDriver


SCENARIO_BATCH_SCHEMA = "grid-scenario-batch-v1"
TRUTH_SCHEMA = "grid-truth-v1"
MEASUREMENT_SCHEMA = "grid-measurement-v1"

ELEMENT_TYPES = (
    "bus",
    "line",
    "trafo",
    "switch",
    "load",
    "sgen",
    "gen",
    "ext_grid",
)

EVENT_TARGET_TYPES = {
    EventType.NORMAL: None,
    EventType.LINE_OUTAGE: "line",
    EventType.TRANSFORMER_OUTAGE: "trafo",
    EventType.SWITCH_MISOPERATION: "switch",
    EventType.LOAD_SURGE: "load",
    EventType.GENERATION_DROP: "sgen",
    EventType.MEASUREMENT_BIAS: "bus",
    EventType.MEASUREMENT_DROPOUT: "line",
    EventType.MEASUREMENT_FROZEN: "bus",
    EventType.MEASUREMENT_DRIFT: "bus",
    EventType.MEASUREMENT_DELAY: "bus",
    EventType.MEASUREMENT_QUANTIZATION: "bus",
    EventType.TAP_POSITION_ANOMALY: "trafo",
}


class ScenarioGenerationError(RuntimeError):
    pass


def generate_scenario_batch(
    request: ScenarioBatchRequest,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    manifest, artifact_dir = resolve_active_grid_package(settings)
    root = settings.resolved_scenario_dir
    root.mkdir(parents=True, exist_ok=True)
    batch_id = request.batch_id or (
        f"scenario-batch-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    try:
        final_dir = safe_child(root, batch_id)
    except ValueError as exc:
        raise ScenarioGenerationError(str(exc)) from exc
    if final_dir.exists():
        raise ScenarioGenerationError(f"场景批次已存在：{batch_id}")

    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{batch_id}-", dir=root)
    )
    started = time.perf_counter()
    try:
        driver = SimBenchProfileDriver(
            artifact_dir / "network.json",
            manifest["gridId"],
            settings,
        )
        rng = np.random.default_rng(request.random_seed)
        index_rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        type_counts: Counter[str] = Counter()
        run_number = 0

        for event_type in request.event_types:
            for type_index in range(request.samples_per_type):
                run_number += 1
                simulation_time = request.start_time + timedelta(
                    seconds=(
                        (run_number - 1) * request.time_step_seconds
                    )
                )
                scenario_seed = int(
                    request.random_seed
                    + run_number * 1009
                    + list(EventType).index(event_type) * 100_003
                )
                scenario_rng = np.random.default_rng(scenario_seed)
                try:
                    row = _generate_one(
                        temporary_dir,
                        batch_id,
                        run_number,
                        type_index,
                        event_type,
                        simulation_time,
                        scenario_seed,
                        scenario_rng,
                        driver,
                        manifest,
                        request.measurement_noise_relative_std,
                    )
                    index_rows.append(row)
                    type_counts[event_type.value] += 1
                except Exception as exc:
                    errors.append(
                        {
                            "eventType": event_type.value,
                            "typeIndex": type_index,
                            "randomSeed": scenario_seed,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        expected = len(request.event_types) * request.samples_per_type
        if len(index_rows) != expected:
            raise ScenarioGenerationError(
                f"场景生成不完整：成功{len(index_rows)}/{expected}；"
                f"首个错误={errors[0] if errors else 'unknown'}"
            )

        write_csv(
            temporary_dir / "scenario-index.csv",
            index_rows,
            fieldnames=[
                "scenarioRunId",
                "scenarioId",
                "eventType",
                "targetBusinessId",
                "simulationTime",
                "randomSeed",
                "converged",
                "objectCount",
                "measurementCount",
                "artifactPath",
            ],
        )
        write_json(
            temporary_dir / "field-catalog.json",
            _field_catalog(),
        )
        write_json(
            temporary_dir / "request.json",
            request.model_dump(by_alias=True, mode="json"),
        )
        readme = _batch_readme(
            batch_id,
            manifest,
            len(index_rows),
            type_counts,
        )
        (temporary_dir / "README.md").write_text(
            readme,
            encoding="utf-8",
        )
        batch_manifest = {
            "batchId": batch_id,
            "schemaVersion": SCENARIO_BATCH_SCHEMA,
            "gridId": manifest["gridId"],
            "topologyVersion": manifest["topologyVersion"],
            "sourceGridSchemaVersion": manifest["schemaVersion"],
            "createdAt": datetime.now(UTC).isoformat(),
            "randomSeed": request.random_seed,
            "requestedEventTypes": [
                value.value for value in request.event_types
            ],
            "samplesPerType": request.samples_per_type,
            "scenarioCount": len(index_rows),
            "eventDistribution": dict(sorted(type_counts.items())),
            "failedScenarioCount": len(errors),
            "errors": errors,
            "durationSeconds": round(time.perf_counter() - started, 3),
            "whiteBoxFiles": {
                "index": "scenario-index.csv",
                "fieldCatalog": "field-catalog.json",
                "request": "request.json",
                "readme": "README.md",
                "perScenario": [
                    "scenario.json",
                    "truth/baseline.json",
                    "truth/event.json",
                    "measurements/frame.json",
                    "measurements/transform-audit.jsonl",
                    "labels.json",
                    "summary.md",
                    "validation.json",
                ],
            },
            "files": file_manifest(temporary_dir),
        }
        write_json(temporary_dir / "manifest.json", batch_manifest)
        os.replace(temporary_dir, final_dir)
        return _batch_response(batch_manifest, final_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def list_scenario_batches(
    settings: GridSettings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_grid_settings()
    root = settings.resolved_scenario_dir
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.iterdir(), reverse=True):
        manifest_path = path / "manifest.json"
        if manifest_path.is_file():
            result.append(_batch_response(read_json(manifest_path), path))
    return result


def get_scenario_batch(
    batch_id: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    try:
        path = safe_child(settings.resolved_scenario_dir, batch_id)
    except ValueError as exc:
        raise ScenarioGenerationError(str(exc)) from exc
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise ScenarioGenerationError(f"场景批次不存在：{batch_id}")
    return _batch_response(read_json(manifest_path), path)


def _generate_one(
    batch_dir: Path,
    batch_id: str,
    run_number: int,
    type_index: int,
    event_type: EventType,
    simulation_time: datetime,
    scenario_seed: int,
    rng: np.random.Generator,
    driver: SimBenchProfileDriver,
    grid_manifest: dict[str, Any],
    noise_relative_std: float,
    requested_target_business_id: str | None = None,
) -> dict[str, Any]:
    provenance = driver.apply(simulation_time, "linear")
    baseline_net = copy.deepcopy(driver.net)
    baseline_converged, baseline_error = _run_power_flow(baseline_net)
    baseline_truth = _build_truth(
        baseline_net,
        grid_manifest,
        simulation_time,
        event_type=EventType.NORMAL,
        target_business_id=None,
        converged=baseline_converged,
        calculation_error=baseline_error,
        phase="BASELINE",
        profile_provenance=provenance,
    )
    if not baseline_converged:
        raise ScenarioGenerationError(
            f"基线潮流未收敛：{baseline_error}"
        )

    scenario_net = copy.deepcopy(baseline_net)
    target_type = EVENT_TARGET_TYPES[event_type]
    target_business_id, parameters = _select_and_apply_event(
        scenario_net,
        grid_manifest["gridId"],
        event_type,
        target_type,
        rng,
        requested_target_business_id,
    )
    converged, calculation_error = _run_power_flow(scenario_net)
    event_truth = _build_truth(
        scenario_net,
        grid_manifest,
        simulation_time,
        event_type=event_type,
        target_business_id=target_business_id,
        converged=converged,
        calculation_error=calculation_error,
        phase="EVENT",
        profile_provenance=provenance,
    )
    scenario_id = (
        f"scenario-{event_type.value.lower().replace('_', '-')}-"
        f"{type_index + 1:04d}"
    )
    run_id = f"{batch_id}-run-{run_number:06d}"
    event = ScenarioEvent(
        eventId=f"{run_id}-event-001",
        eventType=event_type,
        targetBusinessId=target_business_id,
        parameters=parameters,
    )
    definition = ScenarioDefinition(
        scenarioId=scenario_id,
        scenarioRunId=run_id,
        gridId=grid_manifest["gridId"],
        topologyVersion=grid_manifest["topologyVersion"],
        simulationTime=simulation_time,
        randomSeed=scenario_seed,
        event=event,
    )
    measurement, audit_rows = _build_measurement(
        event_truth,
        baseline_truth,
        definition,
        rng,
        noise_relative_std,
    )
    labels = _build_labels(event_truth, definition)
    validation = _validate_run(
        definition,
        baseline_truth,
        event_truth,
        measurement,
        labels,
    )

    run_dir = batch_dir / "runs" / run_id
    write_json(
        run_dir / "scenario.json",
        definition.model_dump(by_alias=True, mode="json"),
    )
    write_json(run_dir / "truth" / "baseline.json", baseline_truth)
    write_json(run_dir / "truth" / "event.json", event_truth)
    write_json(run_dir / "measurements" / "frame.json", measurement)
    write_jsonl(
        run_dir / "measurements" / "transform-audit.jsonl",
        audit_rows,
    )
    write_json(run_dir / "labels.json", labels)
    write_json(run_dir / "validation.json", validation)
    (run_dir / "summary.md").write_text(
        _run_summary(definition, event_truth, measurement, labels),
        encoding="utf-8",
    )
    return {
        "scenarioRunId": run_id,
        "scenarioId": scenario_id,
        "eventType": event_type.value,
        "targetBusinessId": target_business_id or "",
        "simulationTime": simulation_time.isoformat(),
        "randomSeed": scenario_seed,
        "converged": converged,
        "objectCount": len(event_truth["objects"]),
        "measurementCount": len(measurement["objects"]),
        "artifactPath": f"runs/{run_id}",
    }


def _select_and_apply_event(
    net: Any,
    grid_id: str,
    event_type: EventType,
    target_type: str | None,
    rng: np.random.Generator,
    requested_target_business_id: str | None = None,
) -> tuple[str | None, dict[str, Any]]:
    if event_type == EventType.NORMAL:
        return None, {}
    if target_type is None:
        raise ScenarioGenerationError(
            f"事件缺少目标类型：{event_type.value}"
        )
    table = getattr(net, target_type)
    if table.empty:
        raise ScenarioGenerationError(
            f"电网没有可用目标：{target_type}"
        )
    candidates = list(table.index)
    if "in_service" in table:
        candidates = [
            index
            for index in candidates
            if bool(table.at[index, "in_service"])
        ]
    if event_type == EventType.SWITCH_MISOPERATION:
        candidates = [
            index
            for index in candidates
            if str(table.at[index, "et"]) in {"l", "t", "b"}
        ]
    if not candidates:
        raise ScenarioGenerationError(
            f"没有在运目标：{target_type}"
        )
    if requested_target_business_id:
        prefix = f"{grid_id}:{target_type}:"
        if not requested_target_business_id.startswith(prefix):
            raise ScenarioGenerationError(
                "指定目标与事件要求的设备类型不匹配"
            )
        source_text = requested_target_business_id.removeprefix(prefix)
        matching = [
            index for index in candidates if str(index) == source_text
        ]
        if not matching:
            raise ScenarioGenerationError(
                f"指定目标不存在或当前不可用：{requested_target_business_id}"
            )
        index = matching[0]
    else:
        index = candidates[int(rng.integers(0, len(candidates)))]
    target = _business_id(grid_id, target_type, index)
    parameters: dict[str, Any] = {}
    if event_type in {
        EventType.LINE_OUTAGE,
        EventType.TRANSFORMER_OUTAGE,
    }:
        table.at[index, "in_service"] = False
        parameters = {
            "previousInService": True,
            "newInService": False,
        }
    elif event_type == EventType.SWITCH_MISOPERATION:
        previous = bool(table.at[index, "closed"])
        table.at[index, "closed"] = not previous
        parameters = {
            "previousClosed": previous,
            "newClosed": not previous,
        }
    elif event_type == EventType.LOAD_SURGE:
        factor = round(float(rng.uniform(1.35, 1.9)), 6)
        table.at[index, "p_mw"] = float(table.at[index, "p_mw"]) * factor
        table.at[index, "q_mvar"] = (
            float(table.at[index, "q_mvar"]) * factor
        )
        parameters = {"scalingFactor": factor}
    elif event_type == EventType.GENERATION_DROP:
        factor = round(float(rng.uniform(0.05, 0.35)), 6)
        table.at[index, "p_mw"] = float(table.at[index, "p_mw"]) * factor
        if "q_mvar" in table:
            table.at[index, "q_mvar"] = (
                float(table.at[index, "q_mvar"]) * factor
            )
        parameters = {"remainingFactor": factor}
    elif event_type == EventType.MEASUREMENT_BIAS:
        parameters = {
            "relativeBias": round(float(rng.uniform(0.03, 0.08)), 6)
        }
    elif event_type == EventType.MEASUREMENT_DROPOUT:
        parameters = {"dropAllDynamicFields": True}
    elif event_type == EventType.MEASUREMENT_FROZEN:
        parameters = {"frozenAt": "BASELINE"}
    elif event_type == EventType.MEASUREMENT_DRIFT:
        parameters = {
            "relativeDrift": round(float(rng.uniform(0.02, 0.08)), 6)
        }
    elif event_type == EventType.MEASUREMENT_DELAY:
        parameters = {"delayedTo": "BASELINE"}
    elif event_type == EventType.MEASUREMENT_QUANTIZATION:
        parameters = {"decimalPlaces": 2}
    elif event_type == EventType.TAP_POSITION_ANOMALY:
        previous = table.at[index, "tap_pos"]
        minimum = table.at[index, "tap_min"]
        maximum = table.at[index, "tap_max"]
        if previous < maximum:
            current = previous + 1
        elif previous > minimum:
            current = previous - 1
        else:
            raise ScenarioGenerationError("指定变压器没有可用分接头档位")
        table.at[index, "tap_pos"] = current
        parameters = {
            "previousTapPosition": float(previous),
            "newTapPosition": float(current),
        }
    return target, parameters


def _run_power_flow(net: Any) -> tuple[bool, str | None]:
    try:
        pp.reset_results(net)
        pp.runpp(
            net,
            numba=False,
            check_connectivity=True,
            init="auto",
        )
        return bool(net.converged), None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _build_truth(
    net: Any,
    grid_manifest: dict[str, Any],
    simulation_time: datetime,
    *,
    event_type: EventType,
    target_business_id: str | None,
    converged: bool,
    calculation_error: str | None,
    phase: str,
    profile_provenance: dict[str, Any],
) -> dict[str, Any]:
    objects: dict[str, dict[str, Any]] = {}
    _add_result_objects(
        objects,
        grid_manifest["gridId"],
        "bus",
        net.bus,
        net.res_bus,
        {
            "vm_pu": "vmPu",
            "va_degree": "vaDegree",
            "p_mw": "pMw",
            "q_mvar": "qMvar",
        },
    )
    _add_result_objects(
        objects,
        grid_manifest["gridId"],
        "line",
        net.line,
        net.res_line,
        {
            "p_from_mw": "pFromMw",
            "q_from_mvar": "qFromMvar",
            "p_to_mw": "pToMw",
            "q_to_mvar": "qToMvar",
            "i_from_ka": "iFromKa",
            "i_to_ka": "iToKa",
            "loading_percent": "loadingPercent",
            "pl_mw": "lossMw",
        },
    )
    _add_result_objects(
        objects,
        grid_manifest["gridId"],
        "trafo",
        net.trafo,
        net.res_trafo,
        {
            "p_hv_mw": "pHvMw",
            "q_hv_mvar": "qHvMvar",
            "p_lv_mw": "pLvMw",
            "q_lv_mvar": "qLvMvar",
            "i_hv_ka": "iHvKa",
            "i_lv_ka": "iLvKa",
            "loading_percent": "loadingPercent",
            "pl_mw": "lossMw",
        },
    )
    for element_type in ("switch", "load", "sgen", "gen"):
        table = getattr(net, element_type)
        for index, row in table.iterrows():
            values: dict[str, Any] = {
                "inService": bool(row.get("in_service", True)),
            }
            if element_type == "switch":
                values["closed"] = bool(row.get("closed", True))
            else:
                values["pMw"] = _finite_or_none(row.get("p_mw"))
                values["qMvar"] = _finite_or_none(row.get("q_mvar"))
                values["scaling"] = _finite_or_none(
                    row.get("scaling", 1.0)
                )
            business_id = _business_id(
                grid_manifest["gridId"],
                element_type,
                index,
            )
            objects[business_id] = {
                "businessId": business_id,
                "elementType": element_type,
                "sourceIndex": _source_index(index),
                "values": values,
            }
    _add_result_objects(
        objects,
        grid_manifest["gridId"],
        "ext_grid",
        net.ext_grid,
        net.res_ext_grid,
        {
            "p_mw": "pMw",
            "q_mvar": "qMvar",
        },
    )
    impacts = _derive_impacts(objects)
    return {
        "truthSchemaVersion": TRUTH_SCHEMA,
        "frameId": (
            f"truth-{phase.lower()}-"
            f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
        ),
        "gridId": grid_manifest["gridId"],
        "topologyVersion": grid_manifest["topologyVersion"],
        "phase": phase,
        "simulationTime": simulation_time.isoformat(),
        "generatedAt": datetime.now(UTC).isoformat(),
        "converged": converged,
        "calculationError": calculation_error,
        "rootCause": {
            "eventType": event_type.value,
            "targetBusinessId": target_business_id,
        },
        "profileProvenance": profile_provenance,
        "impacts": impacts,
        "objects": objects,
    }


def _add_result_objects(
    objects: dict[str, dict[str, Any]],
    grid_id: str,
    element_type: str,
    input_table: Any,
    result_table: Any,
    columns: dict[str, str],
) -> None:
    for index, row in input_table.iterrows():
        values = {
            target: _finite_or_none(
                result_table.at[index, source]
                if index in result_table.index
                and source in result_table.columns
                else None
            )
            for source, target in columns.items()
        }
        values["inService"] = bool(row.get("in_service", True))
        if element_type == "trafo":
            values["tapPosition"] = _finite_or_none(
                row.get("tap_pos")
            )
        business_id = _business_id(grid_id, element_type, index)
        objects[business_id] = {
            "businessId": business_id,
            "elementType": element_type,
            "sourceIndex": _source_index(index),
            "values": values,
        }


def _derive_impacts(
    objects: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    undervoltage: list[str] = []
    overvoltage: list[str] = []
    overloaded: list[str] = []
    unavailable: list[str] = []
    for business_id, item in objects.items():
        values = item["values"]
        vm_pu = values.get("vmPu")
        loading = values.get("loadingPercent")
        if isinstance(vm_pu, (int, float)):
            if vm_pu < 0.95:
                undervoltage.append(business_id)
            if vm_pu > 1.05:
                overvoltage.append(business_id)
        if isinstance(loading, (int, float)) and loading > 100:
            overloaded.append(business_id)
        if not values.get("inService", True) or (
            item["elementType"] == "bus" and vm_pu is None
        ):
            unavailable.append(business_id)
    return {
        "undervoltageBusinessIds": undervoltage,
        "overvoltageBusinessIds": overvoltage,
        "overloadedBusinessIds": overloaded,
        "unavailableBusinessIds": unavailable,
    }


def _build_measurement(
    truth: dict[str, Any],
    baseline: dict[str, Any],
    definition: ScenarioDefinition,
    rng: np.random.Generator,
    relative_noise_std: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    event_type = definition.event.event_type
    target = definition.event.target_business_id
    baseline_objects = baseline["objects"]
    measured_objects: dict[str, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    for business_id, truth_object in truth["objects"].items():
        quality = QualityCode.GOOD
        values: dict[str, Any] = {}
        source_values = truth_object["values"]
        for field, source_value in source_values.items():
            output_value = source_value
            operation = "IDENTITY"
            parameters: dict[str, Any] = {}
            if (
                isinstance(source_value, (int, float))
                and not isinstance(source_value, bool)
                and relative_noise_std > 0
            ):
                scale = max(abs(float(source_value)), 1e-6)
                noise = float(
                    rng.normal(0.0, scale * relative_noise_std)
                )
                output_value = float(source_value) + noise
                operation = "GAUSSIAN_NOISE"
                parameters = {
                    "relativeStd": relative_noise_std,
                    "noise": noise,
                }

            if business_id == target:
                if event_type == EventType.MEASUREMENT_BIAS and (
                    isinstance(source_value, (int, float))
                    and not isinstance(source_value, bool)
                ):
                    relative_bias = float(
                        definition.event.parameters["relativeBias"]
                    )
                    bias = max(abs(float(source_value)), 1e-3) * (
                        relative_bias
                    )
                    output_value = float(output_value) + bias
                    operation = "BIAS_AFTER_NOISE"
                    parameters = {
                        **parameters,
                        "relativeBias": relative_bias,
                        "bias": bias,
                    }
                    quality = QualityCode.SUSPECT
                elif event_type == EventType.MEASUREMENT_DROPOUT:
                    if (
                        isinstance(source_value, (int, float))
                        and not isinstance(source_value, bool)
                    ):
                        output_value = None
                        operation = "DROPOUT"
                        parameters = {"reason": "SIMULATED_DROPOUT"}
                        quality = QualityCode.MISSING
                elif event_type == EventType.MEASUREMENT_FROZEN:
                    baseline_value = (
                        baseline_objects.get(business_id, {})
                        .get("values", {})
                        .get(field)
                    )
                    output_value = baseline_value
                    operation = "FROZEN_TO_BASELINE"
                    parameters = {
                        "baselineFrameId": baseline["frameId"]
                    }
                    quality = QualityCode.FROZEN
                elif event_type == EventType.MEASUREMENT_DRIFT and (
                    isinstance(source_value, (int, float))
                    and not isinstance(source_value, bool)
                ):
                    relative_drift = float(
                        definition.event.parameters["relativeDrift"]
                    )
                    drift = max(abs(float(source_value)), 1e-3) * relative_drift
                    output_value = float(output_value) + drift
                    operation = "DRIFT_AFTER_NOISE"
                    parameters = {
                        **parameters,
                        "relativeDrift": relative_drift,
                        "drift": drift,
                    }
                    quality = QualityCode.SUSPECT
                elif event_type == EventType.MEASUREMENT_DELAY:
                    output_value = (
                        baseline_objects.get(business_id, {})
                        .get("values", {})
                        .get(field)
                    )
                    operation = "DELAYED_TO_BASELINE"
                    parameters = {
                        "baselineFrameId": baseline["frameId"]
                    }
                    quality = QualityCode.DELAYED
                elif event_type == EventType.MEASUREMENT_QUANTIZATION and (
                    isinstance(source_value, (int, float))
                    and not isinstance(source_value, bool)
                ):
                    decimal_places = int(
                        definition.event.parameters["decimalPlaces"]
                    )
                    output_value = round(float(output_value), decimal_places)
                    operation = "QUANTIZATION"
                    parameters = {"decimalPlaces": decimal_places}
                    quality = QualityCode.SUSPECT
            values[field] = output_value
            if operation != "IDENTITY":
                audit.append(
                    {
                        "scenarioRunId": definition.scenario_run_id,
                        "measurementFrameId": (
                            f"measurement-{definition.scenario_run_id}"
                        ),
                        "truthFrameId": truth["frameId"],
                        "businessId": business_id,
                        "field": field,
                        "sourceValue": source_value,
                        "outputValue": output_value,
                        "operation": operation,
                        "parameters": parameters,
                    }
                )
        measured_objects[business_id] = {
            "businessId": business_id,
            "elementType": truth_object["elementType"],
            "qualityCode": quality.value,
            "values": values,
        }
    simulation_time = definition.simulation_time.isoformat()
    return {
        "measurementSchemaVersion": MEASUREMENT_SCHEMA,
        "frameId": f"measurement-{definition.scenario_run_id}",
        "truthFrameId": truth["frameId"],
        "gridId": definition.grid_id,
        "topologyVersion": definition.topology_version,
        "scenarioRunId": definition.scenario_run_id,
        "measurementTime": simulation_time,
        "arrivalTime": simulation_time,
        "generatedAt": datetime.now(UTC).isoformat(),
        "noiseModel": {
            "type": "GAUSSIAN_RELATIVE",
            "relativeStd": relative_noise_std,
            "randomSeed": definition.random_seed,
        },
        "objects": measured_objects,
    }, audit


def _build_labels(
    truth: dict[str, Any],
    definition: ScenarioDefinition,
) -> dict[str, Any]:
    event_type = definition.event.event_type
    return {
        "labelSchemaVersion": "grid-label-v1",
        "scenarioRunId": definition.scenario_run_id,
        "primaryLabel": event_type.value,
        "rootCauseBusinessId": (
            definition.event.target_business_id
        ),
        "isNormal": event_type == EventType.NORMAL,
        "isPhysicalEvent": event_type
        in {
            EventType.LINE_OUTAGE,
            EventType.TRANSFORMER_OUTAGE,
            EventType.SWITCH_MISOPERATION,
            EventType.LOAD_SURGE,
            EventType.GENERATION_DROP,
            EventType.TAP_POSITION_ANOMALY,
        },
        "isMeasurementEvent": event_type
        in {
            EventType.MEASUREMENT_BIAS,
            EventType.MEASUREMENT_DROPOUT,
            EventType.MEASUREMENT_FROZEN,
            EventType.MEASUREMENT_DRIFT,
            EventType.MEASUREMENT_DELAY,
            EventType.MEASUREMENT_QUANTIZATION,
        },
        "impactLabels": {
            "UNDERVOLTAGE": bool(
                truth["impacts"]["undervoltageBusinessIds"]
            ),
            "OVERVOLTAGE": bool(
                truth["impacts"]["overvoltageBusinessIds"]
            ),
            "OVERLOAD": bool(
                truth["impacts"]["overloadedBusinessIds"]
            ),
            "ISLANDING_OR_UNAVAILABLE": bool(
                truth["impacts"]["unavailableBusinessIds"]
            ),
        },
    }


def _validate_run(
    definition: ScenarioDefinition,
    baseline: dict[str, Any],
    truth: dict[str, Any],
    measurement: dict[str, Any],
    labels: dict[str, Any],
) -> dict[str, Any]:
    baseline_ids = set(baseline["objects"])
    truth_ids = set(truth["objects"])
    measurement_ids = set(measurement["objects"])
    checks = {
        "gridIdentityMatches": (
            definition.grid_id
            == truth["gridId"]
            == measurement["gridId"]
        ),
        "topologyVersionMatches": (
            definition.topology_version
            == truth["topologyVersion"]
            == measurement["topologyVersion"]
        ),
        "objectCoverageMatches": (
            baseline_ids == truth_ids == measurement_ids
        ),
        "truthTraceable": (
            measurement["truthFrameId"] == truth["frameId"]
        ),
        "labelTraceable": (
            labels["scenarioRunId"]
            == definition.scenario_run_id
        ),
        "targetExists": (
            definition.event.target_business_id is None
            or definition.event.target_business_id in truth_ids
        ),
    }
    return {
        "status": (
            "passed" if all(checks.values()) else "failed"
        ),
        "checks": checks,
        "objectCounts": {
            "baseline": len(baseline_ids),
            "truth": len(truth_ids),
            "measurement": len(measurement_ids),
        },
    }


def _field_catalog() -> dict[str, Any]:
    return {
        "schemaVersion": "grid-white-box-catalog-v1",
        "principle": (
            "模型输入来自Measurement，标签和物理核对来自Truth，"
            "根因来自Scenario。所有中间转换均可见。"
        ),
        "files": {
            "scenario.json": "人为施加的离线事件、目标、参数和随机种子",
            "truth/baseline.json": "同一时刻无事件的pandapower物理基线",
            "truth/event.json": "施加事件后pandapower计算得到的物理真值",
            "measurements/frame.json": "Truth经过噪声和量测异常后的模型可见观测",
            "measurements/transform-audit.jsonl": "逐设备逐字段的变换审计",
            "labels.json": "根因、目标和物理影响标签",
            "validation.json": "身份、覆盖和追溯完整性检查",
        },
        "units": {
            "vmPu": "p.u.",
            "vaDegree": "degree",
            "pMw": "MW",
            "qMvar": "Mvar",
            "iFromKa": "kA",
            "iToKa": "kA",
            "loadingPercent": "%",
            "lossMw": "MW",
        },
        "qualityCodes": {
            code.value: description
            for code, description in {
                QualityCode.GOOD: "正常模拟量测",
                QualityCode.SUSPECT: "包含模拟偏置等可疑变换",
                QualityCode.BAD: "明确不可用",
                QualityCode.MISSING: "模拟丢失，不以0代替",
                QualityCode.SUBSTITUTED: "使用替代值",
                QualityCode.DELAYED: "模拟延迟到达",
                QualityCode.FROZEN: "冻结在历史值",
            }.items()
        },
    }


def _batch_response(
    manifest: dict[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    return {
        **manifest,
        "artifactPath": str(artifact_dir.resolve()),
        "manifestPath": str(
            (artifact_dir / "manifest.json").resolve()
        ),
        "readmePath": str((artifact_dir / "README.md").resolve()),
        "indexPath": str(
            (artifact_dir / "scenario-index.csv").resolve()
        ),
    }


def _batch_readme(
    batch_id: str,
    grid_manifest: dict[str, Any],
    scenario_count: int,
    distribution: Counter[str],
) -> str:
    rows = "\n".join(
        f"| {label} | {count} |"
        for label, count in sorted(distribution.items())
    )
    return f"""# DONGJIN离线训练场景批次

- 批次：`{batch_id}`
- 电网：`{grid_manifest["gridId"]}`
- 拓扑版本：`{grid_manifest["topologyVersion"]}`
- 场景数量：{scenario_count}

## 类别分布

| 类别 | 数量 |
|---|---:|
{rows}

## 如何查看

1. 打开`scenario-index.csv`查看所有训练场景。
2. 进入`runs/<scenarioRunId>/`查看单次场景。
3. `scenario.json`说明模拟了什么。
4. `truth/baseline.json`和`truth/event.json`展示pandapower计算前后差异。
5. `measurements/frame.json`是模型将看到的数据。
6. `measurements/transform-audit.jsonl`记录每个噪声、偏置、丢失或冻结变换。
7. `labels.json`是模型训练答案。

这些文件是离线模拟产物，不会写入在线Neo4j或Redis。
"""


def _run_summary(
    definition: ScenarioDefinition,
    truth: dict[str, Any],
    measurement: dict[str, Any],
    labels: dict[str, Any],
) -> str:
    return f"""# 场景白箱摘要

- 场景运行：`{definition.scenario_run_id}`
- 事件：`{definition.event.event_type.value}`
- 目标：`{definition.event.target_business_id or "无（正常场景）"}`
- 仿真时间：`{definition.simulation_time.isoformat()}`
- 随机种子：`{definition.random_seed}`
- 潮流收敛：`{truth["converged"]}`
- Truth对象：{len(truth["objects"])}
- Measurement对象：{len(measurement["objects"])}
- 主标签：`{labels["primaryLabel"]}`

本目录依次暴露Scenario、Baseline Truth、Event Truth、Measurement、变换审计和Label。
"""


def _business_id(
    grid_id: str,
    element_type: str,
    index: Any,
) -> str:
    return f"{grid_id}:{element_type}:{_source_index(index)}"


def _source_index(index: Any) -> int | float | str:
    if isinstance(index, (np.integer, int)):
        return int(index)
    if isinstance(index, (np.floating, float)) and float(
        index
    ).is_integer():
        return int(index)
    return str(index)


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
