from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from app.grid.online.diagnosis import diagnose_snapshot
from app.grid.simulation.engine import get_simulation_engine


class DiagnosisMonitorError(RuntimeError):
    pass


class DiagnosisMonitor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._state = "STOPPED"
        self._interval_seconds = 5.0
        self._run_count = 0
        self._last_diagnosis_id: str | None = None
        self._last_snapshot_id: str | None = None
        self._last_error: str | None = None
        self._started_at: str | None = None

    def start(self, interval_seconds: float = 5.0) -> dict[str, Any]:
        if interval_seconds < 1:
            raise DiagnosisMonitorError("周期研判间隔不能小于1秒")
        with self._lock:
            if self._state == "RUNNING":
                raise DiagnosisMonitorError("周期研判已经运行")
            self._interval_seconds = interval_seconds
            self._run_count = 0
            self._last_diagnosis_id = None
            self._last_snapshot_id = None
            self._last_error = None
            self._started_at = datetime.now(UTC).isoformat()
            self._stop.clear()
            self._state = "RUNNING"
            self._worker = threading.Thread(
                target=self._loop,
                name="dongjin-diagnosis-monitor",
                daemon=True,
            )
            self._worker.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            worker = self._worker
            self._stop.set()
        if worker and worker is not threading.current_thread():
            worker.join(timeout=3)
        with self._lock:
            self._state = "STOPPED"
            self._worker = None
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "intervalSeconds": self._interval_seconds,
                "runCount": self._run_count,
                "lastDiagnosisId": self._last_diagnosis_id,
                "lastSnapshotId": self._last_snapshot_id,
                "lastError": self._last_error,
                "startedAt": self._started_at,
                "workerAlive": bool(
                    self._worker and self._worker.is_alive()
                ),
            }

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                snapshot = get_simulation_engine().current_snapshot()
                snapshot_id = (
                    snapshot.get("snapshotId") if snapshot else None
                )
                if (
                    snapshot is not None
                    and snapshot_id != self._last_snapshot_id
                ):
                    result = diagnose_snapshot(snapshot)
                    with self._lock:
                        self._run_count += 1
                        self._last_snapshot_id = snapshot_id
                        self._last_diagnosis_id = result["diagnosisId"]
                        self._last_error = None
            except Exception as exc:
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"
            if self._stop.wait(self._interval_seconds):
                break
        with self._lock:
            if self._state == "RUNNING":
                self._state = "STOPPED"
            self._worker = None


_MONITOR = DiagnosisMonitor()


def get_diagnosis_monitor() -> DiagnosisMonitor:
    return _MONITOR
