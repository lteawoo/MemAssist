from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
import re

from .embedding_profiles import EmbeddingProfile
from .embeddings import VectorSearchResult, selected_embedding_profile, vector_search
from .models import Memory
from .storage import Store


ACTIVE_STATUSES = {"active"}
STATUS_WEIGHT = {
    "active": 1.0,
}
CHANNEL_WEIGHTS = {
    "lexical": 1.00,
    "vector": 0.95,
    "metadata": 0.90,
    "verifier": 1.00,
    "links_path": 0.85,
}
RRF_K = 60


@dataclass(frozen=True)
class QueryIntent:
    task_type: str
    domains: list[str]
    likely_paths: list[str]
    risk_level: str
    needs_caution_context: bool
    needs_verifier: bool
    retrieval_queries: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "task_type": self.task_type,
            "domains": self.domains,
            "likely_paths": self.likely_paths,
            "risk_level": self.risk_level,
            "needs_caution_context": self.needs_caution_context,
            "needs_verifier": self.needs_verifier,
            "retrieval_queries": self.retrieval_queries,
        }


@dataclass(frozen=True)
class MemoryPack:
    context: list[Memory]
    verifier: list[Memory]
    intent: QueryIntent | None = None
    diagnostics: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "context": [memory.as_dict() for memory in self.context],
            "verifier": [memory.as_dict() for memory in self.verifier],
            "intent": self.intent.as_dict() if self.intent else None,
            "diagnostics": self.diagnostics or {},
        }


def build_memory_pack(
    store: Store,
    *,
    query: str,
    project_id: str,
    context_limit: int = 5,
    embedding_profile_id: str | None = None,
) -> MemoryPack:
    intent = analyze_query_intent(query)
    scoped = _active_memories(store, project_id)
    channels, retrieval_info = retrieve_channels(
        store,
        query=query,
        project_id=project_id,
        intent=intent,
        scoped=scoped,
        embedding_profile_id=embedding_profile_id,
    )
    candidates = fuse_retrieval_channels(query, intent, channels)
    verifier = _section_memories(candidates, intent, section="verifier", limit=5)
    context = _section_memories(
        candidates,
        intent,
        section="context",
        limit=context_limit,
    )
    return MemoryPack(
        context=context,
        verifier=verifier,
        intent=intent,
        diagnostics=_retrieval_diagnostics(channels, candidates, retrieval_info),
    )


def analyze_query_intent(query: str) -> QueryIntent:
    explicit_paths = _extract_paths(query)
    retrieval_queries = _retrieval_queries(query, explicit_paths)
    return QueryIntent(
        task_type="general",
        domains=[],
        likely_paths=explicit_paths,
        risk_level="low",
        needs_caution_context=False,
        needs_verifier=False,
        retrieval_queries=retrieval_queries,
    )


def retrieve_channels(
    store: Store,
    *,
    query: str,
    project_id: str,
    intent: QueryIntent | None = None,
    scoped: list[Memory] | None = None,
    embedding_profile_id: str | None = None,
) -> tuple[dict[str, list[Memory]], dict[str, object]]:
    intent = intent or analyze_query_intent(query)
    scoped = scoped or _active_memories(store, project_id)
    lexical = _lexical_channel(store, intent, project_id=project_id)
    vector_result = _vector_channel(store, query, project_id=project_id, embedding_profile_id=embedding_profile_id)
    metadata = _metadata_channel(query, intent, scoped)
    verifier = _verifier_channel(query, intent, scoped)
    links_path = _links_path_channel(store, query, intent, scoped, seeds=[*lexical[:5], *vector_result.memories[:5], *metadata[:5]])
    return (
        {
            "lexical": lexical,
            "vector": vector_result.memories,
            "metadata": metadata,
            "verifier": verifier,
            "links_path": links_path,
        },
        {"vector": vector_result.diagnostics()},
    )


def fuse_retrieval_channels(
    query: str,
    intent: QueryIntent,
    channels: dict[str, list[Memory]],
    *,
    limit: int = 30,
) -> list[Memory]:
    scores: dict[str, float] = {}
    memories: dict[str, Memory] = {}
    best_channel_rank: dict[str, int] = {}
    for channel, ranked_memories in channels.items():
        weight = CHANNEL_WEIGHTS.get(channel, 1.0)
        seen_in_channel: set[str] = set()
        for rank, memory in enumerate(ranked_memories, start=1):
            if memory.id in seen_in_channel:
                continue
            seen_in_channel.add(memory.id)
            memories[memory.id] = memory
            scores[memory.id] = scores.get(memory.id, 0.0) + weight / (RRF_K + rank)
            best_channel_rank[memory.id] = min(best_channel_rank.get(memory.id, rank), rank)
    ranked = sorted(
        memories.values(),
        key=lambda memory: (
            scores[memory.id],
            _intent_memory_score(query, intent, memory),
            STATUS_WEIGHT.get(memory.status, 0.0),
            memory.importance,
            memory.confidence,
            -best_channel_rank[memory.id],
        ),
        reverse=True,
    )
    return _dedupe_by_signature(ranked, limit=limit)


def _active_memories(store: Store, project_id: str) -> list[Memory]:
    return [
        memory
        for memory in store.list_memories(project_id=project_id, include_global=True)
        if memory.status in ACTIVE_STATUSES
    ]


def _lexical_channel(store: Store, intent: QueryIntent, *, project_id: str) -> list[Memory]:
    results: list[Memory] = []
    seen: set[str] = set()
    for retrieval_query in intent.retrieval_queries:
        for memory in store.search_memories(retrieval_query, project_id=project_id, limit=10):
            if memory.id in seen:
                continue
            seen.add(memory.id)
            results.append(memory)
    return results


def _vector_channel(store: Store, query: str, *, project_id: str, embedding_profile_id: str | None = None) -> VectorSearchResult:
    try:
        profile = selected_embedding_profile(embedding_profile_id, store=store)
    except Exception as exc:
        fallback = EmbeddingProfile(id=embedding_profile_id or "active", provider="none", model="none", dimension=0, normalize=False)
        return VectorSearchResult([], "profile_error", fallback, error=str(exc))
    return vector_search(store, query=query, project_id=project_id, profile=profile, limit=15)


def _metadata_channel(query: str, intent: QueryIntent, memories: list[Memory]) -> list[Memory]:
    relevant = [
        memory
        for memory in memories
        if _metadata_score(query, intent, memory) > 0
    ]
    return sorted(relevant, key=lambda memory: _metadata_score(query, intent, memory), reverse=True)[:20]


def _verifier_channel(query: str, intent: QueryIntent, memories: list[Memory]) -> list[Memory]:
    verifier_memories = [
        memory
        for memory in memories
        if _is_verifier_memory(memory)
        and (_metadata_score(query, intent, memory) > 0 or _content_score(query, memory) > 0)
    ]
    return sorted(verifier_memories, key=lambda memory: _section_score(query, intent, memory, "verifier"), reverse=True)[:12]


def _links_path_channel(
    store: Store,
    query: str,
    intent: QueryIntent,
    memories: list[Memory],
    *,
    seeds: Iterable[Memory],
) -> list[Memory]:
    results: list[Memory] = []
    seen: set[str] = set()
    memory_by_id = {memory.id: memory for memory in memories}
    for memory in memories:
        if _path_score(intent, memory) <= 0:
            continue
        seen.add(memory.id)
        results.append(memory)
    for seed in seeds:
        for link in store.memory_links(seed.id):
            linked = memory_by_id.get(str(link["target_id"]))
            if not linked or linked.id in seen:
                continue
            seen.add(linked.id)
            results.append(linked)
    return sorted(results, key=lambda memory: _section_score(query, intent, memory, "context"), reverse=True)[:15]


def _section_memories(
    candidates: list[Memory],
    intent: QueryIntent,
    *,
    section: str,
    limit: int,
    excluded_ids: set[str] | None = None,
) -> list[Memory]:
    excluded_ids = excluded_ids or set()
    section_candidates = [
        memory
        for memory in candidates
        if memory.id not in excluded_ids and _belongs_to_section(memory, section)
    ]
    return sorted(section_candidates, key=lambda memory: _section_score("", intent, memory, section), reverse=True)[:limit]


def _belongs_to_section(memory: Memory, section: str) -> bool:
    if section == "verifier":
        return _is_verifier_memory(memory)
    return True


def _is_verifier_memory(memory: Memory) -> bool:
    return memory.type == "workflow"


def _metadata_score(query: str, intent: QueryIntent, memory: Memory) -> float:
    path_score = _path_score(intent, memory) * 2.0
    query_tokens = _tokens(query)
    metadata_terms = _metadata_terms(memory)
    metadata_overlap = len(query_tokens & metadata_terms) / max(len(query_tokens), 1)
    return path_score + metadata_overlap


def _section_score(query: str, intent: QueryIntent, memory: Memory, section: str) -> float:
    base = _intent_memory_score(query, intent, memory)
    if section == "verifier":
        if memory.type == "workflow":
            base += 1.0
    else:
        if memory.type in {"directive", "lesson", "decision", "fact", "open_thread", "rule"}:
            base += 0.6
    return base


def _intent_memory_score(query: str, intent: QueryIntent, memory: Memory) -> float:
    return (
        _memory_score(query or " ".join(intent.retrieval_queries), memory)
        + _metadata_score(query or " ".join(intent.retrieval_queries), intent, memory) * 0.15
        + STATUS_WEIGHT.get(memory.status, 0.0) * 0.15
        + memory.importance * 0.10
        + memory.confidence * 0.08
    )


def _path_score(intent: QueryIntent, memory: Memory) -> float:
    if not intent.likely_paths or not memory.paths:
        return 0.0
    score = 0.0
    memory_paths = [path.lower() for path in memory.paths]
    for likely_path in intent.likely_paths:
        normalized = likely_path.lower()
        if any(normalized in path or path in normalized for path in memory_paths):
            score += 1.0
    return min(score, 3.0)


def _dedupe_by_signature(memories: list[Memory], *, limit: int) -> list[Memory]:
    diversified: list[Memory] = []
    seen_signatures: set[str] = set()
    seen_ids: set[str] = set()
    for memory in memories:
        if memory.id in seen_ids:
            continue
        signature = _signature(memory.content)
        if signature in seen_signatures:
            continue
        seen_ids.add(memory.id)
        seen_signatures.add(signature)
        diversified.append(memory)
        if len(diversified) >= limit:
            break
    return diversified


def _memory_score(query: str, memory: Memory) -> float:
    query_tokens = _tokens(query)
    overlap = _content_score(query, memory)
    path_match = 1.0 if any(token in " ".join(memory.paths).lower() for token in query_tokens) else 0.0
    status = STATUS_WEIGHT.get(memory.status, 0.0)
    recency = _recency_score(memory.updated_at)
    return (
        overlap * 0.35
        + status * 0.20
        + memory.importance * 0.15
        + memory.confidence * 0.15
        + path_match * 0.05
        + recency * 0.05
    )


def _content_score(query: str, memory: Memory) -> float:
    query_tokens = _tokens(query)
    text_tokens = _tokens(" ".join([memory.content, " ".join(memory.tags), " ".join(memory.paths)]))
    return len(query_tokens & text_tokens) / max(len(query_tokens), 1)


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


def _extract_paths(text: str) -> list[str]:
    path_pattern = re.compile(
        r"(?:(?:[\w.-]+/)+[\w./-]+|[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|swift|rb|php|cs|cpp|c|h|hpp|sql|ya?ml|json|toml|md|css|scss|html))"
    )
    paths: list[str] = []
    seen: set[str] = set()
    for match in path_pattern.findall(text):
        path = match.strip(".,:;()[]{}'\"")
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _retrieval_queries(query: str, explicit_paths: list[str]) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = " ".join(value.split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            queries.append(normalized)

    add(query)
    for path in explicit_paths:
        add(path)
    return queries[:6]


def _metadata_terms(memory: Memory) -> set[str]:
    return _tokens(
        " ".join(
            [
                memory.type,
                memory.status,
                memory.caution_level,
                " ".join(memory.tags),
                " ".join(memory.paths),
            ]
        )
    )


def _signature(content: str) -> str:
    tokens = sorted(_tokens(content))
    return " ".join(tokens[:16])


def _retrieval_diagnostics(
    channels: dict[str, list[Memory]],
    candidates: list[Memory],
    retrieval_info: dict[str, object] | None = None,
) -> dict[str, object]:
    channel_ids = {
        channel: [memory.id for memory in memories]
        for channel, memories in channels.items()
    }
    contributions: dict[str, list[str]] = {}
    for channel, ids in channel_ids.items():
        for memory_id in ids:
            contributions.setdefault(memory_id, []).append(channel)
    vector_info = (retrieval_info or {}).get("vector")
    vector_status = vector_info.get("status") if isinstance(vector_info, dict) else None
    return {
        "channels": {channel: len(ids) for channel, ids in channel_ids.items()},
        "vector_enabled": bool(channel_ids.get("vector")),
        "vector_status": vector_status or ("ok" if channel_ids.get("vector") else "unknown"),
        "vector": vector_info or {},
        "contributions": {
            memory.id: contributions.get(memory.id, [])
            for memory in candidates
        },
    }


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
