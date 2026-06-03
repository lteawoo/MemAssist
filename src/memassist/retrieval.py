from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from .models import Memory
from .storage import Store


ACTIVE_STATUSES = {"active", "auto_active", "long_term", "policy_active", "pinned"}
STATUS_WEIGHT = {
    "pinned": 1.0,
    "policy_active": 0.95,
    "long_term": 0.85,
    "auto_active": 0.70,
    "active": 0.65,
}


@dataclass(frozen=True)
class MemoryPack:
    context: list[Memory]
    policy: list[Memory]
    verifier: list[Memory]

    def as_dict(self) -> dict[str, list[dict[str, object]]]:
        return {
            "context": [memory.as_dict() for memory in self.context],
            "policy": [memory.as_dict() for memory in self.policy],
            "verifier": [memory.as_dict() for memory in self.verifier],
        }


def build_memory_pack(
    store: Store,
    *,
    query: str,
    project_id: str,
    context_limit: int = 5,
) -> MemoryPack:
    candidates = store.search_memories(query, project_id=project_id, limit=25)
    scoped = [
        memory
        for memory in store.list_memories(project_id=project_id, include_global=True)
        if memory.status in ACTIVE_STATUSES
    ]
    candidate_ids = {memory.id for memory in candidates}
    for memory in scoped:
        if memory.id in candidate_ids:
            continue
        if memory.type in {"rule", "workflow"} or memory.enforcement != "none":
            candidates.append(memory)
            candidate_ids.add(memory.id)
    candidates = rerank_memories(query, candidates)
    policy = [
        memory
        for memory in candidates
        if memory.type == "rule" or memory.enforcement in {"warn", "require_approval", "block"}
    ]
    verifier = [
        memory
        for memory in candidates
        if memory.type == "workflow" or "test" in memory.tags or "verification" in memory.tags
    ]
    context = [
        memory
        for memory in candidates
        if memory not in policy or memory.type in {"lesson", "decision"}
    ][:context_limit]
    return MemoryPack(context=context, policy=policy, verifier=verifier)


def rerank_memories(query: str, memories: list[Memory], *, limit: int | None = None) -> list[Memory]:
    ranked = sorted(memories, key=lambda memory: _memory_score(query, memory), reverse=True)
    diversified: list[Memory] = []
    seen_signatures: set[str] = set()
    for memory in ranked:
        signature = _signature(memory.content)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        diversified.append(memory)
        if limit and len(diversified) >= limit:
            break
    return diversified


def _memory_score(query: str, memory: Memory) -> float:
    query_tokens = _tokens(query)
    text_tokens = _tokens(" ".join([memory.content, " ".join(memory.tags), " ".join(memory.paths)]))
    overlap = len(query_tokens & text_tokens) / max(len(query_tokens), 1)
    path_match = 1.0 if any(token in " ".join(memory.paths).lower() for token in query_tokens) else 0.0
    type_match = 1.0 if memory.type in {"rule", "workflow"} and query_tokens & {"test", "verify", "검증", "policy", "정책"} else 0.0
    status = STATUS_WEIGHT.get(memory.status, 0.0)
    recency = _recency_score(memory.updated_at)
    return (
        overlap * 0.35
        + status * 0.20
        + memory.importance * 0.15
        + memory.confidence * 0.15
        + path_match * 0.05
        + type_match * 0.05
        + recency * 0.05
    )


def _recency_score(value: str) -> float:
    try:
        updated = datetime.fromisoformat(value)
    except ValueError:
        return 0.5
    age_seconds = max((datetime.now(updated.tzinfo) - updated).total_seconds(), 0)
    age_days = age_seconds / 86400
    if age_days <= 1:
        return 1.0
    if age_days >= 30:
        return 0.1
    return max(0.1, 1.0 - (age_days / 30))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_가-힣]+", text.lower()))


def _signature(content: str) -> str:
    tokens = sorted(_tokens(content))
    return " ".join(tokens[:16])


def render_prompt_context(pack: MemoryPack) -> str:
    lines: list[str] = []
    if pack.context:
        lines.append("Relevant memassist memory:")
        for memory in pack.context:
            lines.append(f"- [{memory.type}] {memory.content}")
    if pack.verifier:
        lines.append("Verification reminders:")
        for memory in pack.verifier[:3]:
            lines.append(f"- {memory.content}")
    return "\n".join(lines)
