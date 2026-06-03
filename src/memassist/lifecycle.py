from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .classifier import CandidateDecision, classify_candidate
from .extraction import extract_candidates
from .storage import Store


@dataclass(frozen=True)
class CleanupResult:
    expired: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"expired": self.expired}


@dataclass(frozen=True)
class LifecycleResult:
    session_id: str | None
    stored: list[str]
    auto_active: list[str]
    pending_confirmation: list[str]
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
            "pending_confirmation": self.pending_confirmation,
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
    pending_confirmation: list[str] = []
    ephemeral: list[str] = []
    rejected: list[str] = []
    decisions: list[dict[str, Any]] = []
    duplicates = 0

    for candidate in extract_candidates(events):
        decision = classify_candidate(candidate)
        if store.memory_exists(
            project_id=project_id,
            content=candidate.content,
            type=candidate.type,
            statuses=(
                "candidate",
                "draft",
                "auto_active",
                "pending_confirmation",
                "active",
                "policy_active",
                "pinned",
                "ephemeral",
            ),
        ):
            duplicates += 1
            store.add_lifecycle_event(
                session_id=session_id,
                project_id=project_id,
                memory_id=None,
                candidate=candidate.as_dict(),
                decision="duplicate",
                risk=decision.risk,
                reason="Equivalent memory already exists for this project.",
            )
            continue

        memory_id = _store_decision(store, decision, session_id=session_id, project_id=project_id)
        stored.append(memory_id)
        if decision.status == "auto_active":
            auto_active.append(memory_id)
        elif decision.status == "pending_confirmation":
            pending_confirmation.append(memory_id)
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
        pending_confirmation=pending_confirmation,
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
    return CleanupResult(expired=expired)


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
