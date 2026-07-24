from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

from app.grid.artifact_service import (
    GridPackageError,
    resolve_active_grid_package,
)
from app.grid.settings import GridSettings, get_grid_settings


MANAGED_BY = "dongjin-python-service"
GRID_LABEL = "GridModel"
ELEMENT_LABELS = {
    "bus": "Bus",
    "line": "Line",
    "trafo": "Transformer",
    "trafo3w": "Transformer",
    "switch": "Switch",
    "load": "Load",
    "sgen": "SGen",
    "gen": "Generator",
    "ext_grid": "ExternalGrid",
    "storage": "Storage",
    "shunt": "Shunt",
    "impedance": "Impedance",
    "ward": "Ward",
    "xward": "XWard",
    "motor": "Motor",
    "asymmetric_load": "AsymmetricLoad",
    "asymmetric_sgen": "AsymmetricSGen",
    "substation": "Substation",
}
RELATIONSHIP_TYPES = {
    "FROM_TERMINAL",
    "TO_TERMINAL",
    "HV_TERMINAL",
    "MV_TERMINAL",
    "LV_TERMINAL",
    "CONNECTED_TO",
    "CONTROLS",
}
PARAMETER_UNITS = {
    "vn_kv": "kV",
    "vn_hv_kv": "kV",
    "vn_mv_kv": "kV",
    "vn_lv_kv": "kV",
    "length_km": "km",
    "r_ohm_per_km": "ohm/km",
    "x_ohm_per_km": "ohm/km",
    "c_nf_per_km": "nF/km",
    "g_us_per_km": "uS/km",
    "max_i_ka": "kA",
    "sn_mva": "MVA",
    "p_mw": "MW",
    "q_mvar": "Mvar",
    "z_ohm": "ohm",
    "in_ka": "kA",
}


class Neo4jProjectionError(GridPackageError):
    pass


def publish_active_grid_to_neo4j(
    grid_id: str,
    settings: GridSettings | None = None,
) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    manifest, artifact_dir = resolve_active_grid_package(settings)
    if manifest["gridId"] != grid_id:
        raise Neo4jProjectionError(
            f"请求的gridId不是当前活动电网：{grid_id}"
        )

    topology = _read_json(artifact_dir / "topology.json")
    _validate_topology_identity(topology, manifest)
    grouped_elements: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for element in topology["elements"]:
        element_type = element["elementType"]
        if element_type not in ELEMENT_LABELS:
            raise Neo4jProjectionError(f"Neo4j不支持设备类型：{element_type}")
        grouped_elements[element_type].append(
            _node_row(element, manifest)
        )

    grouped_relationships: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relationship in topology["relationships"]:
        relationship_type = relationship["type"]
        if relationship_type not in RELATIONSHIP_TYPES:
            raise Neo4jProjectionError(
                f"Neo4j不支持关系类型：{relationship_type}"
            )
        grouped_relationships[relationship_type].append(relationship)

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(
            settings.neo4j_username,
            settings.neo4j_password.get_secret_value(),
        ),
        connection_timeout=settings.health_timeout_seconds,
    )
    try:
        driver.verify_connectivity()
        with driver.session(database=settings.neo4j_database) as session:
            _ensure_schema(session)
            session.execute_write(
                _upsert_grid_model,
                manifest,
                str(artifact_dir),
            )
            for element_type, rows in grouped_elements.items():
                session.execute_write(
                    _upsert_nodes,
                    ELEMENT_LABELS[element_type],
                    rows,
                    manifest["topologyVersion"],
                )
            session.execute_write(
                _upsert_contains,
                manifest["gridId"],
                manifest["topologyVersion"],
                [row["businessId"] for rows in grouped_elements.values() for row in rows],
            )
            for relationship_type, rows in grouped_relationships.items():
                session.execute_write(
                    _upsert_relationships,
                    relationship_type,
                    rows,
                    manifest["topologyVersion"],
                )

            verification = _verify_projection(
                session,
                manifest,
                Counter(
                    element["elementType"] for element in topology["elements"]
                ),
                Counter(
                    relationship["type"]
                    for relationship in topology["relationships"]
                ),
            )
            session.execute_write(
                _activate_grid_model,
                manifest["gridId"],
                manifest["topologyVersion"],
                verification,
            )
    except Neo4jProjectionError:
        raise
    except Exception as exc:
        raise Neo4jProjectionError(
            f"Neo4j静态投影失败：{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        driver.close()

    return {
        "status": "published",
        "active": True,
        "managedBy": MANAGED_BY,
        "gridId": manifest["gridId"],
        "topologyVersion": manifest["topologyVersion"],
        "schemaVersion": manifest["schemaVersion"],
        "nodeCounts": verification["nodeCounts"],
        "relationshipCounts": verification["relationshipCounts"],
        "verified": True,
    }


def get_active_neo4j_grid(
    settings: GridSettings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_grid_settings()
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(
            settings.neo4j_username,
            settings.neo4j_password.get_secret_value(),
        ),
        connection_timeout=settings.health_timeout_seconds,
    )
    try:
        with driver.session(database=settings.neo4j_database) as session:
            record = session.run(
                """
                MATCH (grid:GridModel {
                    managedBy: $managedBy,
                    active: true
                })
                RETURN grid.gridId AS gridId,
                       grid.topologyVersion AS topologyVersion,
                       grid.schemaVersion AS schemaVersion,
                       grid.importStatus AS importStatus
                LIMIT 1
                """,
                managedBy=MANAGED_BY,
            ).single()
            return record.data() if record else None
    finally:
        driver.close()


def assert_active_neo4j_grid(
    grid_id: str,
    topology_version: str,
    settings: GridSettings | None = None,
) -> None:
    try:
        active = get_active_neo4j_grid(settings)
    except Exception as exc:
        raise Neo4jProjectionError(
            f"无法确认Neo4j活动电网：{type(exc).__name__}: {exc}"
        ) from exc
    if not active:
        raise Neo4jProjectionError("Neo4j中尚未发布活动标准电网，请先生成并发布SimBench拓扑")
    if (
        active["gridId"] != grid_id
        or active["topologyVersion"] != topology_version
    ):
        raise Neo4jProjectionError(
            "Neo4j活动拓扑版本与权威电网包不一致"
        )


def _ensure_schema(session: Any) -> None:
    session.run(
        """
        CREATE CONSTRAINT dongjin_grid_model_identity IF NOT EXISTS
        FOR (grid:GridModel)
        REQUIRE (grid.gridId, grid.topologyVersion) IS UNIQUE
        """
    ).consume()
    for label in sorted({"Device", *ELEMENT_LABELS.values()}):
        constraint_name = f"dongjin_{label.lower()}_identity"
        session.run(
            f"""
            CREATE CONSTRAINT {constraint_name} IF NOT EXISTS
            FOR (node:{label})
            REQUIRE (node.businessId, node.topologyVersion) IS UNIQUE
            """
        ).consume()
    session.run(
        """
        CREATE INDEX dongjin_grid_model_active IF NOT EXISTS
        FOR (grid:GridModel)
        ON (grid.active)
        """
    ).consume()
    session.run(
        """
        CREATE INDEX dongjin_device_grid_version IF NOT EXISTS
        FOR (node:Device)
        ON (node.gridId, node.topologyVersion)
        """
    ).consume()


def _upsert_grid_model(
    transaction: Any,
    manifest: dict[str, Any],
    artifact_path: str,
) -> None:
    transaction.run(
        """
        MERGE (grid:GridModel {
            gridId: $gridId,
            topologyVersion: $topologyVersion
        })
        SET grid.schemaVersion = $schemaVersion,
            grid.simbenchCode = $simbenchCode,
            grid.source = $source,
            grid.artifactPath = $artifactPath,
            grid.elementCountsJson = $elementCountsJson,
            grid.manifestChecksumsJson = $checksumsJson,
            grid.managedBy = $managedBy,
            grid.importStatus = "IMPORTING",
            grid.active = coalesce(grid.active, false),
            grid.updatedAt = datetime()
        """,
        gridId=manifest["gridId"],
        topologyVersion=manifest["topologyVersion"],
        schemaVersion=manifest["schemaVersion"],
        simbenchCode=manifest["simbenchCode"],
        source=manifest["source"],
        artifactPath=artifact_path,
        elementCountsJson=json.dumps(
            manifest["elementCounts"], ensure_ascii=False, sort_keys=True
        ),
        checksumsJson=json.dumps(
            manifest["checksums"], ensure_ascii=False, sort_keys=True
        ),
        managedBy=MANAGED_BY,
    ).consume()


def _upsert_nodes(
    transaction: Any,
    label: str,
    rows: list[dict[str, Any]],
    topology_version: str,
) -> None:
    transaction.run(
        f"""
        UNWIND $rows AS row
        MERGE (node:Device:{label} {{
            businessId: row.businessId,
            topologyVersion: $topologyVersion
        }})
        SET node += row.parameters,
            node.id = row.businessId,
            node.gridId = row.gridId,
            node.schemaVersion = row.schemaVersion,
            node.sourceIndex = row.sourceIndex,
            node.elementType = row.elementType,
            node.type = row.elementType,
            node.name = row.name,
            node.status = row.status,
            node.voltageLevel = row.voltageLevel,
            node.parameterUnitsJson = row.parameterUnitsJson,
            node.managedBy = $managedBy,
            node.updatedAt = datetime()
        """,
        rows=rows,
        topologyVersion=topology_version,
        managedBy=MANAGED_BY,
    ).consume()


def _upsert_contains(
    transaction: Any,
    grid_id: str,
    topology_version: str,
    business_ids: list[str],
) -> None:
    transaction.run(
        """
        MATCH (grid:GridModel {
            gridId: $gridId,
            topologyVersion: $topologyVersion
        })
        UNWIND $businessIds AS businessId
        MATCH (node:Device {
            businessId: businessId,
            topologyVersion: $topologyVersion
        })
        MERGE (grid)-[relation:CONTAINS {
            topologyVersion: $topologyVersion
        }]->(node)
        SET relation.managedBy = $managedBy
        """,
        gridId=grid_id,
        topologyVersion=topology_version,
        businessIds=business_ids,
        managedBy=MANAGED_BY,
    ).consume()


def _upsert_relationships(
    transaction: Any,
    relationship_type: str,
    rows: list[dict[str, Any]],
    topology_version: str,
) -> None:
    transaction.run(
        f"""
        UNWIND $rows AS row
        MATCH (source:Device {{
            businessId: row.source,
            topologyVersion: $topologyVersion
        }})
        MATCH (target:Device {{
            businessId: row.target,
            topologyVersion: $topologyVersion
        }})
        MERGE (source)-[relation:{relationship_type} {{
            relationshipId: row.relationshipId,
            topologyVersion: $topologyVersion
        }}]->(target)
        SET relation.id = row.relationshipId,
            relation.name = row.type,
            relation.status = "normal",
            relation.managedBy = $managedBy
        """,
        rows=rows,
        topologyVersion=topology_version,
        managedBy=MANAGED_BY,
    ).consume()


def _verify_projection(
    session: Any,
    manifest: dict[str, Any],
    expected_nodes: Counter[str],
    expected_relationships: Counter[str],
) -> dict[str, Any]:
    node_records = session.run(
        """
        MATCH (grid:GridModel {
            gridId: $gridId,
            topologyVersion: $topologyVersion,
            managedBy: $managedBy
        })-[:CONTAINS]->(node:Device)
        RETURN node.elementType AS type, count(node) AS count
        """,
        gridId=manifest["gridId"],
        topologyVersion=manifest["topologyVersion"],
        managedBy=MANAGED_BY,
    )
    actual_nodes = Counter(
        {record["type"]: record["count"] for record in node_records}
    )
    relationship_records = session.run(
        """
        MATCH (grid:GridModel {
            gridId: $gridId,
            topologyVersion: $topologyVersion,
            managedBy: $managedBy
        })-[:CONTAINS]->(source:Device)-[relation]->(target:Device)
              <-[:CONTAINS]-(grid)
        WHERE relation.managedBy = $managedBy
        RETURN type(relation) AS type, count(relation) AS count
        """,
        gridId=manifest["gridId"],
        topologyVersion=manifest["topologyVersion"],
        managedBy=MANAGED_BY,
    )
    actual_relationships = Counter(
        {record["type"]: record["count"] for record in relationship_records}
    )
    if actual_nodes != expected_nodes:
        raise Neo4jProjectionError(
            f"Neo4j节点核对失败：expected={dict(expected_nodes)}, "
            f"actual={dict(actual_nodes)}"
        )
    if actual_relationships != expected_relationships:
        raise Neo4jProjectionError(
            "Neo4j关系核对失败："
            f"expected={dict(expected_relationships)}, "
            f"actual={dict(actual_relationships)}"
        )
    return {
        "nodeCounts": dict(sorted(actual_nodes.items())),
        "relationshipCounts": dict(sorted(actual_relationships.items())),
    }


def _activate_grid_model(
    transaction: Any,
    grid_id: str,
    topology_version: str,
    verification: dict[str, Any],
) -> None:
    transaction.run(
        """
        MATCH (grid:GridModel {managedBy: $managedBy})
        SET grid.active = (
            grid.gridId = $gridId
            AND grid.topologyVersion = $topologyVersion
        )
        WITH grid
        WHERE grid.gridId = $gridId
          AND grid.topologyVersion = $topologyVersion
        SET grid.importStatus = "ACTIVE",
            grid.verifiedAt = datetime(),
            grid.verificationJson = $verificationJson
        """,
        managedBy=MANAGED_BY,
        gridId=grid_id,
        topologyVersion=topology_version,
        verificationJson=json.dumps(
            verification, ensure_ascii=False, sort_keys=True
        ),
    ).consume()


def _node_row(
    element: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    parameters = {
        key: _neo4j_value(value)
        for key, value in element["parameters"].items()
        if value is not None
    }
    name = str(parameters.get("name") or element["businessId"])
    element_type = element["elementType"]
    voltage = _voltage_level(parameters)
    status = (
        "open"
        if element_type == "switch" and parameters.get("closed") is False
        else "normal"
    )
    units = {
        key: PARAMETER_UNITS[key]
        for key in parameters
        if key in PARAMETER_UNITS
    }
    return {
        "businessId": element["businessId"],
        "gridId": manifest["gridId"],
        "schemaVersion": manifest["schemaVersion"],
        "sourceIndex": element["sourceIndex"],
        "elementType": element_type,
        "name": name,
        "status": status,
        "voltageLevel": voltage,
        "parameters": parameters,
        "parameterUnitsJson": json.dumps(
            units, ensure_ascii=False, sort_keys=True
        ),
    }


def _voltage_level(parameters: dict[str, Any]) -> str:
    for key in ("vn_kv", "vn_hv_kv", "vn_lv_kv"):
        value = parameters.get(key)
        if isinstance(value, (int, float)):
            return f"{value:g} kV"
    return ""


def _neo4j_value(value: Any) -> Any:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, tuple):
        return list(value)
    return value


def _validate_topology_identity(
    topology: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    for key in ("gridId", "topologyVersion", "schemaVersion"):
        if topology.get(key) != manifest.get(key):
            raise Neo4jProjectionError(
                f"topology.json与manifest.json的{key}不一致"
            )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Neo4jProjectionError(
            f"无法读取权威拓扑文件{path.name}：{exc}"
        ) from exc
