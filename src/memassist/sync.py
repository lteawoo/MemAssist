from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import ENFORCEMENTS, MEMORY_TYPES, Memory
from .storage import Store


SYNC_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ImportResult:
    imported: list[str]
    skipped: int

    def as_dict(self) -> dict[str, object]:
        return {"imported": self.imported, "skipped": self.skipped}


def export_memories(store: Store, *, project_id: str, path: Path, include_all: bool = False) -> int:
    memories = store.list_memories(
        project_id=project_id,
        include_global=False,
        status=None,
    )
    payload = {
        "schema_version": SYNC_SCHEMA_VERSION,
        "memories": [
            _exportable(memory)
            for memory in memories
            if include_all or memory.status == "active"
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(payload["memories"])


def import_memories(store: Store, *, project_id: str, path: Path, activate: bool = False) -> ImportResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_memories = payload.get("memories", []) if isinstance(payload, dict) else []
    existing = {
        _dedupe_key(memory)
        for memory in store.list_memories(project_id=project_id, include_global=False, status=None)
    }
    imported: list[str] = []
    skipped = 0
    for raw in raw_memories:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        memory = _normalize_import(raw)
        if not memory["content"]:
            skipped += 1
            continue
        key = (
            memory["type"],
            memory["content"],
            tuple(memory["tags"]),
            tuple(memory["paths"]),
        )
        if key in existing:
            skipped += 1
            continue
        memory_id = store.add_memory(
            scope_type="project",
            project_id=project_id,
            type=memory["type"],
            content=memory["content"],
            reason=memory["reason"],
            tags=memory["tags"],
            paths=memory["paths"],
            status="active" if activate else "candidate",
            importance=memory["importance"],
            confidence=memory["confidence"],
            strength=memory["strength"],
            recurrence=memory["recurrence"],
            retrieval_count=memory["retrieval_count"],
            utility=memory["utility"],
            half_life_days=memory["half_life_days"],
            enforcement=memory["enforcement"],
            source_kind="import",
            source_ref=str(path),
        )
        imported.append(memory_id)
        existing.add(key)
    return ImportResult(imported=imported, skipped=skipped)


def _exportable(memory: Memory) -> dict[str, object]:
    data = memory.as_dict()
    keep = [
        "type",
        "content",
        "reason",
        "tags",
        "paths",
        "status",
        "importance",
        "confidence",
        "strength",
        "recurrence",
        "retrieval_count",
        "utility",
        "half_life_days",
        "enforcement",
        "source_kind",
        "source_ref",
        "expires_at",
        "superseded_by",
    ]
    return {key: data[key] for key in keep}


def _dedupe_key(memory: Memory) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    return (memory.type, memory.content, tuple(memory.tags), tuple(memory.paths))


def _normalize_import(raw: dict[str, object]) -> dict[str, object]:
    memory_type = _string(raw.get("type"), "fact")
    enforcement = _string(raw.get("enforcement"), "none")
    return {
        "type": memory_type if memory_type in MEMORY_TYPES else "fact",
        "content": _string(raw.get("content"), ""),
        "reason": raw.get("reason") if isinstance(raw.get("reason"), str) else None,
        "tags": _string_list(raw.get("tags")),
        "paths": _string_list(raw.get("paths")),
        "importance": _float(raw.get("importance"), 0.5),
        "confidence": _float(raw.get("confidence"), 0.6),
        "strength": _float(raw.get("strength"), 0.6),
        "recurrence": int(_float(raw.get("recurrence"), 1)),
        "retrieval_count": int(_float(raw.get("retrieval_count"), 0)),
        "utility": _float(raw.get("utility"), 0.0),
        "half_life_days": _float(raw.get("half_life_days"), 30.0),
        "enforcement": enforcement if enforcement in ENFORCEMENTS else "none",
    }


def _string(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default
