from __future__ import annotations

from dataclasses import dataclass

from .models import Memory
from .storage import Store


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
    scoped = store.list_memories(project_id=project_id, include_global=True, status="active")
    candidate_ids = {memory.id for memory in candidates}
    for memory in scoped:
        if memory.id in candidate_ids:
            continue
        if memory.type in {"rule", "workflow"} or memory.enforcement != "none":
            candidates.append(memory)
            candidate_ids.add(memory.id)
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
