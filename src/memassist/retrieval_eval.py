from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .eval_seed import SeedMemory, insert_seed_memories, remove_seed_memories, seed_from_value
from .models import Memory
from .storage import Store


@dataclass(frozen=True)
class RetrievalCase:
    query: str
    expect: list[str]
    forbid: list[str]
    seed: list[SeedMemory]
    limit: int = 5

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RetrievalCase":
        return cls(
            query=str(value.get("query", "")),
            expect=_string_list(value.get("expect")),
            forbid=_string_list(value.get("forbid")),
            seed=seed_from_value(value.get("seed")),
            limit=int(value.get("limit", 5)),
        )


@dataclass(frozen=True)
class RetrievalEvalResult:
    passed: bool
    case_count: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    forbidden_recall_rate: float
    cases: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "case_count": self.case_count,
            "recall_at_k": self.recall_at_k,
            "precision_at_k": self.precision_at_k,
            "mrr": self.mrr,
            "forbidden_recall_rate": self.forbidden_recall_rate,
            "cases": self.cases,
        }


def load_cases(path: Path) -> list[RetrievalCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list):
        raise ValueError("retrieval eval case file must contain a list or {\"cases\": [...]}")
    return [RetrievalCase.from_dict(case) for case in raw_cases if isinstance(case, dict)]


def evaluate_retrieval(
    store: Store,
    *,
    project_id: str,
    cases: list[RetrievalCase],
) -> RetrievalEvalResult:
    case_results: list[dict[str, Any]] = []
    recall_total = 0.0
    precision_total = 0.0
    reciprocal_total = 0.0
    forbidden_hits = 0

    for case in cases:
        seed_ids = insert_seed_memories(
            store,
            project_id=project_id,
            seed=case.seed,
            source_kind="retrieval_eval_seed",
        )
        try:
            retrieved = store.search_memories(case.query, project_id=project_id, limit=case.limit)
            hit_indexes = _hit_indexes(retrieved, case.expect)
            forbidden = _matched_terms(retrieved, case.forbid)
        finally:
            remove_seed_memories(store, seed_ids)
        recall = 1.0 if case.expect and len(hit_indexes) == len(case.expect) else 0.0
        precision = (len(hit_indexes) / len(retrieved)) if retrieved else 0.0
        reciprocal_rank = (1.0 / (min(hit_indexes) + 1)) if hit_indexes else 0.0
        if forbidden:
            forbidden_hits += 1
        recall_total += recall
        precision_total += precision
        reciprocal_total += reciprocal_rank
        case_results.append(
            {
                "query": case.query,
                "expect": case.expect,
                "forbid": case.forbid,
                "retrieved": [_memory_result(memory) for memory in retrieved],
                "recall": recall,
                "precision": precision,
                "reciprocal_rank": reciprocal_rank,
                "forbidden_hits": forbidden,
                "passed": recall == 1.0 and not forbidden,
            }
        )

    count = len(cases)
    if count == 0:
        return RetrievalEvalResult(True, 0, 1.0, 1.0, 1.0, 0.0, [])
    recall_at_k = recall_total / count
    precision_at_k = precision_total / count
    mrr = reciprocal_total / count
    forbidden_recall_rate = forbidden_hits / count
    return RetrievalEvalResult(
        passed=recall_at_k == 1.0 and forbidden_recall_rate == 0.0,
        case_count=count,
        recall_at_k=recall_at_k,
        precision_at_k=precision_at_k,
        mrr=mrr,
        forbidden_recall_rate=forbidden_recall_rate,
        cases=case_results,
    )


def _hit_indexes(memories: list[Memory], terms: list[str]) -> list[int]:
    hits: list[int] = []
    for term in terms:
        lowered = term.lower()
        for index, memory in enumerate(memories):
            if lowered in memory.content.lower() or lowered in " ".join(memory.tags).lower():
                hits.append(index)
                break
    return hits


def _matched_terms(memories: list[Memory], terms: list[str]) -> list[str]:
    matched: list[str] = []
    for term in terms:
        lowered = term.lower()
        if any(lowered in memory.content.lower() for memory in memories):
            matched.append(term)
    return matched


def _memory_result(memory: Memory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "type": memory.type,
        "status": memory.status,
        "importance": memory.importance,
        "content": memory.content,
        "tags": memory.tags,
    }


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []
