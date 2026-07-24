from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from redis import Redis

from app.grid.settings import GridSettings, get_grid_settings
from app.grid.simulation.models import GridSnapshot


ACTIVE_GRID_KEY = "dongjin:grid:active"
ACTIVE_SNAPSHOT_KEY = "dongjin:snapshot:active"
SIMULATION_STATUS_KEY = "dongjin:simulation:status"
SNAPSHOT_KEY_PREFIX = "dongjin:snapshot:"


class RedisSnapshotError(RuntimeError):
    pass


class RedisSnapshotPublisher:
    def __init__(self, settings: GridSettings | None = None) -> None:
        self.settings = settings or get_grid_settings()

    def publish(
        self,
        snapshot: GridSnapshot,
    ) -> tuple[dict[str, Any], dict[str, float]]:
        payload = snapshot.model_dump(by_alias=True)
        payload["publishedAt"] = datetime.now(UTC).isoformat()
        serialization_started = time.perf_counter()
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        serialization_ms = (
            time.perf_counter() - serialization_started
        ) * 1000
        payload["performance"]["serializationDurationMs"] = round(
            serialization_ms, 3
        )
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

        publish_started = time.perf_counter()
        client = self._client()
        snapshot_key = f"{SNAPSHOT_KEY_PREFIX}{snapshot.snapshot_id}"
        try:
            pipeline = client.pipeline(transaction=True)
            pipeline.set(
                snapshot_key,
                serialized,
                ex=self.settings.snapshot_ttl_seconds,
            )
            pipeline.set(
                ACTIVE_GRID_KEY,
                json.dumps(
                    {
                        "gridId": snapshot.grid_id,
                        "topologyVersion": snapshot.topology_version,
                        "schemaVersion": snapshot.schema_version,
                        "snapshotId": snapshot.snapshot_id,
                        "updatedAt": datetime.now(UTC).isoformat(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            pipeline.set(
                ACTIVE_SNAPSHOT_KEY,
                snapshot.snapshot_id,
                ex=self.settings.snapshot_ttl_seconds,
            )
            pipeline.execute()
        except Exception as exc:
            raise RedisSnapshotError(
                f"Redis快照发布失败：{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            client.close()
        publish_ms = (time.perf_counter() - publish_started) * 1000
        return payload, {
            "serializationDurationMs": round(serialization_ms, 3),
            "redisPublishDurationMs": round(publish_ms, 3),
        }

    def current(self) -> dict[str, Any] | None:
        client = self._client()
        try:
            snapshot_id = client.get(ACTIVE_SNAPSHOT_KEY)
            if not snapshot_id:
                return None
            serialized = client.get(f"{SNAPSHOT_KEY_PREFIX}{snapshot_id}")
            if not serialized:
                client.delete(ACTIVE_SNAPSHOT_KEY)
                return None
            return json.loads(serialized)
        except Exception as exc:
            raise RedisSnapshotError(
                f"Redis当前快照读取失败：{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            client.close()

    def get(self, snapshot_id: str) -> dict[str, Any] | None:
        if not snapshot_id or any(
            character.isspace() for character in snapshot_id
        ):
            raise RedisSnapshotError("snapshotId格式无效")
        client = self._client()
        try:
            serialized = client.get(
                f"{SNAPSHOT_KEY_PREFIX}{snapshot_id}"
            )
            return json.loads(serialized) if serialized else None
        except Exception as exc:
            raise RedisSnapshotError(
                f"Redis指定快照读取失败：{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            client.close()

    def write_simulation_status(self, status: dict[str, Any]) -> None:
        client = self._client()
        try:
            client.set(
                SIMULATION_STATUS_KEY,
                json.dumps(
                    status,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            )
        except Exception as exc:
            raise RedisSnapshotError(
                f"Redis仿真状态写入失败：{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            client.close()

    def _client(self) -> Redis:
        return Redis.from_url(
            self.settings.redis_url.get_secret_value(),
            socket_connect_timeout=self.settings.health_timeout_seconds,
            socket_timeout=self.settings.health_timeout_seconds,
            decode_responses=True,
        )
