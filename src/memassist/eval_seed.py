from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid

from .models import ENFORCEMENTS, MEMORY_STATUSES
from .storage import Store


@dataclass(frozen=True)
class SeedMemory:
    type: str
    content: str
    tags: list[str]
    paths: list[str]
    status: str
    importance: float
    confidence: float
    enforcement: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SeedMemory":
        status = str(value.get("status") or "active")
        enforcement = str(value.get("enforcement") or "none")
        return cls(
            type=str(value.get("type") or "fact"),
            content=str(value.get("content", "")),
            tags=_string_list(value.get("tags")),
            paths=_string_list(value.get("paths")),
            status=status if status in MEMORY_STATUSES else "active",
            importance=float(value.get("importance", 0.8)),
            confidence=float(value.get("confidence", 0.85)),
            enforcement=enforcement if enforcement in ENFORCEMENTS else "none",
        )


def seed_from_value(value: object) -> list[SeedMemory]:
    if not isinstance(value, list):
        return []
    return [SeedMemory.from_dict(item) for item in value if isinstance(item, dict)]


def insert_seed_memories(store: Store, *, project_id: str, seed: list[SeedMemory], source_kind: str) -> list[str]:
    ids: list[str] = []
    for memory in seed:
        if not memory.content:
            continue
        memory_id = store.add_memory(
            scope_type="project",
            project_id=project_id,
            type=memory.type,
            content=memory.content,
            reason=f"Temporary memory inserted by {source_kind} fixture.",
            tags=memory.tags,
            paths=memory.paths,
            status=memory.status,
            importance=memory.importance,
            confidence=memory.confidence,
            enforcement=memory.enforcement,
            source_kind=source_kind,
            source_ref=f"{source_kind}:{uuid.uuid4().hex[:12]}",
        )
        ids.append(memory_id)
    return ids


def remove_seed_memories(store: Store, memory_ids: list[str]) -> None:
    if not memory_ids:
        return
    placeholders = ", ".join("?" for _ in memory_ids)
    store.conn.execute(f"DELETE FROM memory_links WHERE source_id IN ({placeholders})", memory_ids)
    store.conn.execute(f"DELETE FROM memory_links WHERE target_id IN ({placeholders})", memory_ids)
    store.conn.execute(f"DELETE FROM memory_fts WHERE memory_id IN ({placeholders})", memory_ids)
    store.conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", memory_ids)
    store.conn.commit()


def remove_seed_memories_by_source(store: Store, *, project_id: str, source_kind: str) -> None:
    rows = store.conn.execute(
        "SELECT id FROM memories WHERE project_id = ? AND source_kind = ?",
        (project_id, source_kind),
    ).fetchall()
    remove_seed_memories(store, [str(row["id"]) for row in rows])


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []
