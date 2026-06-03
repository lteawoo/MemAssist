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
    expired: list[str]
    stale: list[str]
    superseded: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"expired": self.expired, "stale": self.stale, "superseded": self.superseded}


@dataclass(frozen=True)
class LifecycleResult:
    session_id: str | None
    stored: list[str]
    auto_active: list[str]
    candidates: list[str]
    ephemeral: list[str]
    rejected: list[str]
    duplicates: int
    decisions: list[dict[str, Any]]
    cleanup: CleanupResult

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "stored": self.stored,
            "auto_active": self.auto_active,
            "candidates": self.candidates,
            "ephemeral": self.ephemeral,
            "rejected": self.rejected,
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
    auto_active: list[str] = []
    candidates: list[str] = []
    ephemeral: list[str] = []
    rejected: list[str] = []
    decisions: list[dict[str, Any]] = []
    duplicates = 0

    for candidate in extract_candidates(events):
        existing = store.list_memories(project_id=project_id, include_global=True)
        decision = evaluate_candidate(candidate, existing_memories=existing)
        duplicate = store.find_memory(
            project_id=project_id,
            content=candidate.content,
            type=candidate.type,
            statuses=(
                "candidate",
                "draft",
                "auto_active",
                "active",
                "policy_active",
                "pinned",
                "ephemeral",
                "long_term",
            ),
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
            candidate_enforcement=decision.enforcement,
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

        memory_id = _store_decision(store, decision, session_id=session_id, project_id=project_id)
        store.link_related_memories(memory_id, project_id=project_id)
        stored.append(memory_id)
        if decision.status == "auto_active":
            auto_active.append(memory_id)
        elif decision.status == "candidate":
            candidates.append(memory_id)
        elif decision.status == "ephemeral":
            ephemeral.append(memory_id)
        elif decision.status == "rejected":
            rejected.append(memory_id)
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
        auto_active=auto_active,
        candidates=candidates,
        ephemeral=ephemeral,
        rejected=rejected,
        duplicates=duplicates,
        decisions=decisions,
        cleanup=cleanup,
    )


def cleanup_memories(store: Store) -> CleanupResult:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = store.conn.execute(
        """
        SELECT id FROM memories
        WHERE expires_at IS NOT NULL
          AND expires_at <= ?
          AND status NOT IN ('expired', 'deleted', 'disabled')
        """,
        (now,),
    ).fetchall()
    expired = [str(row["id"]) for row in rows]
    for memory_id in expired:
        store.update_status(memory_id, "expired")
    stale_rows = store.conn.execute(
        """
        SELECT id FROM memories
        WHERE status = 'candidate'
          AND updated_at <= datetime('now', '-14 days')
        """
    ).fetchall()
    stale = [str(row["id"]) for row in stale_rows]
    for memory_id in stale:
        store.update_status(memory_id, "stale")

    duplicate_rows = store.conn.execute(
        """
        SELECT old.id AS old_id, new.id AS new_id
        FROM memories old
        JOIN memories new
          ON old.project_id IS new.project_id
         AND old.type = new.type
         AND lower(old.content) = lower(new.content)
         AND old.id != new.id
        WHERE old.status IN ('active', 'auto_active', 'long_term')
          AND new.status IN ('active', 'auto_active', 'long_term')
          AND old.updated_at < new.updated_at
        """
    ).fetchall()
    superseded: list[str] = []
    for row in duplicate_rows:
        memory_id = str(row["old_id"])
        if memory_id in superseded:
            continue
        store.supersede(memory_id, str(row["new_id"]))
        superseded.append(memory_id)
    semantic_candidates = store.conn.execute(
        """
        SELECT * FROM memories
        WHERE status = 'candidate'
        ORDER BY updated_at ASC
        """
    ).fetchall()
    for row in semantic_candidates:
        candidate = store._row_to_memory(row)
        if not candidate:
            continue
        existing = [
            memory
            for memory in store.list_memories(project_id=candidate.project_id, include_global=True, status=None)
            if memory.id != candidate.id
        ]
        duplicate = find_semantic_duplicate(
            candidate.content,
            candidate_type=candidate.type,
            candidate_tags=candidate.tags,
            candidate_status=candidate.status,
            candidate_enforcement=candidate.enforcement,
            existing_memories=existing,
        )
        if duplicate and should_suppress_candidate(candidate.status, duplicate.memory):
            store.supersede(candidate.id, duplicate.memory.id)
            superseded.append(candidate.id)
    return CleanupResult(expired=expired, stale=stale, superseded=superseded)


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
        enforcement=decision.enforcement,
        source_kind="lifecycle",
        source_ref=session_id,
    )
