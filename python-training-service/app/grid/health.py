from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from neo4j import GraphDatabase
from redis import Redis

from app.grid.artifact_service import GridPackageError, get_active_grid_package
from app.grid.settings import GridSettings, get_grid_settings


def build_grid_health(settings: GridSettings | None = None) -> dict[str, Any]:
    settings = settings or get_grid_settings()
    components = {
        "pandapower": _package_health("pandapower"),
        "simbench": _package_health("simbench"),
    }
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="grid-health") as executor:
        neo4j_future = executor.submit(_neo4j_health, settings)
        redis_future = executor.submit(_redis_health, settings)
        components["neo4j"] = neo4j_future.result()
        components["redis"] = redis_future.result()

    return {
        "status": (
            "ok"
            if all(item["status"] == "ok" for item in components.values())
            else "degraded"
        ),
        "activeGrid": _active_grid_health(settings),
        "components": components,
    }


def _active_grid_health(settings: GridSettings) -> dict[str, Any]:
    try:
        active = get_active_grid_package(settings)
    except GridPackageError:
        return {
            "status": "not_initialized",
            "gridId": settings.grid_id,
            "simbenchCode": settings.simbench_code,
            "topologyVersion": None,
        }
    return {
        "status": "ready",
        "gridId": active["gridId"],
        "simbenchCode": active["simbenchCode"],
        "topologyVersion": active["topologyVersion"],
        "validationStatus": active["validation"]["status"],
    }


def _package_health(distribution: str) -> dict[str, Any]:
    try:
        installed_version = version(distribution)
    except PackageNotFoundError:
        return {
            "status": "not_installed",
            "version": None,
            "detail": f"Python distribution '{distribution}' is not installed",
        }
    try:
        import_module(distribution)
    except Exception as exc:
        return {
            "status": "unavailable",
            "version": installed_version,
            "detail": _safe_error(exc),
        }
    return {"status": "ok", "version": installed_version, "detail": None}


def _neo4j_health(settings: GridSettings) -> dict[str, Any]:
    try:
        with GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(
                settings.neo4j_username,
                settings.neo4j_password.get_secret_value(),
            ),
            connection_timeout=settings.health_timeout_seconds,
        ) as driver:
            driver.verify_connectivity()
        return {"status": "ok", "version": _safe_version("neo4j"), "detail": None}
    except Exception as exc:
        return {
            "status": "unavailable",
            "version": _safe_version("neo4j"),
            "detail": _safe_error(exc),
        }


def _redis_health(settings: GridSettings) -> dict[str, Any]:
    client: Redis | None = None
    try:
        client = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=settings.health_timeout_seconds,
            socket_timeout=settings.health_timeout_seconds,
            decode_responses=True,
        )
        if client.ping() is not True:
            raise ConnectionError("Redis PING did not return true")
        return {"status": "ok", "version": _safe_version("redis"), "detail": None}
    except Exception as exc:
        return {
            "status": "unavailable",
            "version": _safe_version("redis"),
            "detail": _safe_error(exc),
        }
    finally:
        if client is not None:
            client.close()


def _safe_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
