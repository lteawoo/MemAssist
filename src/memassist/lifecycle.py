from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .storage import Store


@dataclass(frozen=True)
class CleanupResult:
    archived: list[str]
    duplicates: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"archived": self.archived, "duplicates": self.duplicates}


@dataclass(frozen=True)
class LifecycleResult:
    session_id: str | None
    stored: list[str]
    active: list[str]
    candidates: list[str]
    archived: list[str]
    duplicates: int
    decisions: list[dict[str, Any]]
    cleanup: CleanupResult

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "stored": self.stored,
            "active": self.active,
            "candidates": self.candidates,
            "archived": self.archived,
            "duplicates": self.duplicates,
            "decisions": self.decisions,
            "cleanup": self.cleanup.as_dict(),
        }


def process_session_lifecycle(
    store: Store,
    *,
    session_id: str | None,
    project_id: str | None,
) -> LifecycleResult:
    """Run maintenance lifecycle operations for a session.

    D5: Deterministic heuristic candidate extraction has been removed.
    Memory ingestion now happens exclusively through the isolated judge path
    (process_pending_memory_intents → store_judge_result), which includes
    conflict resolution and duplicate reinforcement before storage.

    This function now handles only operational maintenance:
    cleanup_memories handles expiry-based archival and exact duplicate cleanup.
    """
    cleanup = cleanup_memories(store)
    return LifecycleResult(
        session_id=session_id,
        stored=[],
        active=[],
        candidates=[],
        archived=[],
        duplicates=0,
        decisions=[],
        cleanup=cleanup,
    )


def cleanup_memories(store: Store) -> CleanupResult:
    now_dt = datetime.now(timezone.utc)
    memories = store.list_memories(include_global=False, status=None)
    archived: list[str] = []
    for memory in memories:
        if memory.status in {"active", "candidate"} and memory.expires_at:
            try:
                expires_at = datetime.fromisoformat(memory.expires_at)
            except ValueError:
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now_dt:
                store.update_status(memory.id, "archived")
                archived.append(memory.id)

    duplicates: list[str] = []
    active = [memory for memory in memories if memory.status == "active" and memory.id not in archived]
    insertion_order = _memory_insertion_order(store)
    by_key: dict[tuple[str | None, str], Any] = {}
    for memory in sorted(active, key=lambda item: (item.updated_at or "", item.created_at or "", insertion_order.get(item.id, 0))):
        key = (memory.project_id, memory.content.lower())
        newer = by_key.get(key)
        if newer is None:
            by_key[key] = memory
            continue
        store.supersede(newer.id, memory.id)
        duplicates.append(newer.id)
        by_key[key] = memory

    return CleanupResult(archived=archived, duplicates=duplicates)


def _memory_insertion_order(store: Store) -> dict[str, int]:
    try:
        rows = store.conn.execute("SELECT id FROM memories ORDER BY rowid ASC").fetchall()
    except Exception:
        return {}
    return {str(row["id"]): index for index, row in enumerate(rows)}
