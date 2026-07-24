from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandapower as pp
import pandapower.shortcircuit as sc
import pandas as pd

from app.grid.artifact_service import resolve_active_grid_package
from app.grid.offline_io import write_csv, write_json
from app.grid.settings import GridSettings, get_grid_settings


class ShortCircuitAnalysisError(RuntimeError):
    pass


def run_short_circuit_analysis(
    target_business_id: str,
    *,
    fault_type: Literal["3ph", "2ph", "1ph"] = "3ph",
    case: Literal["max", "min"] = "max",
    r_fault_ohm: float = 0.0,
    x_fault_ohm: float = 0.0,
    s_sc_mva: float | None = None,
    rx: float | None = None,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    manifest, artifact_dir = resolve_active_grid_package(settings)
    prefix = f"{manifest['gridId']}:bus:"
    if not target_business_id.startswith(prefix):
        raise ShortCircuitAnalysisError(
            "短路目标必须是当前P1中的母线businessId"
        )
    source_text = target_business_id.removeprefix(prefix)
    net = pp.from_json(str(artifact_dir / "network.json"))
    matching = [index for index in net.bus.index if str(index) == source_text]
    if not matching:
        raise ShortCircuitAnalysisError(
            f"短路目标不存在：{target_business_id}"
        )
    bus_index = matching[0]
    analysis_id = (
        f"short-circuit-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    root = settings.resolved_short_circuit_dir / analysis_id
    root.mkdir(parents=True, exist_ok=False)
    request = {
        "analysisId": analysis_id,
        "gridId": manifest["gridId"],
        "topologyVersion": manifest["topologyVersion"],
        "faultType": fault_type,
        "case": case,
        "targetBusinessId": target_business_id,
        "faultParameters": {
            "rFaultOhm": r_fault_ohm,
            "xFaultOhm": x_fault_ohm,
            "externalGridShortCircuitPowerMva": s_sc_mva,
            "externalGridRx": rx,
        },
        "requestedAt": datetime.now(UTC).isoformat(),
    }
    write_json(root / "request.json", request)
    if s_sc_mva is None or rx is None:
        failure = {
            **request,
            "status": "FAILED",
            "error": (
                "P1中的SimBench外部电网没有短路容量和R/X参数；"
                "请显式提供sScMva与rx，系统不会静默补造。"
            ),
        }
        write_json(root / "result.json", failure)
        raise ShortCircuitAnalysisError(failure["error"])
    if fault_type == "1ph":
        failure = {
            **request,
            "status": "FAILED",
            "error": (
                "当前P1缺少线路零序r0/x0/c0及外部电网零序参数，"
                "暂不能物理可信地计算单相接地短路。"
            ),
        }
        write_json(root / "result.json", failure)
        raise ShortCircuitAnalysisError(failure["error"])
    column_suffix = "max" if case == "max" else "min"
    net.ext_grid[f"s_sc_{column_suffix}_mva"] = float(s_sc_mva)
    net.ext_grid[f"rx_{column_suffix}"] = float(rx)
    try:
        sc.calc_sc(
            net,
            bus=bus_index,
            fault=fault_type,
            case=case,
            r_fault_ohm=r_fault_ohm,
            x_fault_ohm=x_fault_ohm,
            ip=True,
            ith=True,
            branch_results=True,
        )
    except Exception as exc:
        failure = {
            **request,
            "status": "FAILED",
            "error": f"{type(exc).__name__}: {exc}",
            "warning": (
                "该类型可能缺少零序或短路参数；系统没有静默补造参数。"
            ),
        }
        write_json(root / "result.json", failure)
        raise ShortCircuitAnalysisError(failure["error"]) from exc

    bus_rows = _result_rows(
        net.res_bus_sc,
        manifest["gridId"],
        "bus",
    )
    line_rows = _result_rows(
        getattr(net, "res_line_sc", pd.DataFrame()),
        manifest["gridId"],
        "line",
    )
    trafo_rows = _result_rows(
        getattr(net, "res_trafo_sc", pd.DataFrame()),
        manifest["gridId"],
        "trafo",
    )
    write_csv(
        root / "bus-results.csv",
        bus_rows,
        fieldnames=list(bus_rows[0]) if bus_rows else [],
    )
    write_csv(
        root / "line-results.csv",
        line_rows,
        fieldnames=list(line_rows[0]) if line_rows else [],
    )
    write_csv(
        root / "transformer-results.csv",
        trafo_rows,
        fieldnames=list(trafo_rows[0]) if trafo_rows else [],
    )
    target_result = next(
        (
            row for row in bus_rows
            if row["businessId"] == target_business_id
        ),
        {},
    )
    result = {
        "shortCircuitSchemaVersion": "grid-short-circuit-result-v1",
        **request,
        "status": "COMPLETED",
        "calculationMethod": "pandapower.shortcircuit.calc_sc",
        "targetResult": target_result,
        "resultCounts": {
            "buses": len(bus_rows),
            "lines": len(line_rows),
            "transformers": len(trafo_rows),
        },
        "warnings": [
            "短路结果属于独立稳态短路分析，不是连续潮流快照。",
            "结果未写入dongjin:snapshot:active。",
        ],
        "artifactPath": str(root),
        "generatedAt": datetime.now(UTC).isoformat(),
    }
    write_json(root / "result.json", result)
    (root / "README.md").write_text(
        "# 独立短路分析白箱档案\n\n"
        f"- 分析ID：`{analysis_id}`\n"
        f"- 故障类型：`{fault_type}`\n"
        f"- 目标：`{target_business_id}`\n\n"
        "本目录结果不属于连续潮流，也不会进入当前稳态GNN输入。\n",
        encoding="utf-8",
    )
    return result


def get_short_circuit_analysis(
    analysis_id: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    if not analysis_id or Path(analysis_id).name != analysis_id:
        raise ShortCircuitAnalysisError("短路分析ID无效")
    path = (
        settings.resolved_short_circuit_dir
        / analysis_id
        / "result.json"
    )
    if not path.is_file():
        raise ShortCircuitAnalysisError(
            f"短路分析不存在：{analysis_id}"
        )
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _result_rows(
    frame: pd.DataFrame,
    grid_id: str,
    element_type: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, series in frame.iterrows():
        values = {
            str(column): _finite(value)
            for column, value in series.items()
        }
        rows.append(
            {
                "businessId": f"{grid_id}:{element_type}:{index}",
                "sourceIndex": index,
                **values,
            }
        )
    return rows


def _finite(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    return value
