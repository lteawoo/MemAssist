from __future__ import annotations

import re
from dataclasses import dataclass
from sqlite3 import Row
from typing import Any

from .session import summarize_session


@dataclass(frozen=True)
class MemoryCandidate:
    type: str
    content: str
    tags: list[str]
    importance: float
    confidence: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "content": self.content,
            "tags": self.tags,
            "importance": self.importance,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def extract_candidates(events: list[Row]) -> list[MemoryCandidate]:
    summary = summarize_session(events)
    candidates: list[MemoryCandidate] = []
    if summary.test_commands:
        commands = ", ".join(sorted(set(summary.test_commands)))
        candidates.append(
            MemoryCandidate(
                type="workflow",
                content=f"Use these verification command(s) when relevant: {commands}",
                tags=["verification", "test"],
                importance=0.7,
                confidence=0.7,
                reason="Observed successful or attempted test command in session trace.",
            )
        )
    if summary.files:
        tags = _tags_from_files(summary.files)
        candidates.append(
            MemoryCandidate(
                type="fact",
                content="Session touched project file(s): " + ", ".join(summary.files[:8]),
                tags=tags,
                importance=0.4,
                confidence=0.5,
                reason="Derived from tool trace files; archive unless explicitly promoted.",
            )
        )
    if summary.denied_events:
        candidates.append(
            MemoryCandidate(
                type="lesson",
                content="A policy-denied tool call occurred; review whether a persistent rule or workflow update is needed.",
                tags=["policy", "lesson"],
                importance=0.8,
                confidence=0.6,
                reason="Policy denial was recorded in the session trace.",
            )
        )
    return candidates


def store_candidates(events: list[Row], add_memory: Any, *, project_id: str | None) -> list[str]:
    session_id = str(events[0]["session_id"]) if events else None
    ids: list[str] = []
    for candidate in extract_candidates(events):
        memory_id = add_memory(
            scope_type="project" if project_id else "global",
            project_id=project_id,
            session_id=session_id,
            type=candidate.type,
            content=candidate.content,
            reason=candidate.reason,
            tags=candidate.tags,
            status="candidate",
            importance=candidate.importance,
            confidence=candidate.confidence,
            source_kind="extracted",
            source_ref=session_id,
        )
        ids.append(memory_id)
    return ids


def _tags_from_files(files: list[str]) -> list[str]:
    tags: set[str] = set()
    for file in files:
        for part in re.split(r"[/_.-]+", file):
            if part and len(part) > 2:
                tags.add(part.lower())
    return sorted(tags)[:8]


def _explicit_memory(message: str) -> MemoryCandidate | None:
    lowered = message.lower()
    triggers = ["remember", "기억", "앞으로", "always", "항상"]
    if not any(trigger in lowered for trigger in triggers):
        return None
    explicit_line = _explicit_line(message, triggers)
    if not explicit_line:
        return None
    return MemoryCandidate(
        type="preference",
        content=explicit_line[:300],
        tags=["explicit", "preference"],
        importance=0.8,
        confidence=0.8,
        reason="Assistant final message contained explicit memory-like wording.",
    )


def _explicit_line(message: str, triggers: list[str]) -> str:
    chunks: list[str] = []
    for line in message.splitlines():
        cleaned = line.strip().strip("-* ")
        if not cleaned:
            continue
        chunks.extend(part.strip() for part in re.split(r"(?<=[.!?。])\s+", cleaned) if part.strip())
    if not chunks:
        return ""
    for chunk in chunks:
        lowered = chunk.lower()
        if any(trigger in lowered for trigger in triggers):
            return chunk
    return chunks[0]
