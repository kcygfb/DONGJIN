from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.grid.artifact_service import (
    GridPackageError,
    get_active_grid_package,
    get_grid_validation,
    initialize_grid_package,
)
from app.grid.publishers.neo4j import (
    Neo4jProjectionError,
    publish_active_grid_to_neo4j,
)


router = APIRouter(prefix="/grids", tags=["standard-grid"])


class GridInitializationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    simbench_code: str | None = Field(default=None, alias="simbenchCode")
    topology_version: str = Field(default="v1", alias="topologyVersion")
    force: bool = False


@router.post("/initialize")
def initialize_grid(request: GridInitializationRequest | None = None):
    request = request or GridInitializationRequest()
    try:
        return initialize_grid_package(
            simbench_code=request.simbench_code,
            topology_version=request.topology_version,
            force=request.force,
        )
    except GridPackageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/active")
def active_grid():
    try:
        return get_active_grid_package()
    except GridPackageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{grid_id}/validation")
def grid_validation(
    grid_id: str,
    topology_version: Annotated[str, Query(alias="topologyVersion")] = "v1",
):
    try:
        return get_grid_validation(grid_id, topology_version)
    except GridPackageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{grid_id}/publish/neo4j")
def publish_grid_to_neo4j(grid_id: str):
    try:
        return publish_active_grid_to_neo4j(grid_id)
    except Neo4jProjectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
