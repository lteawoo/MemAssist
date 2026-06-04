from __future__ import annotations

import re
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


HIGH_RISK_TERMS = [
    "auth",
    "authentication",
    "authorization",
    "token",
    "secret",
    "password",
    "billing",
    "payment",
    "permission",
    "권한",
    "인증",
    "토큰",
    "결제",
    "시크릿",
]

PROTECTION_TERMS = [
    "do not",
    "don't",
    "never",
    "must not",
    "without asking",
    "묻지 않고",
    "건드리지",
    "수정하지",
    "바꾸지",
    "금지",
    "막아",
]


def classify_candidate(candidate: MemoryCandidate) -> CandidateDecision:
    text = candidate.content.lower()
    memory_kind = _memory_kind(candidate)
    high_risk = _contains_any(text, HIGH_RISK_TERMS)
    protective = _contains_any(text, PROTECTION_TERMS)
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

    if candidate.type in {"rule", "lesson"} or protective or high_risk:
        return CandidateDecision(
            candidate=candidate,
            decision="keep_candidate",
            status="candidate",
            risk="high" if high_risk or protective else "medium",
            memory_kind=memory_kind,
            caution_level="none",
            reason=(
                "Potentially strong or risky inferred memory is kept inactive; "
                "only direct user directives can create caution-level metadata."
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
    if candidate.type in {"fact", "preference", "decision"}:
        return "semantic"
    if candidate.type == "workflow":
        return "procedural"
    if candidate.type in {"lesson", "rule"}:
        return "procedural"
    return "episodic"


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text) or term in text for term in terms)
