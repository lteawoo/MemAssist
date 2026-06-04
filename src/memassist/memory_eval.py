from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .eval_seed import (
    SeedMemory,
    insert_seed_memories,
    remove_seed_memories,
    remove_seed_memories_by_source,
    seed_from_value,
)
from .models import Memory
from .storage import Store


ACTIVE_STATUSES = {"active"}


@dataclass(frozen=True)
class MemoryQualityCase:
    name: str
    query: str
    expect: list[str]
    forbid: list[str]
    expect_statuses: list[str]
    forbid_statuses: list[str]
    seed: list[SeedMemory]
    limit: int = 10

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MemoryQualityCase":
        return cls(
            name=str(value.get("name") or value.get("query") or "case"),
            query=str(value.get("query", "")),
            expect=_string_list(value.get("expect")),
            forbid=_string_list(value.get("forbid")),
            expect_statuses=_string_list(value.get("expect_statuses")) or sorted(ACTIVE_STATUSES),
            forbid_statuses=_string_list(value.get("forbid_statuses")) or sorted(ACTIVE_STATUSES),
            seed=seed_from_value(value.get("seed")),
            limit=int(value.get("limit", 10)),
        )


@dataclass(frozen=True)
class MemoryQualityEvalResult:
    passed: bool
    case_count: int
    memory_recall: float
    memory_precision: float
    wrong_promotion_rate: float
    wrong_policy_rate: float
    stale_memory_rate: float
    cases: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "case_count": self.case_count,
            "memory_recall": self.memory_recall,
            "memory_precision": self.memory_precision,
            "wrong_context_promotion_rate": self.wrong_policy_rate,
            "wrong_promotion_rate": self.wrong_promotion_rate,
            "wrong_policy_rate": self.wrong_policy_rate,
            "stale_memory_rate": self.stale_memory_rate,
            "cases": self.cases,
        }


def load_memory_quality_cases(path: Path) -> list[MemoryQualityCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list):
        raise ValueError("memory eval case file must contain a list or {\"cases\": [...]}")
    return [MemoryQualityCase.from_dict(case) for case in raw_cases if isinstance(case, dict)]


def evaluate_memory_quality(
    store: Store,
    *,
    project_id: str,
    cases: list[MemoryQualityCase],
) -> MemoryQualityEvalResult:
    rows: list[dict[str, Any]] = []
    recall_total = 0.0
    precision_total = 0.0
    wrong_promotions = 0
    wrong_policies = 0
    stale_hits = 0

    for case in cases:
        remove_seed_memories_by_source(store, project_id=project_id, source_kind="memory_eval_seed")
        seed_ids = insert_seed_memories(
            store,
            project_id=project_id,
            seed=case.seed,
            source_kind="memory_eval_seed",
        )
        try:
            retrieved = store.search_memories(case.query, project_id=project_id, limit=case.limit)
            expected_hits = _matched_terms(retrieved, case.expect, set(case.expect_statuses))
            forbidden_hits = _matched_terms(retrieved, case.forbid, set(case.forbid_statuses))
            stale = [memory.id for memory in retrieved if memory.status == "archived"]
            wrong_policy: list[str] = []
            wrong_promotion = [
                term
                for term in case.forbid
                for memory in retrieved
                if memory.status == "active" and _contains(memory, term)
            ]
        finally:
            remove_seed_memories(store, seed_ids)
        recall = len(expected_hits) / len(case.expect) if case.expect else 1.0
        precision = 1.0 if not forbidden_hits else 0.0
        recall_total += recall
        precision_total += precision
        wrong_promotions += 1 if wrong_promotion else 0
        wrong_policies += 1 if wrong_policy else 0
        stale_hits += 1 if stale else 0
        rows.append(
            {
                "name": case.name,
                "query": case.query,
                "expect": case.expect,
                "forbid": case.forbid,
                "expected_hits": expected_hits,
                "forbidden_hits": forbidden_hits,
                "wrong_policy_hits": wrong_policy,
                "wrong_promotion_hits": wrong_promotion,
                "stale_hits": stale,
                "retrieved": [_memory_result(memory) for memory in retrieved],
                "passed": recall == 1.0 and not forbidden_hits and not stale and not wrong_policy,
            }
        )

    count = len(cases)
    if count == 0:
        return MemoryQualityEvalResult(True, 0, 1.0, 1.0, 0.0, 0.0, 0.0, [])
    memory_recall = recall_total / count
    memory_precision = precision_total / count
    wrong_promotion_rate = wrong_promotions / count
    wrong_policy_rate = wrong_policies / count
    stale_memory_rate = stale_hits / count
    return MemoryQualityEvalResult(
        passed=memory_recall == 1.0
        and memory_precision == 1.0
        and wrong_promotion_rate == 0.0
        and wrong_policy_rate == 0.0
        and stale_memory_rate == 0.0,
        case_count=count,
        memory_recall=memory_recall,
        memory_precision=memory_precision,
        wrong_promotion_rate=wrong_promotion_rate,
        wrong_policy_rate=wrong_policy_rate,
        stale_memory_rate=stale_memory_rate,
        cases=rows,
    )


def _matched_terms(memories: list[Memory], terms: list[str], statuses: set[str]) -> list[str]:
    matched: list[str] = []
    for term in terms:
        if any(memory.status in statuses and _contains(memory, term) for memory in memories):
            matched.append(term)
    return matched


def _contains(memory: Memory, term: str) -> bool:
    lowered = term.lower()
    haystack = " ".join([memory.content, " ".join(memory.tags), " ".join(memory.paths)]).lower()
    return lowered in haystack


def _memory_result(memory: Memory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "type": memory.type,
        "status": memory.status,
        "confidence": memory.confidence,
        "importance": memory.importance,
        "content": memory.content,
        "tags": memory.tags,
    }


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []
