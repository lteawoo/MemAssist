from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .classifier import CandidateDecision, classify_candidate
from .extraction import MemoryCandidate
from .models import Memory


def evaluate_candidate(candidate: MemoryCandidate, *, existing_memories: list[Memory]) -> CandidateDecision:
    base = classify_candidate(candidate)
    scores = _score_candidate(candidate, existing_memories=existing_memories)
    total = _weighted_total(scores)
    status = base.status
    decision = base.decision
    caution_level = base.caution_level
    reason = base.reason

    if status == "candidate" and decision != "keep_candidate" and total >= 0.68:
        status = "active"
        decision = "activate"
        reason = "Candidate met the memory quality threshold after evidence and specificity scoring."
    elif decision == "keep_candidate" and scores["conflict_score"] < 0.35:
        status = "archived"
        decision = "archive_conflict"
        reason = "Candidate conflicts with an existing memory and needs to be discarded before activation."
    elif decision == "keep_candidate" and total < 0.50:
        status = "archived"
        decision = "archive_low_quality"
        reason = "Candidate did not meet minimum evidence quality for inactive storage."

    return replace(
        base,
        decision=decision,
        status=status,
        caution_level=caution_level,
        reason=reason,
        scores=scores,
        total_score=total,
    )


def _score_candidate(candidate: MemoryCandidate, *, existing_memories: list[Memory]) -> dict[str, float]:
    evidence = _clamp(candidate.confidence)
    specificity = _specificity_score(candidate.content)
    repeat = _repeat_score(candidate, existing_memories)
    verification = 1.0 if candidate.type == "workflow" and "test" in candidate.tags else 0.6
    freshness = 1.0
    return {
        "evidence_score": evidence,
        "repeat_score": repeat,
        "specificity_score": specificity,
        "risk_score": 1.0,
        "conflict_score": 1.0,
        "verification_score": verification,
        "freshness_score": freshness,
    }


def _weighted_total(scores: dict[str, float]) -> float:
    weights = {
        "evidence_score": 0.20,
        "repeat_score": 0.15,
        "specificity_score": 0.15,
        "risk_score": 0.15,
        "conflict_score": 0.15,
        "verification_score": 0.10,
        "freshness_score": 0.10,
    }
    return round(sum(scores[key] * weight for key, weight in weights.items()), 4)


def _specificity_score(content: str) -> float:
    tokens = re.findall(r"[A-Za-z0-9_가-힣]+", content.lower())
    if not tokens:
        return 0.0
    unique = len(set(tokens))
    if "/" in content or "." in content or "-" in content:
        unique += 2
    return _clamp(unique / 12)


def _repeat_score(candidate: MemoryCandidate, existing_memories: list[Memory]) -> float:
    candidate_tokens = _tokens(candidate.content)
    if not candidate_tokens:
        return 0.0
    best = 0.0
    for memory in existing_memories:
        if memory.type != candidate.type:
            continue
        overlap = len(candidate_tokens & _tokens(memory.content)) / max(len(candidate_tokens), 1)
        best = max(best, overlap)
    return _clamp(best)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_가-힣]+", text.lower()))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
