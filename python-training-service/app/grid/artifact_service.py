from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pandapower as pp
import simbench as sb

from app.grid.settings import GridSettings, get_grid_settings


SCHEMA_VERSION = "grid-schema-v1"
ID_STRATEGY = "{gridId}:{elementType}:{sourceIndex}"
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ELEMENT_TABLES = (
    "bus",
    "line",
    "trafo",
    "trafo3w",
    "switch",
    "load",
    "sgen",
    "gen",
    "ext_grid",
    "storage",
    "shunt",
    "impedance",
    "ward",
    "xward",
    "motor",
    "asymmetric_load",
    "asymmetric_sgen",
    "substation",
)
REQUIRED_TABLES = ("bus", "line", "trafo", "switch", "load", "sgen", "ext_grid")
BASELINE_TABLES = ("res_bus", "res_line", "res_trafo", "res_trafo3w", "res_ext_grid")


class GridPackageError(RuntimeError):
    pass


def initialize_grid_package(
    *,
    simbench_code: str | None = None,
    topology_version: str = "v1",
    force: bool = False,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    code = (simbench_code or settings.simbench_code).strip()
    if not code:
        raise GridPackageError("SimBench网络编号不能为空")
    if not VERSION_PATTERN.fullmatch(topology_version):
        raise GridPackageError("topologyVersion只能包含字母、数字、点、下划线和连字符")

    grid_id = _grid_id(code)
    root = settings.resolved_data_dir
    grid_root = _safe_child(root, grid_id)
    final_dir = _safe_child(grid_root, topology_version)
    manifest_path = final_dir / "manifest.json"

    if manifest_path.is_file() and not force:
        manifest = _read_json(manifest_path)
        _write_active_marker(root, manifest, final_dir)
        return _response_from_manifest(manifest, final_dir, reused=True)

    grid_root.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{topology_version}-", dir=grid_root))
    backup_dir: Path | None = None
    try:
        net = sb.get_simbench_net(code)
        validation, profile_metadata = _validate_source_network(net, grid_id)
        pp.runpp(net, numba=False)
        _validate_baseline(net)

        id_mapping = _build_id_mapping(net, grid_id)
        topology = _build_topology(net, grid_id, topology_version, id_mapping)
        baseline = _build_baseline_results(net, grid_id, topology_version, id_mapping)

        network_path = temporary_dir / "network.json"
        pp.to_json(net, str(network_path), indent=2, sort_keys=True)
        _write_json(temporary_dir / "std-types.json", net.std_types)
        _write_json(temporary_dir / "id-mapping.json", id_mapping)
        _write_json(temporary_dir / "topology.json", topology)
        _write_json(temporary_dir / "profile-metadata.json", profile_metadata)
        _write_json(temporary_dir / "baseline-results.json", baseline)

        reload_validation = _validate_saved_network(network_path, net)
        validation["checks"].update(reload_validation)
        validation["status"] = "passed"

        package_files = sorted(path for path in temporary_dir.iterdir() if path.is_file())
        checksums = {path.name: _sha256(path) for path in package_files}
        file_sizes = {path.name: path.stat().st_size for path in package_files}
        manifest = {
            "gridId": grid_id,
            "topologyVersion": topology_version,
            "schemaVersion": SCHEMA_VERSION,
            "simbenchCode": code,
            "source": "SimBench",
            "createdAt": datetime.now(UTC).isoformat(),
            "generator": "python-training-service/app/grid",
            "idStrategy": ID_STRATEGY,
            "dependencyVersions": {
                "python": _python_version(),
                "pandapower": version("pandapower"),
                "simbench": version("simbench"),
                "pandas": version("pandas"),
                "numpy": version("numpy"),
            },
            "elementCounts": _element_counts(net),
            "profileSummary": profile_metadata["summary"],
            "baseline": baseline["summary"],
            "validation": validation,
            "checksums": checksums,
            "fileSizes": file_sizes,
        }
        _write_json(temporary_dir / "manifest.json", manifest)

        if final_dir.exists():
            backup_dir = _safe_child(grid_root, f".{topology_version}-backup")
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            os.replace(final_dir, backup_dir)
        os.replace(temporary_dir, final_dir)
        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(backup_dir)

        _write_active_marker(root, manifest, final_dir)
        return _response_from_manifest(manifest, final_dir, reused=False)
    except Exception as exc:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir, ignore_errors=True)
        if backup_dir is not None and backup_dir.exists() and not final_dir.exists():
            os.replace(backup_dir, final_dir)
        if isinstance(exc, GridPackageError):
            raise
        raise GridPackageError(f"标准电网包生成失败：{type(exc).__name__}: {exc}") from exc


def get_active_grid_package(settings: GridSettings | None = None) -> dict[str, Any]:
    manifest, artifact_dir = resolve_active_grid_package(settings)
    return _response_from_manifest(manifest, artifact_dir, reused=True)


def resolve_active_grid_package(
    settings: GridSettings | None = None,
) -> tuple[dict[str, Any], Path]:
    settings = settings or get_grid_settings()
    marker_path = settings.resolved_data_dir / "active-grid.json"
    if not marker_path.is_file():
        raise GridPackageError("尚未生成活动标准电网包")
    marker = _read_json(marker_path)
    artifact_dir = Path(marker["artifactPath"])
    manifest_path = artifact_dir / "manifest.json"
    if not manifest_path.is_file():
        raise GridPackageError("活动电网包的manifest.json不存在")
    return _read_json(manifest_path), artifact_dir


def get_grid_validation(
    grid_id: str,
    topology_version: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    if not VERSION_PATTERN.fullmatch(topology_version):
        raise GridPackageError("topologyVersion格式无效")
    manifest_path = (
        _safe_child(_safe_child(settings.resolved_data_dir, grid_id), topology_version)
        / "manifest.json"
    )
    if not manifest_path.is_file():
        raise GridPackageError("指定的标准电网包不存在")
    manifest = _read_json(manifest_path)
    return {
        "gridId": manifest["gridId"],
        "topologyVersion": manifest["topologyVersion"],
        "schemaVersion": manifest["schemaVersion"],
        "validation": manifest["validation"],
        "checksums": manifest["checksums"],
    }


def _validate_source_network(net: Any, grid_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    checks: dict[str, Any] = {}
    for table_name in REQUIRED_TABLES:
        table = getattr(net, table_name, None)
        if not isinstance(table, pd.DataFrame) or table.empty:
            raise GridPackageError(f"SimBench网络缺少必需设备表：{table_name}")
    checks["requiredTablesPresent"] = True

    ids = [
        _business_id(grid_id, table_name, index)
        for table_name in ELEMENT_TABLES
        for index in _table(net, table_name).index
    ]
    if len(ids) != len(set(ids)):
        raise GridPackageError("稳定业务ID存在重复")
    checks["stableBusinessIdsUnique"] = True

    bus_indexes = set(net.bus.index)
    _validate_references(net.line, ("from_bus", "to_bus"), bus_indexes, "line")
    _validate_references(net.trafo, ("hv_bus", "lv_bus"), bus_indexes, "trafo")
    _validate_references(net.load, ("bus",), bus_indexes, "load")
    _validate_references(net.sgen, ("bus",), bus_indexes, "sgen")
    _validate_references(net.ext_grid, ("bus",), bus_indexes, "ext_grid")
    checks["terminalReferencesValid"] = True

    _validate_positive(net.bus, ("vn_kv",), "bus")
    _validate_positive(net.line, ("length_km", "max_i_ka"), "line")
    _validate_positive(net.trafo, ("sn_mva", "vn_hv_kv", "vn_lv_kv"), "trafo")
    checks["requiredRatingsPositive"] = True

    profiles = net.get("profiles")
    if not isinstance(profiles, dict) or "load" not in profiles or "renewables" not in profiles:
        raise GridPackageError("SimBench网络未包含必需的负荷和新能源曲线")
    absolute = sb.get_absolute_values(net, profiles_instead_of_study_cases=True)
    required_absolute = (("load", "p_mw"), ("load", "q_mvar"), ("sgen", "p_mw"))
    absolute_shapes: dict[str, list[int]] = {}
    for key in required_absolute:
        values = absolute.get(key)
        expected_columns = len(_table(net, key[0]))
        if not isinstance(values, pd.DataFrame) or values.shape[1] != expected_columns:
            raise GridPackageError(f"时间曲线与设备数量不匹配：{key[0]}.{key[1]}")
        absolute_shapes[f"{key[0]}.{key[1]}"] = list(values.shape)
    checks["profilesMatchDynamicDevices"] = True

    profile_tables = {
        name: {
            "rows": len(table),
            "columns": list(table.columns),
            "timeColumn": "time" if "time" in table.columns else None,
        }
        for name, table in profiles.items()
        if isinstance(table, pd.DataFrame)
    }
    device_associations = {
        name: {
            "deviceCount": len(_table(net, name)),
            "withProfile": int(_table(net, name).get("profile", pd.Series(dtype=object)).notna().sum()),
            "profiles": sorted(
                _table(net, name).get("profile", pd.Series(dtype=object)).dropna().astype(str).unique().tolist()
            ),
        }
        for name in ("load", "sgen", "gen", "storage")
    }
    time_steps = max((item["rows"] for item in profile_tables.values()), default=0)
    profile_metadata = {
        "gridId": grid_id,
        "source": "SimBench",
        "tables": profile_tables,
        "deviceAssociations": device_associations,
        "absoluteValueShapes": absolute_shapes,
        "summary": {
            "timeSteps": time_steps,
            "profileTables": len(profile_tables),
            "loadDevicesWithProfile": device_associations["load"]["withProfile"],
            "generationDevicesWithProfile": device_associations["sgen"]["withProfile"],
        },
    }
    return {"status": "running", "checks": checks, "warnings": []}, profile_metadata


def _validate_baseline(net: Any) -> None:
    if not bool(net.converged):
        raise GridPackageError("基准潮流未收敛")
    for table_name in ("res_bus", "res_line", "res_trafo", "res_ext_grid"):
        table = _table(net, table_name)
        if table.empty:
            continue
        numeric = table.select_dtypes(include=[np.number])
        if numeric.empty or not np.isfinite(numeric.to_numpy(dtype=float)).all():
            raise GridPackageError(f"基准潮流结果包含无效数值：{table_name}")


def _validate_saved_network(path: Path, original: Any) -> dict[str, Any]:
    restored = pp.from_json(str(path))
    for table_name in ELEMENT_TABLES:
        if len(_table(restored, table_name)) != len(_table(original, table_name)):
            raise GridPackageError(f"network.json重新加载后设备数量不一致：{table_name}")
    original_profiles = original.get("profiles", {})
    restored_profiles = restored.get("profiles", {})
    if {
        key: value.shape for key, value in original_profiles.items() if isinstance(value, pd.DataFrame)
    } != {
        key: value.shape for key, value in restored_profiles.items() if isinstance(value, pd.DataFrame)
    }:
        raise GridPackageError("network.json重新加载后曲线结构不一致")
    pp.runpp(restored, numba=False)
    if not bool(restored.converged):
        raise GridPackageError("network.json重新加载后基准潮流不收敛")
    return {
        "networkReloadEquivalent": True,
        "reloadedPowerFlowConverged": True,
        "profileShapesPreserved": True,
    }


def _build_id_mapping(net: Any, grid_id: str) -> dict[str, Any]:
    entries = []
    for table_name in ELEMENT_TABLES:
        for index in _table(net, table_name).index:
            entries.append(
                {
                    "businessId": _business_id(grid_id, table_name, index),
                    "elementType": table_name,
                    "sourceIndex": _json_value(index),
                }
            )
    return {
        "gridId": grid_id,
        "schemaVersion": SCHEMA_VERSION,
        "idStrategy": ID_STRATEGY,
        "entries": entries,
    }


def _build_topology(
    net: Any,
    grid_id: str,
    topology_version: str,
    id_mapping: dict[str, Any],
) -> dict[str, Any]:
    lookup = {
        (entry["elementType"], str(entry["sourceIndex"])): entry["businessId"]
        for entry in id_mapping["entries"]
    }
    elements: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    for table_name in ELEMENT_TABLES:
        table = _table(net, table_name)
        for index, row in table.iterrows():
            business_id = lookup[(table_name, str(_json_value(index)))]
            elements.append(
                {
                    "businessId": business_id,
                    "elementType": table_name,
                    "sourceIndex": _json_value(index),
                    "parameters": {key: _json_value(value) for key, value in row.items()},
                }
            )
            relationships.extend(
                _element_relationships(table_name, row, business_id, lookup)
            )
    return {
        "gridId": grid_id,
        "topologyVersion": topology_version,
        "schemaVersion": SCHEMA_VERSION,
        "elements": elements,
        "relationships": relationships,
    }


def _element_relationships(
    table_name: str,
    row: pd.Series,
    business_id: str,
    lookup: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    terminal_specs = {
        "line": (("from_bus", "FROM_TERMINAL", "bus"), ("to_bus", "TO_TERMINAL", "bus")),
        "trafo": (("hv_bus", "HV_TERMINAL", "bus"), ("lv_bus", "LV_TERMINAL", "bus")),
        "trafo3w": (
            ("hv_bus", "HV_TERMINAL", "bus"),
            ("mv_bus", "MV_TERMINAL", "bus"),
            ("lv_bus", "LV_TERMINAL", "bus"),
        ),
        "load": (("bus", "CONNECTED_TO", "bus"),),
        "sgen": (("bus", "CONNECTED_TO", "bus"),),
        "gen": (("bus", "CONNECTED_TO", "bus"),),
        "ext_grid": (("bus", "CONNECTED_TO", "bus"),),
        "storage": (("bus", "CONNECTED_TO", "bus"),),
        "shunt": (("bus", "CONNECTED_TO", "bus"),),
        "motor": (("bus", "CONNECTED_TO", "bus"),),
        "asymmetric_load": (("bus", "CONNECTED_TO", "bus"),),
        "asymmetric_sgen": (("bus", "CONNECTED_TO", "bus"),),
    }
    result = []
    for column, relation_type, target_table in terminal_specs.get(table_name, ()):
        target = lookup.get((target_table, str(_json_value(row[column]))))
        if target:
            result.append(
                {
                    "relationshipId": f"{business_id}:{relation_type.lower()}",
                    "type": relation_type,
                    "source": business_id,
                    "target": target,
                }
            )
    if table_name == "switch":
        bus_target = lookup.get(("bus", str(_json_value(row["bus"]))))
        if bus_target:
            result.append(
                {
                    "relationshipId": f"{business_id}:switch_bus",
                    "type": "CONNECTED_TO",
                    "source": business_id,
                    "target": bus_target,
                }
            )
        controlled_table = {"b": "bus", "l": "line", "t": "trafo", "t3": "trafo3w"}.get(
            str(row["et"])
        )
        controlled = (
            lookup.get((controlled_table, str(_json_value(row["element"]))))
            if controlled_table
            else None
        )
        if controlled:
            result.append(
                {
                    "relationshipId": f"{business_id}:controls",
                    "type": "CONTROLS",
                    "source": business_id,
                    "target": controlled,
                }
            )
    return result


def _build_baseline_results(
    net: Any,
    grid_id: str,
    topology_version: str,
    id_mapping: dict[str, Any],
) -> dict[str, Any]:
    result_to_element = {
        "res_bus": "bus",
        "res_line": "line",
        "res_trafo": "trafo",
        "res_trafo3w": "trafo3w",
        "res_ext_grid": "ext_grid",
    }
    tables: dict[str, Any] = {}
    for result_table in BASELINE_TABLES:
        table = _table(net, result_table)
        element_type = result_to_element[result_table]
        tables[result_table] = [
            {
                "businessId": _business_id(grid_id, element_type, index),
                "sourceIndex": _json_value(index),
                "values": {key: _json_value(value) for key, value in row.items()},
            }
            for index, row in table.iterrows()
        ]
    bus_vm = net.res_bus["vm_pu"]
    line_loading = net.res_line["loading_percent"]
    trafo_loading = net.res_trafo["loading_percent"]
    return {
        "gridId": grid_id,
        "topologyVersion": topology_version,
        "schemaVersion": SCHEMA_VERSION,
        "converged": bool(net.converged),
        "summary": {
            "converged": bool(net.converged),
            "busVoltageMinPu": float(bus_vm.min()),
            "busVoltageMaxPu": float(bus_vm.max()),
            "lineLoadingMaxPercent": float(line_loading.max()),
            "transformerLoadingMaxPercent": (
                float(trafo_loading.max()) if not trafo_loading.empty else None
            ),
        },
        "tables": tables,
    }


def _element_counts(net: Any) -> dict[str, int]:
    return {name: len(_table(net, name)) for name in ELEMENT_TABLES}


def _validate_references(
    table: pd.DataFrame,
    columns: tuple[str, ...],
    valid_indexes: set[Any],
    table_name: str,
) -> None:
    for column in columns:
        invalid = [value for value in table[column].dropna() if value not in valid_indexes]
        if invalid:
            raise GridPackageError(f"{table_name}.{column}包含无效引用：{invalid[0]}")


def _validate_positive(table: pd.DataFrame, columns: tuple[str, ...], table_name: str) -> None:
    for column in columns:
        values = pd.to_numeric(table[column], errors="coerce")
        if values.isna().any() or (values <= 0).any():
            raise GridPackageError(f"{table_name}.{column}必须全部为正数")


def _table(net: Any, name: str) -> pd.DataFrame:
    value = net.get(name)
    return value if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _business_id(grid_id: str, element_type: str, source_index: Any) -> str:
    return f"{grid_id}:{element_type}:{source_index}"


def _grid_id(simbench_code: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", simbench_code.lower()).strip("-")
    return f"simbench-{normalized}"


def _safe_child(parent: Path, child: str) -> Path:
    candidate = (parent / child).resolve()
    resolved_parent = parent.resolve()
    if candidate.parent != resolved_parent:
        raise GridPackageError(f"非法数据目录：{child}")
    return candidate


def _write_active_marker(root: Path, manifest: dict[str, Any], artifact_dir: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    marker = {
        "gridId": manifest["gridId"],
        "topologyVersion": manifest["topologyVersion"],
        "schemaVersion": manifest["schemaVersion"],
        "simbenchCode": manifest["simbenchCode"],
        "artifactPath": str(artifact_dir.resolve()),
        "activatedAt": datetime.now(UTC).isoformat(),
    }
    temporary = root / ".active-grid.json.tmp"
    _write_json(temporary, marker)
    os.replace(temporary, root / "active-grid.json")


def _response_from_manifest(
    manifest: dict[str, Any], artifact_dir: Path, *, reused: bool
) -> dict[str, Any]:
    return {
        "status": "reused" if reused else "generated",
        "reused": reused,
        "gridId": manifest["gridId"],
        "simbenchCode": manifest["simbenchCode"],
        "topologyVersion": manifest["topologyVersion"],
        "schemaVersion": manifest["schemaVersion"],
        "artifactPath": str(artifact_dir.resolve()),
        "elementCounts": manifest["elementCounts"],
        "profileSummary": manifest["profileSummary"],
        "baseline": manifest["baseline"],
        "validation": manifest["validation"],
        "checksums": manifest["checksums"],
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(_json_value(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, np.ndarray, pd.Index)):
        return [_json_value(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "to_dict"):
        return _json_value(value.to_dict())
    return str(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _python_version() -> str:
    import platform

    return platform.python_version()
