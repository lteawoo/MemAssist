from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .classifier import CandidateDecision
from .dedupe import find_semantic_duplicate, should_suppress_candidate
from .evaluator import evaluate_candidate
from .extraction import extract_candidates
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
    events = store.trace_events(session_id) if session_id else []
    stored: list[str] = []
    active: list[str] = []
    candidates: list[str] = []
    archived: list[str] = []
    decisions: list[dict[str, Any]] = []
    duplicates = 0

    for candidate in extract_candidates(events):
        existing = store.list_memories(project_id=project_id, include_global=True)
        decision = evaluate_candidate(candidate, existing_memories=existing)
        duplicate = store.find_memory(
            project_id=project_id,
            content=candidate.content,
            type=candidate.type,
            statuses=("candidate", "active"),
        )
        if duplicate:
            duplicates += 1
            reinforced = store.reinforce_memory(duplicate.id)
            store.add_lifecycle_event(
                session_id=session_id,
                project_id=project_id,
                memory_id=duplicate.id,
                candidate=candidate.as_dict(),
                decision="reinforce_duplicate",
                risk=decision.risk,
                reason=(
                    "Equivalent memory already exists for this project; confidence and importance were reinforced."
                    if reinforced
                    else "Equivalent memory already exists for this project."
                ),
            )
            continue
        semantic_duplicate = find_semantic_duplicate(
            candidate.content,
            candidate_type=candidate.type,
            candidate_tags=candidate.tags,
            candidate_status=decision.status,
            candidate_caution_level=decision.caution_level,
            existing_memories=existing,
        )
        if semantic_duplicate and should_suppress_candidate(decision.status, semantic_duplicate.memory):
            duplicates += 1
            reinforced = store.reinforce_memory(semantic_duplicate.memory.id, confidence_delta=0.02, importance_delta=0.01)
            store.add_lifecycle_event(
                session_id=session_id,
                project_id=project_id,
                memory_id=semantic_duplicate.memory.id,
                candidate=candidate.as_dict(),
                decision="suppress_semantic_duplicate",
                risk=decision.risk,
                reason=(
                    f"Weaker candidate duplicates stronger memory {semantic_duplicate.memory.id}; "
                    f"{semantic_duplicate.reason} (score={semantic_duplicate.score})."
                    if reinforced
                    else f"Weaker candidate duplicates stronger memory {semantic_duplicate.memory.id}."
                ),
            )
            continue

        memory_id: str | None = None
        if decision.status in {"active", "candidate"}:
            memory_id = _store_decision(store, decision, session_id=session_id, project_id=project_id)
            store.link_related_memories(memory_id, project_id=project_id)
            stored.append(memory_id)
            if decision.status == "active":
                active.append(memory_id)
            else:
                candidates.append(memory_id)
        else:
            archived.append(candidate.content)
        store.add_lifecycle_event(
            session_id=session_id,
            project_id=project_id,
            memory_id=memory_id,
            candidate=candidate.as_dict(),
            decision=decision.decision,
            risk=decision.risk,
            reason=decision.reason,
        )
        decisions.append({"memory_id": memory_id, **decision.as_dict()})

    cleanup = cleanup_memories(store)
    return LifecycleResult(
        session_id=session_id,
        stored=stored,
        active=active,
        candidates=candidates,
        archived=archived,
        duplicates=duplicates,
        decisions=decisions,
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
    by_key: dict[tuple[str | None, str, str], Any] = {}
    for memory in sorted(active, key=lambda item: item.updated_at or ""):
        key = (memory.project_id, memory.type, memory.content.lower())
        newer = by_key.get(key)
        if newer is None:
            by_key[key] = memory
            continue
        store.supersede(newer.id, memory.id)
        duplicates.append(newer.id)
        by_key[key] = memory

    for candidate in [memory for memory in memories if memory.status == "candidate"]:
        existing = [
            memory
            for memory in store.list_memories(project_id=candidate.project_id, include_global=True, status=None)
            if memory.id != candidate.id and memory.status == "active"
        ]
        duplicate = find_semantic_duplicate(
            candidate.content,
            candidate_type=candidate.type,
            candidate_tags=candidate.tags,
            candidate_status=candidate.status,
            candidate_caution_level=candidate.caution_level,
            existing_memories=existing,
        )
        if duplicate and should_suppress_candidate(candidate.status, duplicate.memory):
            store.supersede(candidate.id, duplicate.memory.id)
            duplicates.append(candidate.id)
    return CleanupResult(archived=archived, duplicates=duplicates)


def _store_decision(
    store: Store,
    decision: CandidateDecision,
    *,
    session_id: str | None,
    project_id: str | None,
) -> str:
    candidate = decision.candidate
    return store.add_memory(
        scope_type="project" if project_id else "global",
        project_id=project_id,
        session_id=session_id,
        type=candidate.type,
        content=candidate.content,
        reason=f"{candidate.reason} Lifecycle: {decision.reason}",
        tags=[*candidate.tags, decision.memory_kind, decision.risk],
        status=decision.status,
        importance=candidate.importance,
        confidence=candidate.confidence,
        caution_level=decision.caution_level,
        source_kind="lifecycle",
        source_ref=session_id,
    )
