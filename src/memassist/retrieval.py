from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re

from .embedding_profiles import EmbeddingProfile
from .embeddings import ChunkVectorSearchResult, VectorSearchResult, selected_embedding_profile, vector_search, vector_search_chunks
from .models import Memory, MemoryChunk
from .storage import Store


ACTIVE_STATUSES = {"active"}
STATUS_WEIGHT = {
    "active": 1.0,
}
CHANNEL_WEIGHTS = {
    "lexical_chunk": 1.05,
    "lexical": 1.00,
    "vector_chunk": 1.00,
    "vector": 0.95,
    "metadata": 0.90,
    "links_path": 0.85,
}
RRF_K = 60
PROMPT_CONTEXT_BUDGET_CHARS = 2400
PROMPT_ITEM_BUDGET_CHARS = 420
PROMPT_SNIPPET_CHUNK_KINDS = {"content"}
_W_RETRIEVAL = 0.30
_W_UTILITY = 0.25
_W_STRENGTH = 0.25
_W_RECENCY = 0.20
_RECENCY_TAU_DAYS = 30.0
_MAX_USAGE_SCORE = 1.0


def _normalize_retrieval_count(count: int) -> float:
    return min(1.0, math.log1p(max(0, count)) / math.log1p(100))


def _recency_decay(last_used_at: str | None) -> float:
    if not last_used_at:
        return 0.0
    try:
        used = datetime.fromisoformat(last_used_at)
    except ValueError:
        return 0.0
    if used.tzinfo is None:
        used = used.replace(tzinfo=timezone.utc)
    delta_days = max(0.0, (datetime.now(timezone.utc) - used).total_seconds() / 86400)
    return math.exp(-delta_days / _RECENCY_TAU_DAYS)


def _usage_score(memory: Memory) -> float:
    rc = _normalize_retrieval_count(memory.retrieval_count)
    ut = max(0.0, min(1.0, memory.utility))
    st = max(0.0, min(1.0, memory.strength))
    rec = _recency_decay(memory.last_used_at)
    score = _W_RETRIEVAL * rc + _W_UTILITY * ut + _W_STRENGTH * st + _W_RECENCY * rec
    return min(_MAX_USAGE_SCORE, score)


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
class MemorySnippet:
    memory_id: str
    chunk_id: str
    chunk_kind: str
    content: str

    def as_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "chunk_id": self.chunk_id,
            "chunk_kind": self.chunk_kind,
            "content": self.content,
        }


@dataclass(frozen=True)
class MemoryPack:
    context: list[Memory]
    verifier: list[Memory]
    intent: QueryIntent | None = None
    diagnostics: dict[str, object] | None = None
    snippets: list[MemorySnippet] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "context": [memory.as_dict() for memory in self.context],
            "verifier": [memory.as_dict() for memory in self.verifier],
            "intent": self.intent.as_dict() if self.intent else None,
            "diagnostics": self.diagnostics or {},
            "snippets": [snippet.as_dict() for snippet in self.snippets],
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
    verifier = _section_memories(candidates, intent, query=query, section="verifier", limit=5)
    context = _section_memories(
        candidates,
        intent,
        query=query,
        section="context",
        limit=context_limit,
    )
    snippets = _snippets_for_memories(context + verifier, retrieval_info)
    return MemoryPack(
        context=context,
        verifier=verifier,
        intent=intent,
        diagnostics=_retrieval_diagnostics(channels, candidates, retrieval_info),
        snippets=snippets,
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
    lexical_chunks = _lexical_chunk_channel(store, intent, project_id=project_id)
    lexical_chunk_memories = _memories_from_chunks(store, lexical_chunks)
    legacy_lexical = [] if lexical_chunk_memories else _lexical_channel(store, intent, project_id=project_id)
    lexical = lexical_chunk_memories or legacy_lexical
    vector_chunk_result = _vector_chunk_channel(store, query, project_id=project_id, embedding_profile_id=embedding_profile_id)
    vector_chunk_memories = _memories_from_chunks(store, vector_chunk_result.chunks)
    vector_result = _vector_channel(store, query, project_id=project_id, embedding_profile_id=embedding_profile_id)
    vector = _dedupe_memory_order([*vector_chunk_memories, *vector_result.memories], limit=15)
    metadata = _metadata_channel(query, intent, scoped)
    links_path = _links_path_channel(store, query, intent, scoped, seeds=[*lexical[:5], *vector[:5], *metadata[:5]])
    return (
        {
            "lexical_chunk": lexical_chunk_memories,
            "lexical": legacy_lexical,
            "vector_chunk": vector_chunk_memories,
            "vector": vector,
            "metadata": metadata,
            "links_path": links_path,
        },
        {
            "vector": vector_result.diagnostics(),
            "vector_chunks": vector_chunk_result.diagnostics(),
            "chunk_hits": [chunk.as_dict() for chunk in _dedupe_chunks([*lexical_chunks, *vector_chunk_result.chunks], limit=30)],
        },
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


def _lexical_chunk_channel(store: Store, intent: QueryIntent, *, project_id: str) -> list[MemoryChunk]:
    results: list[MemoryChunk] = []
    seen: set[str] = set()
    for retrieval_query in intent.retrieval_queries:
        for chunk in store.search_memory_chunks(retrieval_query, project_id=project_id, limit=20):
            if chunk.id in seen:
                continue
            seen.add(chunk.id)
            results.append(chunk)
    return results


def _vector_channel(store: Store, query: str, *, project_id: str, embedding_profile_id: str | None = None) -> VectorSearchResult:
    try:
        profile = selected_embedding_profile(embedding_profile_id, store=store)
    except Exception as exc:
        fallback = EmbeddingProfile(id=embedding_profile_id or "active", provider="none", model="none", dimension=0, normalize=False)
        return VectorSearchResult([], "profile_error", fallback, error=str(exc))
    return vector_search(store, query=query, project_id=project_id, profile=profile, limit=15)


def _vector_chunk_channel(store: Store, query: str, *, project_id: str, embedding_profile_id: str | None = None) -> ChunkVectorSearchResult:
    try:
        profile = selected_embedding_profile(embedding_profile_id, store=store)
    except Exception as exc:
        fallback = EmbeddingProfile(id=embedding_profile_id or "active", provider="none", model="none", dimension=0, normalize=False)
        return ChunkVectorSearchResult([], "profile_error", fallback, error=str(exc))
    return vector_search_chunks(store, query=query, project_id=project_id, profile=profile, limit=20)


def _memories_from_chunks(store: Store, chunks: list[MemoryChunk]) -> list[Memory]:
    memories: list[Memory] = []
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.memory_id in seen:
            continue
        memory = store.get_memory(chunk.memory_id)
        if not memory or memory.status != "active":
            continue
        seen.add(memory.id)
        memories.append(memory)
    return memories


def _dedupe_memory_order(memories: list[Memory], *, limit: int) -> list[Memory]:
    results: list[Memory] = []
    seen: set[str] = set()
    for memory in memories:
        if memory.id in seen:
            continue
        seen.add(memory.id)
        results.append(memory)
        if len(results) >= limit:
            break
    return results


def _dedupe_chunks(chunks: list[MemoryChunk], *, limit: int) -> list[MemoryChunk]:
    results: list[MemoryChunk] = []
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.id in seen:
            continue
        seen.add(chunk.id)
        results.append(chunk)
        if len(results) >= limit:
            break
    return results


def _metadata_channel(query: str, intent: QueryIntent, memories: list[Memory]) -> list[Memory]:
    relevant = [
        memory
        for memory in memories
        if _metadata_score(query, intent, memory) > 0
    ]
    return sorted(relevant, key=lambda memory: _metadata_score(query, intent, memory), reverse=True)[:20]


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
    query: str,
    section: str,
    limit: int,
    excluded_ids: set[str] | None = None,
) -> list[Memory]:
    excluded_ids = excluded_ids or set()
    section_candidates = [
        memory
        for memory in candidates
        if memory.id not in excluded_ids
    ]
    return sorted(section_candidates, key=lambda memory: _section_score(query, intent, memory, section), reverse=True)[:limit]


def _metadata_score(query: str, intent: QueryIntent, memory: Memory) -> float:
    path_score = _path_score(intent, memory) * 2.0
    query_tokens = _tokens(query)
    metadata_terms = _metadata_terms(memory)
    metadata_overlap = len(query_tokens & metadata_terms) / max(len(query_tokens), 1)
    return path_score + metadata_overlap


def _section_score(query: str, intent: QueryIntent, memory: Memory, section: str) -> float:
    return _intent_memory_score(query, intent, memory)


def _intent_memory_score(query: str, intent: QueryIntent, memory: Memory) -> float:
    effective_query = query or " ".join(intent.retrieval_queries)
    return (
        _memory_score(effective_query, memory)
        + _metadata_score(effective_query, intent, memory) * 0.15
        + STATUS_WEIGHT.get(memory.status, 0.0) * 0.15
        + memory.importance * 0.10
        + memory.confidence * 0.08
        + _usage_score(memory) * 0.20
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
                memory.status,
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
    vector_chunk_info = (retrieval_info or {}).get("vector_chunks")
    chunk_hits = (retrieval_info or {}).get("chunk_hits")
    vector_status = vector_info.get("status") if isinstance(vector_info, dict) else None
    vector_chunk_status = vector_chunk_info.get("status") if isinstance(vector_chunk_info, dict) else None
    chunk_hit_count = len(chunk_hits) if isinstance(chunk_hits, list) else 0
    return {
        "channels": {channel: len(ids) for channel, ids in channel_ids.items()},
        "vector_enabled": bool(channel_ids.get("vector")),
        "vector_status": vector_status or ("ok" if channel_ids.get("vector") else "unknown"),
        "vector": vector_info or {},
        "vector_chunk_status": vector_chunk_status or "unknown",
        "vector_chunks": vector_chunk_info or {},
        "chunk_hit_count": chunk_hit_count,
        "contributions": {
            memory.id: contributions.get(memory.id, [])
            for memory in candidates
        },
    }


def _snippets_for_memories(memories: list[Memory], retrieval_info: dict[str, object] | None) -> list[MemorySnippet]:
    chunk_hits = (retrieval_info or {}).get("chunk_hits")
    if not isinstance(chunk_hits, list):
        return []
    allowed_ids = {memory.id for memory in memories}
    snippets: list[MemorySnippet] = []
    seen: set[tuple[str, str]] = set()
    for item in chunk_hits:
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("memory_id") or "")
        chunk_kind = str(item.get("chunk_kind") or "")
        if memory_id not in allowed_ids or chunk_kind not in PROMPT_SNIPPET_CHUNK_KINDS:
            continue
        key = (memory_id, chunk_kind)
        if key in seen:
            continue
        content = str(item.get("content") or "").strip()
        chunk_id = str(item.get("id") or "")
        if not content or not chunk_id:
            continue
        seen.add(key)
        snippets.append(
            MemorySnippet(
                memory_id=memory_id,
                chunk_id=chunk_id,
                chunk_kind=chunk_kind,
                content=content,
            )
        )
    return snippets


def render_prompt_context(pack: MemoryPack, *, max_chars: int = PROMPT_CONTEXT_BUDGET_CHARS) -> str:
    lines: list[str] = []
    remaining = max_chars
    snippets_by_memory: dict[str, list[MemorySnippet]] = {}
    for snippet in pack.snippets:
        snippets_by_memory.setdefault(snippet.memory_id, []).append(snippet)

    def add_line(line: str) -> bool:
        nonlocal remaining
        if remaining <= 0:
            return False
        text = line
        newline_cost = 1 if lines else 0
        available = remaining - newline_cost
        if available <= 0:
            return False
        if len(text) > available:
            suffix = " ...<truncated>"
            if available <= len(suffix):
                return False
            text = text[: available - len(suffix)].rstrip() + suffix
        lines.append(text)
        remaining -= len(text) + newline_cost
        return True

    if pack.context:
        add_line("Relevant memassist memory:")
        for memory in pack.context:
            content = _prompt_memory_text(memory, snippets_by_memory.get(memory.id, []), intent=pack.intent)
            if not add_line(f"- [{memory.type}] {content}"):
                break
    if pack.verifier:
        add_line("Verification reminders:")
        for memory in pack.verifier[:3]:
            content = _prompt_memory_text(memory, snippets_by_memory.get(memory.id, []), intent=pack.intent)
            if not add_line(f"- {content}"):
                break
    return "\n".join(lines)


def _prompt_memory_text(memory: Memory, snippets: list[MemorySnippet], *, intent: QueryIntent | None) -> str:
    query = intent.retrieval_queries[0] if intent and intent.retrieval_queries else ""
    if len(memory.content) <= PROMPT_ITEM_BUDGET_CHARS:
        return memory.content
    for preferred in ("content",):
        for snippet in snippets:
            if snippet.chunk_kind == preferred:
                return _truncate_relevant_text(snippet.content, PROMPT_ITEM_BUDGET_CHARS, query=query)
    return _truncate_relevant_text(memory.content, PROMPT_ITEM_BUDGET_CHARS, query=query)


def _truncate_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    suffix = " ...<truncated>"
    if limit <= len(suffix):
        return suffix[-limit:]
    return compact[: max(0, limit - len(suffix))].rstrip() + suffix


def _truncate_relevant_text(text: str, limit: int, *, query: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    hit = _first_query_hit(compact, query)
    if hit is None:
        return _truncate_text(compact, limit)
    suffix = " ...<truncated>"
    prefix = "<truncated>... "
    window = max(0, limit - len(prefix) - len(suffix))
    if window <= 0:
        return _truncate_text(compact, limit)
    start = max(0, hit - window // 3)
    end = min(len(compact), start + window)
    if end - start < window:
        start = max(0, end - window)
    excerpt = compact[start:end].strip()
    left = prefix if start > 0 else ""
    right = suffix if end < len(compact) else ""
    return f"{left}{excerpt}{right}"


def _first_query_hit(text: str, query: str) -> int | None:
    lowered = text.lower()
    hits: list[int] = []
    for token in sorted(_tokens(query), key=len, reverse=True):
        if len(token) < 2:
            continue
        index = lowered.find(token.lower())
        if index >= 0:
            hits.append(index)
    return min(hits) if hits else None
