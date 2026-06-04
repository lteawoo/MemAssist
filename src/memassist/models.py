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

CAUTION_LEVELS = {"none", "warn", "block"}


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
    caution_level: str
    source_kind: str
    source_ref: str | None
    created_at: str
    updated_at: str
    last_used_at: str | None
    expires_at: str | None
    superseded_by: str | None

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
            "caution_level": self.caution_level,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "expires_at": self.expires_at,
            "superseded_by": self.superseded_by,
        }
