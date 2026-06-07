from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .lifecycle import process_session_lifecycle
from .memory_judge import pending_memory_source_events, process_memory_intent_event
from .paths import project_memassist_home
from .project import Project
from .storage import Store

STOP_INGEST_MODE_ENV = "MEMASSIST_STOP_INGEST_MODE"
HOOK_MODE_ENV = "MEMASSIST_HOOK_MODE"
DAEMON_BATCH_LIMIT_ENV = "MEMASSIST_DAEMON_BATCH_LIMIT"
DAEMON_LOCK_TTL_ENV = "MEMASSIST_DAEMON_LOCK_TTL_SECONDS"

DEFAULT_STOP_INGEST_MODE = "async"
DEFAULT_DAEMON_BATCH_LIMIT = 3
DEFAULT_DAEMON_LOCK_TTL_SECONDS = 300

WORKER_LOCK_NAME = "daemon.lock"
WORKER_LOG_NAME = "daemon.log"


@dataclass(frozen=True)
class WorkerLockStatus:
    locked: bool
    stale: bool
    path: str
    age_seconds: float | None = None
    pid: int | None = None
    started_at: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "locked": self.locked,
            "stale": self.stale,
            "path": self.path,
            "age_seconds": self.age_seconds,
            "pid": self.pid,
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class WorkerSpawnResult:
    status: str
    session_id: str
    pid: int | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "session_id": self.session_id,
            "pid": self.pid,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AsyncIngestionResult:
    skipped: bool
    skip_reason: str | None
    processed: int
    stored_memory_ids: list[str]
    decisions: list[dict[str, object]]
    lock: WorkerLockStatus
    lifecycle: Any | None = None
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        if self.skipped:
            return f"skipped_{self.skip_reason or 'unknown'}"
        return "ok"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "processed": self.processed,
            "stored_memory_ids": self.stored_memory_ids,
            "decisions": self.decisions,
            "lock": self.lock.as_dict(),
            "lifecycle": self.lifecycle.as_dict() if self.lifecycle else None,
            "error": self.error,
        }


def stop_ingest_mode() -> str:
    raw = os.environ.get(STOP_INGEST_MODE_ENV, DEFAULT_STOP_INGEST_MODE).strip().lower()
    return raw if raw in {"async", "sync", "off"} else DEFAULT_STOP_INGEST_MODE


def hook_integration_mode() -> str:
    raw = os.environ.get(HOOK_MODE_ENV, "full").strip().lower()
    return raw if raw in {"full", "context", "trace"} else "full"


def hook_mode() -> str:
    return hook_integration_mode()


def daemon_batch_limit() -> int:
    return _bounded_int(os.environ.get(DAEMON_BATCH_LIMIT_ENV), default=DEFAULT_DAEMON_BATCH_LIMIT, minimum=1, maximum=50)


def async_batch_limit() -> int:
    return daemon_batch_limit()


def async_ingestion_enabled_for_hook() -> bool:
    return stop_ingest_mode() == "async"


def sync_ingestion_enabled_for_hook() -> bool:
    return stop_ingest_mode() == "sync"


def daemon_lock_ttl_seconds() -> int:
    return _bounded_int(
        os.environ.get(DAEMON_LOCK_TTL_ENV),
        default=DEFAULT_DAEMON_LOCK_TTL_SECONDS,
        minimum=1,
        maximum=24 * 60 * 60,
    )


def worker_lock_path(memassist_dir: Path) -> Path:
    return memassist_dir / WORKER_LOCK_NAME


def worker_log_path(memassist_dir: Path) -> Path:
    return memassist_dir / WORKER_LOG_NAME


def worker_lock_status(memassist_dir: Path, *, ttl_seconds: int | None = None) -> WorkerLockStatus:
    path = worker_lock_path(memassist_dir)
    ttl = daemon_lock_ttl_seconds() if ttl_seconds is None else ttl_seconds
    if not path.exists():
        return WorkerLockStatus(locked=False, stale=False, path=str(path))
    info = _read_lock(path)
    started_at = _float_or_none(info.get("started_at"))
    if started_at is None:
        try:
            started_at = path.stat().st_mtime
        except OSError:
            started_at = time.time() - ttl - 1
    age = max(0.0, time.time() - started_at)
    stale = bool(age is not None and age > ttl)
    pid = _int_or_none(info.get("pid"))
    return WorkerLockStatus(locked=not stale, stale=stale, path=str(path), age_seconds=age, pid=pid, started_at=started_at)


def pending_source_count(store: Store, *, project_id: str, session_id: str | None = None) -> int:
    return len(pending_memory_source_events(store, project_id=project_id, session_id=session_id, limit=10_000))


def recent_worker_failures(store: Store, *, project_id: str, limit: int = 3) -> list[dict[str, object]]:
    rows = store.conn.execute(
        """
        SELECT id, session_id, input_json, created_at
        FROM trace_events
        WHERE project_id = ? AND event_type = 'async_ingestion_worker_failed'
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()
    failures: list[dict[str, object]] = []
    for row in rows:
        failures.append(
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "created_at": row["created_at"],
                "input": _json_dict(row["input_json"]),
            }
        )
    return failures


def spawn_async_ingestion_worker(project: Project, *, session_id: str) -> WorkerSpawnResult:
    mem_dir = project_memassist_home(project.root)
    mem_dir.mkdir(parents=True, exist_ok=True)
    lock = worker_lock_status(mem_dir)
    if lock.locked:
        return WorkerSpawnResult(status="skipped_locked", session_id=session_id, detail=f"worker already active: {lock.path}")

    env = os.environ.copy()
    env["MEMASSIST_HOME"] = str(mem_dir)
    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "memassist").exists():
        current = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{source_root}{os.pathsep}{current}" if current else str(source_root)

    command = [
        sys.executable,
        "-m",
        "memassist",
        "daemon",
        "once",
        "--session",
        "all",
        "--quiet",
        "--json",
    ]
    log_path = worker_log_path(mem_dir)
    try:
        log_file = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=str(project.root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            close_fds=True,
            start_new_session=True,
        )
    except Exception as exc:  # pragma: no cover - defensive path exercised through result assertions.
        _append_worker_log(mem_dir, {"event": "spawn_failed", "session_id": session_id, "error": str(exc)})
        return WorkerSpawnResult(status="failed", session_id=session_id, detail=str(exc))
    finally:
        try:
            log_file.close()  # type: ignore[possibly-undefined]
        except Exception:
            pass
    return WorkerSpawnResult(status="spawned", session_id=session_id, pid=process.pid, detail="detached daemon once started")


def run_async_ingestion_worker(
    store: Store,
    *,
    project: Project,
    session_id: str | None,
    limit: int | None = None,
) -> AsyncIngestionResult:
    mem_dir = store.path.parent
    batch_limit = daemon_batch_limit() if limit is None else max(1, limit)
    lock_snapshot = worker_lock_status(mem_dir)
    with acquire_worker_lock(mem_dir) as acquired:
        if not acquired:
            return AsyncIngestionResult(
                skipped=True,
                skip_reason="locked",
                processed=0,
                stored_memory_ids=[],
                decisions=[],
                lock=lock_snapshot,
            )
        active_lock = worker_lock_status(mem_dir)
        events = pending_memory_source_events(store, project_id=project.id, session_id=session_id, limit=batch_limit)
        stored: list[str] = []
        decisions: list[dict[str, object]] = []
        for event in events:
            result = process_memory_intent_event(
                store,
                project=project,
                session_id=str(event["session_id"]),
                source_event_id=str(event["id"]),
            )
            if result.memory_id:
                stored.append(result.memory_id)
            decisions.append(
                {
                    "source_event_id": str(event["id"]),
                    "session_id": str(event["session_id"]),
                    "event_id": result.event_id,
                    "memory_id": result.memory_id,
                    "decision": result.decision,
                }
            )
        return AsyncIngestionResult(
            skipped=False,
            skip_reason=None,
            processed=len(events),
            stored_memory_ids=stored,
            decisions=decisions,
            lock=active_lock,
        )


def run_async_ingestion_once(
    store: Store,
    *,
    project: Project,
    session_id: str | None,
    batch_limit: int | None = None,
) -> AsyncIngestionResult:
    trace_session_id = session_id or "project_async_ingestion"
    if trace_session_id:
        store.add_trace_event(
            session_id=trace_session_id,
            project_id=project.id,
            event_type="async_ingestion_worker_started",
            tool_name="memassist",
            input_json={"batch_limit": batch_limit or daemon_batch_limit()},
        )
    try:
        result = run_async_ingestion_worker(store, project=project, session_id=session_id, limit=batch_limit)
        lifecycle = None
        if not result.skipped:
            lifecycle_session_ids = [session_id] if session_id else sorted(
                {
                    str(decision.get("session_id") or "")
                    for decision in result.decisions
                    if decision.get("session_id")
                }
            )
            for lifecycle_session_id in lifecycle_session_ids:
                lifecycle = process_session_lifecycle(store, session_id=lifecycle_session_id, project_id=project.id)
        final = AsyncIngestionResult(
            skipped=result.skipped,
            skip_reason=result.skip_reason,
            processed=result.processed,
            stored_memory_ids=result.stored_memory_ids,
            decisions=result.decisions,
            lock=result.lock,
            lifecycle=lifecycle,
        )
        if trace_session_id:
            store.add_trace_event(
                session_id=trace_session_id,
                project_id=project.id,
                event_type="async_ingestion_worker_completed",
                tool_name="memassist",
                input_json=final.as_dict(),
            )
        return final
    except Exception as exc:
        if trace_session_id:
            store.add_trace_event(
                session_id=trace_session_id,
                project_id=project.id,
                event_type="async_ingestion_worker_failed",
                tool_name="memassist",
                input_json={"error": str(exc), "batch_limit": batch_limit or daemon_batch_limit()},
            )
        return AsyncIngestionResult(
            skipped=False,
            skip_reason=None,
            processed=0,
            stored_memory_ids=[],
            decisions=[],
            lock=worker_lock_status(store.path.parent),
            lifecycle=None,
            error=str(exc),
        )


def async_ingestion_diagnostics(store: Store, *, project: Project) -> dict[str, object]:
    lock = worker_lock_status(store.path.parent)
    if lock.locked:
        lock_detail = "active"
    elif lock.stale:
        lock_detail = "stale"
    else:
        lock_detail = "free"
    failures = recent_worker_failures(store, project_id=project.id, limit=3)
    return {
        "mode": stop_ingest_mode(),
        "batch_limit": daemon_batch_limit(),
        "pending_sources": pending_source_count(store, project_id=project.id),
        "lock": {
            **lock.as_dict(),
            "detail": lock_detail,
        },
        "last_failure": failures[0] if failures else None,
        "recent_failures": failures,
    }


@contextmanager
def acquire_worker_lock(memassist_dir: Path, *, ttl_seconds: int | None = None) -> Iterator[bool]:
    path = worker_lock_path(memassist_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    ttl = daemon_lock_ttl_seconds() if ttl_seconds is None else ttl_seconds
    acquired = False
    try:
        while True:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                status = worker_lock_status(memassist_dir, ttl_seconds=ttl)
                if status.stale:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        continue
                    continue
                yield False
                return
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(json.dumps({"pid": os.getpid(), "started_at": time.time()}, sort_keys=True))
            acquired = True
            yield True
            return
    finally:
        if acquired:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _append_worker_log(memassist_dir: Path, record: dict[str, object]) -> None:
    try:
        path = worker_log_path(memassist_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return


def _read_lock(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_dict(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _bounded_int(value: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(str(value or ""))
    except ValueError:
        return default
    if number < minimum:
        return minimum
    if number > maximum:
        return maximum
    return number


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
