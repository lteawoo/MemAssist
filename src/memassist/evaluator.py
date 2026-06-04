from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .classifier import CandidateDecision, HIGH_RISK_TERMS, PROTECTION_TERMS, classify_candidate
from .extraction import MemoryCandidate
from .models import Memory


def evaluate_candidate(candidate: MemoryCandidate, *, existing_memories: list[Memory]) -> CandidateDecision:
    base = classify_candidate(candidate)
    scores = _score_candidate(candidate, existing_memories=existing_memories)
    total = _weighted_total(scores)
    status = base.status
    decision = base.decision
    enforcement = base.enforcement
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
        reason = "Risky candidate did not meet minimum evidence quality for inactive storage."
    elif decision == "keep_candidate" and candidate.type in {"rule", "lesson"} and total >= 0.72:
        enforcement = "warn"
        reason = "Risky inferred memory is retained as a reminder with warning semantics, not a gate."

    return replace(
        base,
        decision=decision,
        status=status,
        enforcement=enforcement,
        reason=reason,
        scores=scores,
        total_score=total,
    )


def _score_candidate(candidate: MemoryCandidate, *, existing_memories: list[Memory]) -> dict[str, float]:
    text = candidate.content.lower()
    evidence = _clamp(candidate.confidence)
    specificity = _specificity_score(candidate.content)
    repeat = _repeat_score(candidate, existing_memories)
    risk = 1.0 - _risk_penalty(text)
    conflict = _conflict_score(candidate, existing_memories)
    verification = 1.0 if candidate.type == "workflow" and "test" in candidate.tags else 0.6
    freshness = 1.0
    return {
        "evidence_score": evidence,
        "repeat_score": repeat,
        "specificity_score": specificity,
        "risk_score": risk,
        "conflict_score": conflict,
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


def _conflict_score(candidate: MemoryCandidate, existing_memories: list[Memory]) -> float:
    candidate_text = candidate.content.lower()
    protective = _contains_any(candidate_text, PROTECTION_TERMS)
    permissive = _contains_any(candidate_text, ["allow", "allowed", "can edit", "수정해도", "허용"])
    candidate_tokens = _tokens(candidate.content)
    for memory in existing_memories:
        if memory.status == "archived":
            continue
        overlap = len(candidate_tokens & _tokens(memory.content)) / max(len(candidate_tokens), 1)
        if overlap < 0.4:
            continue
        memory_text = memory.content.lower()
        memory_protective = _contains_any(memory_text, PROTECTION_TERMS)
        memory_permissive = _contains_any(memory_text, ["allow", "allowed", "can edit", "수정해도", "허용"])
        if (protective and memory_permissive) or (permissive and memory_protective):
            return 0.0
    return 1.0


def _risk_penalty(text: str) -> float:
    risk = 0.0
    if _contains_any(text, HIGH_RISK_TERMS):
        risk += 0.35
    if _contains_any(text, PROTECTION_TERMS):
        risk += 0.20
    return _clamp(risk)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_가-힣]+", text.lower()))


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
