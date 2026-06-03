from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Memory


ACTIVE_STRONG_STATUSES = {"active", "auto_active", "long_term", "durable", "warn_policy", "block_policy", "pinned"}
INACTIVE_STATUSES = {"candidate", "draft", "stale"}
PROTECTIVE_TERMS = {
    "확인",
    "허락",
    "block",
    "forbid",
    "protect",
    "금지",
    "막아",
}

SYNONYMS = {
    "리프레시": "refresh",
    "토큰": "token",
    "리프레시토큰": "refresh token",
    "정책": "policy",
    "변경": "change",
    "수정": "change",
    "확인": "policy",
    "인증": "auth",
    "세션": "session",
}


@dataclass(frozen=True)
class DuplicateMatch:
    memory: Memory
    score: float
    reason: str


def find_semantic_duplicate(
    candidate_content: str,
    *,
    candidate_type: str,
    candidate_tags: list[str],
    candidate_status: str,
    candidate_enforcement: str,
    existing_memories: list[Memory],
    threshold: float = 0.65,
) -> DuplicateMatch | None:
    candidate_tokens = _tokens(" ".join([candidate_content, " ".join(candidate_tags)]))
    if not candidate_tokens:
        return None
    candidate_protective = _is_protective(candidate_content, candidate_enforcement)
    best: DuplicateMatch | None = None
    for memory in existing_memories:
        if memory.status in {"rejected", "expired", "superseded", "disabled", "deleted", "ephemeral"}:
            continue
        if not _compatible_type(candidate_type, memory.type):
            continue
        memory_tokens = _tokens(" ".join([memory.content, " ".join(memory.tags), " ".join(memory.paths)]))
        if not memory_tokens:
            continue
        overlap = len(candidate_tokens & memory_tokens) / max(len(candidate_tokens), 1)
        tag_overlap = len(set(candidate_tags) & set(memory.tags)) / max(len(set(candidate_tags)), 1)
        protective_bonus = 0.18 if candidate_protective and _is_protective(memory.content, memory.enforcement) else 0.0
        strength_bonus = 0.12 if _is_stronger(memory.status, candidate_status) else 0.0
        score = min(1.0, overlap * 0.70 + tag_overlap * 0.18 + protective_bonus + strength_bonus)
        if score >= threshold and (best is None or score > best.score):
            best = DuplicateMatch(
                memory=memory,
                score=round(score, 4),
                reason="semantic duplicate by overlapping intent, tags, and protection terms",
            )
    return best


def should_suppress_candidate(candidate_status: str, duplicate: Memory) -> bool:
    if candidate_status in INACTIVE_STATUSES and duplicate.status in ACTIVE_STRONG_STATUSES:
        return True
    if candidate_status == "candidate" and duplicate.status in {"warn_policy", "block_policy"}:
        return True
    return False


def _compatible_type(candidate_type: str, memory_type: str) -> bool:
    if candidate_type == memory_type:
        return True
    semantic_types = {"preference", "rule", "lesson", "decision"}
    return candidate_type in semantic_types and memory_type in semantic_types


def _is_stronger(memory_status: str, candidate_status: str) -> bool:
    rank = {
        "candidate": 1,
        "draft": 1,
        "active": 2,
        "auto_active": 2,
        "long_term": 3,
        "durable": 4,
        "warn_policy": 4,
        "block_policy": 4,
        "pinned": 5,
    }
    return rank.get(memory_status, 0) > rank.get(candidate_status, 0)


def _is_protective(content: str, enforcement: str) -> bool:
    if enforcement in {"warn", "block"}:
        return True
    text = content.lower()
    return any(term in text for term in PROTECTIVE_TERMS)


def _tokens(text: str) -> set[str]:
    normalized = text.lower()
    for source, replacement in SYNONYMS.items():
        normalized = normalized.replace(source, f" {replacement} ")
    raw = re.findall(r"[A-Za-z0-9_가-힣]+", normalized)
    return {
        token
        for token in raw
        if len(token) > 1 and token not in {"앞으로", "먼저", "관련", "없이", "하고", "진행", "하겠습니다"}
    }
