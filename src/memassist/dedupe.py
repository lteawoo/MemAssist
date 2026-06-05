from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Memory


ACTIVE_STRONG_STATUSES = {"active"}
INACTIVE_STATUSES = {"candidate"}


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
    candidate_caution_level: str,
    existing_memories: list[Memory],
    threshold: float = 0.65,
) -> DuplicateMatch | None:
    candidate_tokens = _tokens(" ".join([candidate_content, " ".join(candidate_tags)]))
    if not candidate_tokens:
        return None
    best: DuplicateMatch | None = None
    for memory in existing_memories:
        if memory.status == "archived":
            continue
        if not _compatible_type(candidate_type, memory.type):
            continue
        memory_tokens = _tokens(" ".join([memory.content, " ".join(memory.tags), " ".join(memory.paths)]))
        if not memory_tokens:
            continue
        overlap = len(candidate_tokens & memory_tokens) / max(len(candidate_tokens), 1)
        tag_overlap = len(set(candidate_tags) & set(memory.tags)) / max(len(set(candidate_tags)), 1)
        strength_bonus = 0.12 if _is_stronger(memory.status, candidate_status) else 0.0
        score = min(1.0, overlap * 0.76 + tag_overlap * 0.18 + strength_bonus)
        if score >= threshold and (best is None or score > best.score):
            best = DuplicateMatch(
                memory=memory,
                score=round(score, 4),
                reason="semantic duplicate by overlapping text, tags, and memory strength",
            )
    return best


def should_suppress_candidate(candidate_status: str, duplicate: Memory) -> bool:
    if candidate_status in INACTIVE_STATUSES and duplicate.status in ACTIVE_STRONG_STATUSES:
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
        "active": 2,
    }
    return rank.get(memory_status, 0) > rank.get(candidate_status, 0)


def _tokens(text: str) -> set[str]:
    normalized = text.lower()
    raw = re.findall(r"[A-Za-z0-9_가-힣]+", normalized)
    return {token for token in raw if len(token) > 1}
