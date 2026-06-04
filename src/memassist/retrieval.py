from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
import re

from .models import Memory
from .storage import Store


ACTIVE_STATUSES = {"active", "auto_active", "long_term", "durable", "pinned"}
STATUS_WEIGHT = {
    "pinned": 1.0,
    "durable": 0.90,
    "long_term": 0.85,
    "auto_active": 0.70,
    "active": 0.65,
}
CHANNEL_WEIGHTS = {
    "lexical": 1.00,
    "metadata": 0.90,
    "verifier": 1.00,
    "links_path": 0.85,
}
RRF_K = 60

DOMAIN_KEYWORDS = {
    "auth": {"auth", "login", "logout", "session", "token", "jwt", "oauth", "permission", "role", "access"},
    "security": {"security", "secret", "credential", "password", "encrypt", "sanitize", "xss", "csrf", "policy"},
    "database": {"database", "db", "sql", "sqlite", "postgres", "migration", "schema", "store", "storage"},
    "frontend": {"frontend", "ui", "react", "component", "css", "html", "browser", "viewport", "page"},
    "backend": {"backend", "api", "server", "endpoint", "route", "handler", "service", "fastapi"},
    "tests": {"test", "tests", "pytest", "unittest", "verify", "verification", "ci", "coverage"},
    "docs": {"readme", "doc", "docs", "documentation", "markdown"},
    "cli": {"cli", "command", "argparse", "terminal", "shell"},
    "retrieval": {"retrieval", "retrieve", "rag", "memory", "memassist", "query", "rank", "intent"},
    "deployment": {"deploy", "release", "production", "env", "config", "rollback"},
}

DOMAIN_PATH_HINTS = {
    "auth": ["auth", "session", "token"],
    "security": ["policy", "security"],
    "database": ["storage", "store", "db", "migration"],
    "frontend": ["app", "components", "pages", "styles"],
    "backend": ["api", "server", "routes", "handlers"],
    "tests": ["test", "tests", "spec"],
    "docs": ["README", "docs"],
    "cli": ["cli"],
    "retrieval": ["retrieval", "memassist"],
    "deployment": ["deploy", "config"],
}

TASK_TYPE_KEYWORDS = {
    "bugfix": {"fix", "bug", "error", "failing", "failure", "broken", "regression", "crash"},
    "feature": {"implement", "add", "build", "create", "support", "enable", "new"},
    "refactor": {"refactor", "cleanup", "simplify", "restructure", "rename"},
    "test": {"test", "tests", "verify", "coverage", "assert", "unittest", "pytest"},
    "review": {"review", "audit", "inspect", "assess"},
    "docs": {"doc", "docs", "readme", "documentation", "write"},
    "debug": {"debug", "trace", "diagnose", "root", "cause", "investigate"},
    "config": {"config", "setting", "env", "yaml", "toml", "json"},
}

HIGH_RISK_TERMS = {
    "auth",
    "authorization",
    "authentication",
    "token",
    "secret",
    "credential",
    "password",
    "permission",
    "policy",
    "security",
    "migration",
    "schema",
    "delete",
    "drop",
    "production",
    "deploy",
    "payment",
    "admin",
}
MEDIUM_RISK_TERMS = {
    "api",
    "database",
    "db",
    "storage",
    "session",
    "refactor",
    "integration",
    "workflow",
    "hook",
    "retrieval",
    "rank",
}
VERIFIER_TERMS = {"test", "tests", "verify", "verification", "ci", "coverage", "run", "assert", "failing"}
POLICY_TERMS = {"policy", "rule", "block", "warn", "permission", "security", "secret", "auth", "protect"}


@dataclass(frozen=True)
class QueryIntent:
    task_type: str
    domains: list[str]
    likely_paths: list[str]
    risk_level: str
    needs_policy: bool
    needs_verifier: bool
    retrieval_queries: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "task_type": self.task_type,
            "domains": self.domains,
            "likely_paths": self.likely_paths,
            "risk_level": self.risk_level,
            "needs_policy": self.needs_policy,
            "needs_verifier": self.needs_verifier,
            "retrieval_queries": self.retrieval_queries,
        }


@dataclass(frozen=True)
class MemoryPack:
    context: list[Memory]
    policy: list[Memory]
    verifier: list[Memory]
    intent: QueryIntent | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "context": [memory.as_dict() for memory in self.context],
            "policy": [memory.as_dict() for memory in self.policy],
            "verifier": [memory.as_dict() for memory in self.verifier],
            "intent": self.intent.as_dict() if self.intent else None,
        }


def build_memory_pack(
    store: Store,
    *,
    query: str,
    project_id: str,
    context_limit: int = 5,
) -> MemoryPack:
    intent = analyze_query_intent(query)
    scoped = _active_memories(store, project_id)
    channels = retrieve_channels(store, query=query, project_id=project_id, intent=intent, scoped=scoped)
    candidates = fuse_retrieval_channels(query, intent, channels)
    verifier = _section_memories(candidates, intent, section="verifier", limit=5)
    context = _section_memories(
        candidates,
        intent,
        section="context",
        limit=context_limit,
    )
    return MemoryPack(context=context, policy=[], verifier=verifier, intent=intent)


def analyze_query_intent(query: str) -> QueryIntent:
    tokens = _tokens(query)
    explicit_paths = _extract_paths(query)
    task_type = _task_type(tokens)
    domains = _domains(tokens, explicit_paths)
    likely_paths = _likely_paths(explicit_paths, domains)
    risk_level = _risk_level(tokens, domains, likely_paths)
    needs_policy = risk_level in {"medium", "high"} or bool(tokens & POLICY_TERMS) or any(domain in {"auth", "security"} for domain in domains)
    needs_verifier = (
        task_type in {"bugfix", "feature", "refactor", "test", "debug"}
        or bool(tokens & VERIFIER_TERMS)
        or risk_level in {"medium", "high"}
    )
    retrieval_queries = _retrieval_queries(query, task_type, domains, likely_paths)
    return QueryIntent(
        task_type=task_type,
        domains=domains,
        likely_paths=likely_paths,
        risk_level=risk_level,
        needs_policy=needs_policy,
        needs_verifier=needs_verifier,
        retrieval_queries=retrieval_queries,
    )


def retrieve_channels(
    store: Store,
    *,
    query: str,
    project_id: str,
    intent: QueryIntent | None = None,
    scoped: list[Memory] | None = None,
) -> dict[str, list[Memory]]:
    intent = intent or analyze_query_intent(query)
    scoped = scoped or _active_memories(store, project_id)
    lexical = _lexical_channel(store, intent, project_id=project_id)
    metadata = _metadata_channel(query, intent, scoped)
    verifier = _verifier_channel(query, intent, scoped)
    links_path = _links_path_channel(store, query, intent, scoped, seeds=[*lexical[:5], *metadata[:5]])
    return {
        "lexical": lexical,
        "metadata": metadata,
        "verifier": verifier,
        "links_path": links_path,
    }


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


def _metadata_channel(query: str, intent: QueryIntent, memories: list[Memory]) -> list[Memory]:
    relevant = [
        memory
        for memory in memories
        if _metadata_score(query, intent, memory) > 0
        or memory.status in {"pinned", "long_term", "durable"}
        or memory.type in {"lesson", "decision", "open_thread"}
    ]
    return sorted(relevant, key=lambda memory: _metadata_score(query, intent, memory), reverse=True)[:20]


def _verifier_channel(query: str, intent: QueryIntent, memories: list[Memory]) -> list[Memory]:
    if not intent.needs_verifier:
        return []
    verifier_memories = [memory for memory in memories if _is_verifier_memory(memory)]
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
    if section == "verifier" and not intent.needs_verifier:
        return []
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
    tags = set(memory.tags)
    return memory.type == "workflow" or bool(tags & {"test", "tests", "verification", "verify", "ci"})


def _metadata_score(query: str, intent: QueryIntent, memory: Memory) -> float:
    memory_terms = _memory_terms(memory)
    domain_score = sum(1.0 for domain in intent.domains if domain in memory_terms)
    tag_score = len(set(intent.domains) & set(memory.tags)) * 1.5
    path_score = _path_score(intent, memory) * 2.0
    query_overlap = len(_tokens(query) & memory_terms) / max(len(_tokens(query)), 1)
    type_score = 0.0
    if intent.task_type == "test" and _is_verifier_memory(memory):
        type_score += 1.0
    if intent.needs_policy and (memory.type == "rule" or memory.enforcement != "none"):
        type_score += 1.0
    return domain_score + tag_score + path_score + query_overlap + type_score


def _section_score(query: str, intent: QueryIntent, memory: Memory, section: str) -> float:
    base = _intent_memory_score(query, intent, memory)
    if section == "verifier":
        if memory.type == "workflow":
            base += 1.0
        if set(memory.tags) & {"verification", "test", "tests", "ci"}:
            base += 0.8
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
    tokens = set(re.findall(r"[A-Za-z0-9_가-힣]+", text.lower()))
    aliases = {
        "리프레시": {"refresh", "리프레쉬"},
        "리프레쉬": {"refresh", "리프레시"},
        "refresh": {"리프레시", "리프레쉬"},
        "토큰": {"token"},
        "token": {"토큰"},
        "ttl": {"만료", "시간"},
        "만료": {"ttl", "expiry", "expires"},
        "확인": {"confirm", "ask"},
        "승인": {"approval", "approve"},
        "변경": {"change", "modify"},
        "수정": {"change", "edit", "modify"},
    }
    expanded = set(tokens)
    for token in list(tokens):
        expanded.update(aliases.get(token, set()))
    return expanded


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


def _task_type(tokens: set[str]) -> str:
    if tokens & TASK_TYPE_KEYWORDS["feature"]:
        return "feature"
    scores = {
        task_type: len(tokens & keywords)
        for task_type, keywords in TASK_TYPE_KEYWORDS.items()
    }
    best_type, best_score = max(scores.items(), key=lambda item: (item[1], -list(TASK_TYPE_KEYWORDS).index(item[0])))
    return best_type if best_score else "general"


def _domains(tokens: set[str], explicit_paths: list[str]) -> list[str]:
    scored: list[tuple[int, str]] = []
    path_text = " ".join(explicit_paths).lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = len(tokens & keywords)
        score += sum(1 for hint in DOMAIN_PATH_HINTS.get(domain, []) if hint.lower() in path_text)
        if score:
            scored.append((score, domain))
    return [domain for _score, domain in sorted(scored, key=lambda item: (-item[0], item[1]))]


def _likely_paths(explicit_paths: list[str], domains: list[str]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for path in explicit_paths:
        if path not in seen:
            seen.add(path)
            paths.append(path)
    for domain in domains:
        for hint in DOMAIN_PATH_HINTS.get(domain, []):
            if hint not in seen:
                seen.add(hint)
                paths.append(hint)
    return paths[:8]


def _risk_level(tokens: set[str], domains: list[str], likely_paths: list[str]) -> str:
    path_text = " ".join(likely_paths).lower()
    if tokens & HIGH_RISK_TERMS or any(domain in {"auth", "security", "deployment"} for domain in domains):
        return "high"
    if tokens & MEDIUM_RISK_TERMS or any(domain in {"database", "backend", "retrieval"} for domain in domains):
        return "medium"
    if any(term in path_text for term in {"auth", "policy", "storage", "db", "deploy"}):
        return "medium"
    return "low"


def _retrieval_queries(query: str, task_type: str, domains: list[str], likely_paths: list[str]) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = " ".join(value.split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            queries.append(normalized)

    add(query)
    if domains:
        add(" ".join([task_type, *domains]))
    if likely_paths:
        add(" ".join(likely_paths[:4]))
    for domain in domains[:3]:
        add(domain)
    if task_type != "general":
        add(task_type)
    return queries[:6]


def _memory_terms(memory: Memory) -> set[str]:
    return _tokens(" ".join([memory.content, " ".join(memory.tags), " ".join(memory.paths), memory.type]))


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
