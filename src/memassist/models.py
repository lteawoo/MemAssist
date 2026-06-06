from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MEMORY_TYPES = {
    "fact",
    "preference",
    "rule",
    "decision",
    "lesson",
    "workflow",
    "open_thread",
    "directive",
}

MEMORY_STATUSES = {
    "candidate",
    "active",
    "archived",
}

MEMORY_CHUNK_KINDS = {
    "content",
    "source_quote",
    "reason",
    "raw_source",
}


@dataclass(frozen=True)
class Memory:
    id: str
    scope_type: str
    project_id: str | None
    session_id: str | None
    type: str
    content: str
    reason: str | None
    tags: list[str]
    paths: list[str]
    status: str
    importance: float
    confidence: float
    strength: float
    recurrence: int
    retrieval_count: int
    utility: float
    half_life_days: float
    source_kind: str
    source_ref: str | None
    created_at: str
    updated_at: str
    last_used_at: str | None
    expires_at: str | None
    superseded_by: str | None
    source_quote: str | None = None
    source_ids: list[str] | None = None
    content_hash: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope_type": self.scope_type,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "type": self.type,
            "content": self.content,
            "reason": self.reason,
            "tags": self.tags,
            "paths": self.paths,
            "status": self.status,
            "importance": self.importance,
            "confidence": self.confidence,
            "strength": self.strength,
            "recurrence": self.recurrence,
            "retrieval_count": self.retrieval_count,
            "utility": self.utility,
            "half_life_days": self.half_life_days,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "expires_at": self.expires_at,
            "superseded_by": self.superseded_by,
            "source_quote": self.source_quote,
            "source_ids": self.source_ids or [],
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class MemoryChunk:
    id: str
    memory_id: str
    project_id: str | None
    scope_type: str
    status: str
    chunk_index: int
    chunk_kind: str
    content: str
    content_hash: str
    tags: list[str]
    paths: list[str]
    source_ids: list[str]
    source_ref: str | None
    start_offset: int | None
    end_offset: int | None
    created_at: str
    indexed_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "memory_id": self.memory_id,
            "project_id": self.project_id,
            "scope_type": self.scope_type,
            "status": self.status,
            "chunk_index": self.chunk_index,
            "chunk_kind": self.chunk_kind,
            "content": self.content,
            "content_hash": self.content_hash,
            "tags": self.tags,
            "paths": self.paths,
            "source_ids": self.source_ids,
            "source_ref": self.source_ref,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "created_at": self.created_at,
            "indexed_at": self.indexed_at,
        }
