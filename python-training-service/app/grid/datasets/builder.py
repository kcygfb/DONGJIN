from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.grid.artifact_service import resolve_active_grid_package
from app.grid.offline_io import (
    file_manifest,
    read_json,
    safe_child,
    write_csv,
    write_json,
    write_jsonl,
)
from app.grid.scenarios.service import get_scenario_batch
from app.grid.settings import GridSettings, get_grid_settings


DATASET_SCHEMA = "grid-gnn-dataset-v1"
INCLUDED_TYPES = (
    "bus",
    "line",
    "trafo",
    "switch",
    "load",
    "sgen",
    "gen",
    "ext_grid",
)
DYNAMIC_FIELDS = (
    "vmPu",
    "vaDegree",
    "pMw",
    "qMvar",
    "currentKa",
    "loadingPercent",
    "lossMw",
    "inService",
    "closed",
    "scaling",
)
QUALITY_CODES = ("GOOD", "SUSPECT", "BAD", "MISSING", "FROZEN")


class DatasetBuildError(RuntimeError):
    pass


class DatasetBuildRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    batch_id: str = Field(alias="batchId", min_length=1)
    dataset_id: str | None = Field(default=None, alias="datasetId")
    random_seed: int = Field(default=20260723, alias="randomSeed")
    train_ratio: float = Field(default=0.6, alias="trainRatio", gt=0, lt=1)
    validation_ratio: float = Field(
        default=0.2,
        alias="validationRatio",
        gt=0,
        lt=1,
    )

    @model_validator(mode="after")
    def validate_ratios(self) -> "DatasetBuildRequest":
        if self.train_ratio + self.validation_ratio >= 1:
            raise ValueError(
                "trainRatio + validationRatio必须小于1"
            )
        return self


def build_dataset(
    request: DatasetBuildRequest,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    batch = get_scenario_batch(request.batch_id, settings)
    grid_manifest, grid_dir = resolve_active_grid_package(settings)
    if (
        batch["gridId"] != grid_manifest["gridId"]
        or batch["topologyVersion"]
        != grid_manifest["topologyVersion"]
    ):
        raise DatasetBuildError(
            "场景批次与当前P1权威电网包版本不一致"
        )
    dataset_id = request.dataset_id or (
        f"gnn-dataset-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    root = settings.resolved_dataset_dir
    root.mkdir(parents=True, exist_ok=True)
    try:
        final_dir = safe_child(root, dataset_id)
    except ValueError as exc:
        raise DatasetBuildError(str(exc)) from exc
    if final_dir.exists():
        raise DatasetBuildError(f"数据集已存在：{dataset_id}")
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{dataset_id}-", dir=root)
    )
    try:
        topology = read_json(grid_dir / "topology.json")
        graph = _build_graph(topology)
        split_by_run = _split_runs(
            Path(batch["artifactPath"]) / "scenario-index.csv",
            request,
        )
        rows, sample_rows = _build_rows(
            Path(batch["artifactPath"]),
            graph,
            split_by_run,
        )
        frame = pd.DataFrame(rows)
        if frame.empty:
            raise DatasetBuildError("场景批次没有可构建的数据行")

        feature_columns = [
            column
            for column in frame.columns
            if column.startswith("feature.")
        ]
        feature_names = [
            column.removeprefix("feature.")
            for column in feature_columns
        ]
        metadata_columns = [
            column
            for column in frame.columns
            if not column.startswith("feature.")
        ]
        frame = frame[metadata_columns + feature_columns]
        _write_machine_tables(temporary_dir, frame)
        write_csv(
            temporary_dir / "samples.csv",
            frame.to_dict(orient="records"),
            fieldnames=list(frame.columns),
        )
        write_csv(
            temporary_dir / "sample-summary.csv",
            sample_rows,
            fieldnames=[
                "sampleId",
                "scenarioRunId",
                "split",
                "primaryLabel",
                "rootCauseBusinessId",
                "simulationTime",
                "vertexCount",
                "targetRowCount",
            ],
        )
        write_jsonl(
            temporary_dir / "samples.jsonl",
            frame.to_dict(orient="records"),
        )
        preview = frame.head(1000)
        write_csv(
            temporary_dir / "preview-first-1000-rows.csv",
            preview.to_dict(orient="records"),
            fieldnames=list(preview.columns),
        )
        write_json(temporary_dir / "graph.json", graph)
        write_json(
            temporary_dir / "feature-schema.json",
            _feature_schema(feature_names),
        )
        label_order = sorted(frame["nodeLabel"].unique().tolist())
        write_json(
            temporary_dir / "label-schema.json",
            _label_schema(label_order),
        )
        split_summary = _split_summary(sample_rows)
        write_csv(
            temporary_dir / "split-summary.csv",
            split_summary,
            fieldnames=["split", "primaryLabel", "scenarioCount"],
        )
        write_json(
            temporary_dir / "normalization.json",
            _normalization(frame, feature_names),
        )
        write_json(
            temporary_dir / "source-scenarios.json",
            {
                "batchId": request.batch_id,
                "batchManifestPath": batch["manifestPath"],
                "scenarioRuns": sample_rows,
            },
        )
        (temporary_dir / "README.md").write_text(
            _dataset_readme(
                dataset_id,
                request.batch_id,
                graph,
                frame,
                sample_rows,
                feature_names,
            ),
            encoding="utf-8",
        )
        manifest = {
            "datasetId": dataset_id,
            "datasetSchemaVersion": DATASET_SCHEMA,
            "gridId": grid_manifest["gridId"],
            "topologyVersion": grid_manifest["topologyVersion"],
            "graphSignature": graph["signature"],
            "sourceScenarioBatchId": request.batch_id,
            "createdAt": datetime.now(UTC).isoformat(),
            "randomSeed": request.random_seed,
            "rowCount": len(frame),
            "scenarioCount": len(sample_rows),
            "vertexCount": len(graph["vertices"]),
            "edgeCount": len(graph["edges"]),
            "featureCount": len(feature_names),
            "featureNames": feature_names,
            "labelOrder": label_order,
            "scenarioLabelDistribution": dict(
                sorted(
                    Counter(
                        row["primaryLabel"]
                        for row in sample_rows
                    ).items()
                )
            ),
            "splitScenarioCounts": dict(
                sorted(
                    Counter(
                        row["split"] for row in sample_rows
                    ).items()
                )
            ),
            "whiteBoxFiles": {
                "humanReadableFullTable": "samples.csv",
                "streamReadableFullTable": "samples.jsonl",
                "quickPreview": "preview-first-1000-rows.csv",
                "scenarioSummary": "sample-summary.csv",
                "splitSummary": "split-summary.csv",
                "graph": "graph.json",
                "featureSchema": "feature-schema.json",
                "labelSchema": "label-schema.json",
                "normalization": "normalization.json",
                "readme": "README.md",
            },
            "machineFiles": {
                split: f"{split}.parquet"
                for split in ("train", "validation", "test")
            }
            | {"all": "all.parquet"},
            "files": file_manifest(temporary_dir),
        }
        write_json(temporary_dir / "manifest.json", manifest)
        os.replace(temporary_dir, final_dir)
        return _dataset_response(manifest, final_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def list_datasets(
    settings: GridSettings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_grid_settings()
    root = settings.resolved_dataset_dir
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.iterdir(), reverse=True):
        manifest_path = path / "manifest.json"
        if manifest_path.is_file():
            result.append(
                _dataset_response(read_json(manifest_path), path)
            )
    return result


def get_dataset(
    dataset_id: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    try:
        path = safe_child(settings.resolved_dataset_dir, dataset_id)
    except ValueError as exc:
        raise DatasetBuildError(str(exc)) from exc
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise DatasetBuildError(f"数据集不存在：{dataset_id}")
    return _dataset_response(read_json(manifest_path), path)


def _build_graph(topology: dict[str, Any]) -> dict[str, Any]:
    elements = [
        element
        for element in topology["elements"]
        if element["elementType"] in INCLUDED_TYPES
    ]
    vertices = [
        {
            "index": index,
            "businessId": element["businessId"],
            "elementType": element["elementType"],
            "sourceIndex": element["sourceIndex"],
            "parameters": element["parameters"],
        }
        for index, element in enumerate(
            sorted(elements, key=lambda item: item["businessId"])
        )
    ]
    index_by_id = {
        item["businessId"]: item["index"] for item in vertices
    }
    seen: set[tuple[int, int]] = set()
    edges: list[dict[str, Any]] = []
    for relationship in topology["relationships"]:
        source = index_by_id.get(relationship["source"])
        target = index_by_id.get(relationship["target"])
        if source is None or target is None or source == target:
            continue
        key = tuple(sorted((source, target)))
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "sourceIndex": key[0],
                "targetIndex": key[1],
                "sourceBusinessId": vertices[key[0]][
                    "businessId"
                ],
                "targetBusinessId": vertices[key[1]][
                    "businessId"
                ],
            }
        )
    degrees = Counter(
        index
        for edge in edges
        for index in (edge["sourceIndex"], edge["targetIndex"])
    )
    for vertex in vertices:
        vertex["degree"] = int(degrees[vertex["index"]])
    signature_payload = json.dumps(
        {
            "vertices": [
                (item["businessId"], item["elementType"])
                for item in vertices
            ],
            "edges": [
                (item["sourceIndex"], item["targetIndex"])
                for item in edges
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "graphSchemaVersion": "grid-graph-v1",
        "vertexIdentity": "P1 stable businessId",
        "vertices": vertices,
        "edges": edges,
        "signature": hashlib.sha256(
            signature_payload.encode("utf-8")
        ).hexdigest(),
    }


def _split_runs(
    index_path: Path,
    request: DatasetBuildRequest,
) -> dict[str, str]:
    index_frame = pd.read_csv(index_path, encoding="utf-8-sig")
    rng = np.random.default_rng(request.random_seed)
    result: dict[str, str] = {}
    for _, group in index_frame.groupby("eventType", sort=True):
        run_ids = group["scenarioRunId"].astype(str).tolist()
        order = rng.permutation(len(run_ids))
        ordered = [run_ids[int(index)] for index in order]
        count = len(ordered)
        train_count = max(1, int(round(count * request.train_ratio)))
        validation_count = max(
            1,
            int(round(count * request.validation_ratio)),
        )
        if train_count + validation_count >= count:
            train_count = max(1, count - 2)
            validation_count = 1
        for index, run_id in enumerate(ordered):
            if index < train_count:
                split = "train"
            elif index < train_count + validation_count:
                split = "validation"
            else:
                split = "test"
            result[run_id] = split
    return result


def _build_rows(
    batch_dir: Path,
    graph: dict[str, Any],
    split_by_run: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []
    for run_id, split in sorted(split_by_run.items()):
        run_dir = batch_dir / "runs" / run_id
        scenario = read_json(run_dir / "scenario.json")
        baseline = read_json(run_dir / "truth" / "baseline.json")
        truth = read_json(run_dir / "truth" / "event.json")
        measurement = read_json(
            run_dir / "measurements" / "frame.json"
        )
        labels = read_json(run_dir / "labels.json")
        target = labels["rootCauseBusinessId"]
        sample_id = f"sample-{run_id}"
        target_rows = 0
        for vertex in graph["vertices"]:
            business_id = vertex["businessId"]
            measured = measurement["objects"].get(
                business_id,
                {
                    "qualityCode": "MISSING",
                    "values": {},
                },
            )
            baseline_object = baseline["objects"].get(
                business_id,
                {"values": {}},
            )
            is_target = target == business_id
            if is_target:
                target_rows += 1
            features = _features(
                vertex,
                measured,
                baseline_object,
            )
            rows.append(
                {
                    "datasetSchemaVersion": DATASET_SCHEMA,
                    "sampleId": sample_id,
                    "scenarioRunId": run_id,
                    "frameId": measurement["frameId"],
                    "split": split,
                    "gridId": scenario["gridId"],
                    "topologyVersion": scenario[
                        "topologyVersion"
                    ],
                    "simulationTime": scenario[
                        "simulationTime"
                    ],
                    "randomSeed": scenario["randomSeed"],
                    "vertexIndex": vertex["index"],
                    "businessId": business_id,
                    "elementType": vertex["elementType"],
                    "qualityCode": measured["qualityCode"],
                    "isTarget": bool(is_target),
                    "nodeLabel": (
                        labels["primaryLabel"]
                        if is_target
                        else "NORMAL"
                    ),
                    "scenarioLabel": labels["primaryLabel"],
                    **{
                        f"feature.{name}": value
                        for name, value in features.items()
                    },
                }
            )
        sample_rows.append(
            {
                "sampleId": sample_id,
                "scenarioRunId": run_id,
                "split": split,
                "primaryLabel": labels["primaryLabel"],
                "rootCauseBusinessId": target or "",
                "simulationTime": scenario["simulationTime"],
                "vertexCount": len(graph["vertices"]),
                "targetRowCount": target_rows,
            }
        )
    return rows, sample_rows


def _features(
    vertex: dict[str, Any],
    measurement: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, float]:
    element_type = vertex["elementType"]
    measured = _canonical_values(measurement.get("values", {}))
    baseline_values = _canonical_values(baseline.get("values", {}))
    parameters = vertex.get("parameters", {})
    result: dict[str, float] = {
        f"type.{name}": float(element_type == name)
        for name in INCLUDED_TYPES
    }
    result.update(
        {
            "topologyDegree": float(vertex["degree"]),
            "ratedVoltageKv": _first_finite(
                parameters,
                ("vn_kv", "vn_hv_kv", "vn_lv_kv"),
            ),
            "ratedPowerMva": _first_finite(
                parameters,
                ("sn_mva",),
            ),
            "lengthKm": _first_finite(
                parameters,
                ("length_km",),
            ),
        }
    )
    missing_count = 0
    for field in DYNAMIC_FIELDS:
        value = measured[field]
        missing = value is None
        if missing:
            missing_count += 1
        numeric = 0.0 if missing else float(value)
        result[field] = numeric
        result[f"{field}.missing"] = float(missing)
        baseline_value = baseline_values[field]
        result[f"{field}.deltaFromBaseline"] = (
            numeric - float(baseline_value)
            if baseline_value is not None and not missing
            else 0.0
        )
    result["missingFieldCount"] = float(missing_count)
    quality = str(measurement.get("qualityCode", "MISSING"))
    for code in QUALITY_CODES:
        result[f"quality.{code}"] = float(quality == code)
    return result


def _canonical_values(values: dict[str, Any]) -> dict[str, float | None]:
    return {
        "vmPu": _number(values.get("vmPu")),
        "vaDegree": _number(values.get("vaDegree")),
        "pMw": _first_number(
            values,
            ("pMw", "pFromMw", "pHvMw"),
        ),
        "qMvar": _first_number(
            values,
            ("qMvar", "qFromMvar", "qHvMvar"),
        ),
        "currentKa": _maximum_number(
            values,
            ("iFromKa", "iToKa", "iHvKa", "iLvKa"),
        ),
        "loadingPercent": _number(
            values.get("loadingPercent")
        ),
        "lossMw": _number(values.get("lossMw")),
        "inService": _boolean_number(values.get("inService")),
        "closed": _boolean_number(values.get("closed")),
        "scaling": _number(values.get("scaling")),
    }


def _write_machine_tables(root: Path, frame: pd.DataFrame) -> None:
    try:
        frame.to_parquet(
            root / "all.parquet",
            index=False,
            compression="zstd",
        )
        for split in ("train", "validation", "test"):
            frame.loc[frame["split"] == split].to_parquet(
                root / f"{split}.parquet",
                index=False,
                compression="zstd",
            )
    except ImportError as exc:
        raise DatasetBuildError(
            "生成标准Parquet数据集需要pyarrow，请安装requirements.txt"
        ) from exc


def _feature_schema(feature_names: list[str]) -> dict[str, Any]:
    units = {
        "vmPu": "p.u.",
        "vaDegree": "degree",
        "pMw": "MW",
        "qMvar": "Mvar",
        "currentKa": "kA",
        "loadingPercent": "%",
        "lossMw": "MW",
        "ratedVoltageKv": "kV",
        "ratedPowerMva": "MVA",
        "lengthKm": "km",
    }
    return {
        "featureSchemaVersion": "grid-gnn-feature-v1",
        "features": [
            {
                "name": name,
                "column": f"feature.{name}",
                "dataType": "float32",
                "unit": next(
                    (
                        unit
                        for prefix, unit in units.items()
                        if name.startswith(prefix)
                    ),
                    "",
                ),
                "source": (
                    "P1 static topology"
                    if name.startswith(("type.", "topology", "rated", "length"))
                    else "MeasurementFrame and baseline Truth"
                ),
            }
            for name in feature_names
        ],
        "missingPolicy": (
            "缺失数值列置0，但对应<field>.missing必须为1；"
            "真实0与缺失0可区分。"
        ),
    }


def _label_schema(labels: list[str]) -> dict[str, Any]:
    return {
        "labelSchemaVersion": "grid-gnn-label-v1",
        "labels": labels,
        "nodeLabelRule": (
            "根因目标顶点使用场景主标签，其他顶点为NORMAL；"
            "正常场景全部顶点为NORMAL。"
        ),
        "locationLabel": "isTarget",
        "scenarioLabel": "scenarioLabel",
    }


def _normalization(
    frame: pd.DataFrame,
    feature_names: list[str],
) -> dict[str, Any]:
    training = frame.loc[frame["split"] == "train"]
    return {
        "normalizationSchemaVersion": "grid-normalization-v1",
        "fitSplit": "train",
        "features": {
            name: {
                "mean": float(
                    training[f"feature.{name}"].mean()
                ),
                "standardDeviation": float(
                    training[f"feature.{name}"].std(ddof=0)
                ),
            }
            for name in feature_names
        },
    }


def _split_summary(
    sample_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts = Counter(
        (row["split"], row["primaryLabel"])
        for row in sample_rows
    )
    return [
        {
            "split": split,
            "primaryLabel": label,
            "scenarioCount": count,
        }
        for (split, label), count in sorted(counts.items())
    ]


def _dataset_response(
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
        "visibleSamplesPath": str(
            (artifact_dir / "samples.csv").resolve()
        ),
        "previewPath": str(
            (
                artifact_dir / "preview-first-1000-rows.csv"
            ).resolve()
        ),
    }


def _dataset_readme(
    dataset_id: str,
    batch_id: str,
    graph: dict[str, Any],
    frame: pd.DataFrame,
    samples: list[dict[str, Any]],
    feature_names: list[str],
) -> str:
    distribution = Counter(
        row["primaryLabel"] for row in samples
    )
    rows = "\n".join(
        f"| {label} | {count} |"
        for label, count in sorted(distribution.items())
    )
    return f"""# DONGJIN GNN白箱训练数据集

- 数据集：`{dataset_id}`
- 来源场景批次：`{batch_id}`
- 场景数：{len(samples)}
- 图顶点数：{len(graph["vertices"])}
- 图边数：{len(graph["edges"])}
- 长表行数：{len(frame)}
- 特征数：{len(feature_names)}

## 类别分布

| 场景标签 | 场景数 |
|---|---:|
{rows}

## 用户可直接查看

- `samples.csv`：完整训练长表，可用Excel或文本工具打开。
- `samples.jsonl`：同一完整长表，每行一个图顶点样本。
- `preview-first-1000-rows.csv`：快速预览。
- `sample-summary.csv`：每个场景的标签、根因和切分。
- `split-summary.csv`：训练/验证/测试类别分布。
- `feature-schema.json`：每个输入特征的来源和单位。
- `label-schema.json`：标签含义。
- `normalization.json`：仅用训练集拟合的归一化参数。
- `graph.json`：模型使用的完整图和稳定业务ID。

## 程序训练文件

- `train.parquet`
- `validation.parquet`
- `test.parquet`

Parquet不是隐藏数据；它与`samples.csv`来自同一张长表，CSV用于人工审查，
Parquet用于程序高效读取。
"""


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _first_number(
    values: dict[str, Any],
    names: tuple[str, ...],
) -> float | None:
    for name in names:
        value = _number(values.get(name))
        if value is not None:
            return value
    return None


def _maximum_number(
    values: dict[str, Any],
    names: tuple[str, ...],
) -> float | None:
    candidates = [
        value
        for name in names
        if (value := _number(values.get(name))) is not None
    ]
    return max(candidates) if candidates else None


def _boolean_number(value: Any) -> float | None:
    return None if value is None else float(bool(value))


def _first_finite(
    values: dict[str, Any],
    names: tuple[str, ...],
) -> float:
    value = _first_number(values, names)
    return 0.0 if value is None else value
