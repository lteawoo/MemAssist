from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid

from .models import MEMORY_STATUSES
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
    source_quote: str | None = None
    source_ref: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SeedMemory":
        status = str(value.get("status") or "active")
        return cls(
            type=str(value.get("type") or "fact"),
            content=str(value.get("content", "")),
            tags=_string_list(value.get("tags")),
            paths=_string_list(value.get("paths")),
            status=status if status in MEMORY_STATUSES else "archived",
            importance=float(value.get("importance", 0.8)),
            confidence=float(value.get("confidence", 0.85)),
            source_quote=value.get("source_quote") if isinstance(value.get("source_quote"), str) else None,
            source_ref=value.get("source_ref") if isinstance(value.get("source_ref"), str) else None,
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
            source_kind=source_kind,
            source_ref=memory.source_ref or f"{source_kind}:{uuid.uuid4().hex[:12]}",
            source_quote=memory.source_quote,
        )
        ids.append(memory_id)
    return ids


def remove_seed_memories(store: Store, memory_ids: list[str]) -> None:
    if not memory_ids:
        return
    for memory_id in memory_ids:
        store.delete_memory(memory_id)


def remove_seed_memories_by_source(store: Store, *, project_id: str, source_kind: str) -> None:
    ids = [
        memory.id
        for memory in store.list_memories(project_id=project_id, include_global=False, status=None)
        if memory.source_kind == source_kind
    ]
    remove_seed_memories(store, ids)


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []
