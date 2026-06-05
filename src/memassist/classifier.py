from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .extraction import MemoryCandidate


@dataclass(frozen=True)
class CandidateDecision:
    candidate: MemoryCandidate
    decision: str
    status: str
    risk: str
    memory_kind: str
    caution_level: str
    reason: str
    scores: dict[str, float] | None = None
    total_score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.as_dict(),
            "decision": self.decision,
            "status": self.status,
            "risk": self.risk,
            "memory_kind": self.memory_kind,
            "caution_level": self.caution_level,
            "reason": self.reason,
            "scores": self.scores or {},
            "total_score": self.total_score,
        }


def classify_candidate(candidate: MemoryCandidate) -> CandidateDecision:
    memory_kind = _memory_kind(candidate)
    explicit = _has_explicit_source(candidate)
    duplicate_or_weak = candidate.confidence < 0.45 or candidate.importance < 0.35

    if duplicate_or_weak:
        return CandidateDecision(
            candidate=candidate,
            decision="archive_low_quality",
            status="archived",
            risk="low",
            memory_kind=memory_kind,
            caution_level="none",
            reason="Candidate confidence or importance is below automatic memory threshold.",
        )

    if candidate.type == "workflow" and "test" in candidate.tags:
        return CandidateDecision(
            candidate=candidate,
            decision="activate",
            status="active",
            risk="low",
            memory_kind="procedural",
            caution_level="none",
            reason="Verification workflow was observed in the trace and is safe to remember automatically.",
        )

    if candidate.type == "fact" and candidate.content.startswith("Session touched"):
        return CandidateDecision(
            candidate=candidate,
            decision="archive_session_evidence",
            status="archived",
            risk="low",
            memory_kind="episodic",
            caution_level="none",
            reason="Touched-file facts are session evidence, not active project memory.",
        )

    if candidate.type in {"preference", "decision", "directive"} and explicit:
        return CandidateDecision(
            candidate=candidate,
            decision="activate",
            status="active",
            risk="low",
            memory_kind=memory_kind,
            caution_level="none",
            reason=(
                "Persistent user memory has explicit source provenance and can be remembered automatically; "
                "memassist does not derive tool policy from memory text."
            ),
        )

    if candidate.type in {"rule", "lesson"}:
        return CandidateDecision(
            candidate=candidate,
            decision="keep_candidate",
            status="candidate",
            risk="medium",
            memory_kind=memory_kind,
            caution_level="none",
            reason=(
                "Inferred rule-like memory remains a candidate until stronger source evidence is available; "
                "retrieved memories remain context and do not create tool policy."
            ),
        )

    if candidate.type in {"preference", "decision"}:
        return CandidateDecision(
            candidate=candidate,
            decision="activate",
            status="active",
            risk="low",
            memory_kind=memory_kind,
            caution_level="none",
            reason="Explicit low-risk preference or decision can be remembered automatically.",
        )

    return CandidateDecision(
        candidate=candidate,
        decision="candidate",
        status="candidate",
        risk="medium",
        memory_kind=memory_kind,
        caution_level="none",
        reason="Candidate may be useful but lacks enough confidence for automatic activation.",
    )


def _memory_kind(candidate: MemoryCandidate) -> str:
    if candidate.type in {"fact", "preference", "decision", "directive"}:
        return "semantic"
    if candidate.type == "workflow":
        return "procedural"
    if candidate.type in {"lesson", "rule"}:
        return "procedural"
    return "episodic"


def _has_explicit_source(candidate: MemoryCandidate) -> bool:
    tags = set(candidate.tags)
    return bool(tags & {"explicit", "user_prompt", "isolated_judge"})
