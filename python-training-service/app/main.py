from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.grid.api import router as grid_router
from app.grid.health import build_grid_health
from app.grid.offline_api import router as offline_training_router
from app.grid.online.api import router as online_diagnosis_router
from app.grid.online.model_manager import get_selected_model
from app.grid.online.monitor import get_diagnosis_monitor
from app.grid.simulation.api import router as grid_runtime_router
from app.grid.simulation.engine import get_simulation_engine


app = FastAPI(
    title="Dongjin Python Compute Service",
    version="3.0.0",
    description=(
        "提供SimBench/pandapower标准电网、白箱离线数据、"
        "48维在线推理、影子错误与独立短路分析。"
    ),
)
app.include_router(grid_router)
app.include_router(grid_runtime_router)
app.include_router(offline_training_router)
app.include_router(online_diagnosis_router)


@app.on_event("shutdown")
def shutdown_services() -> None:
    get_diagnosis_monitor().stop()
    get_simulation_engine().shutdown()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "serviceVersion": "3.0.0",
        "primaryModelType": "GCN_NODE_CLASSIFIER_48_FEATURES",
        "selectedInferenceModel": get_selected_model(required=False),
        "diagnosisMonitor": get_diagnosis_monitor().status(),
        "gridData": build_grid_health(),
    }
