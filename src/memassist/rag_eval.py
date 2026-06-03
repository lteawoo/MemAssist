from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import uuid

from .eval_seed import remove_seed_memories_by_source
from .retrieval import MemoryPack, build_memory_pack
from .storage import Store


SECTIONS = ("context", "policy", "verifier")


@dataclass(frozen=True)
class RagExpectation:
    term: str
    section: str

    @classmethod
    def from_value(cls, value: object) -> "RagExpectation":
        if isinstance(value, str):
            return cls(term=value, section="*")
        if isinstance(value, dict):
            return cls(term=str(value.get("term", "")), section=str(value.get("section", "*")))
        return cls(term="", section="*")


@dataclass(frozen=True)
class RagSeedMemory:
    type: str
    content: str
    section: str
    tags: list[str]
    paths: list[str]
    status: str
    importance: float
    confidence: float
    enforcement: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RagSeedMemory":
        section = str(value.get("section", "context"))
        return cls(
            type=str(value.get("type") or _default_type(section)),
            content=str(value.get("content", "")),
            section=section,
            tags=_string_list(value.get("tags")),
            paths=_string_list(value.get("paths")),
            status=str(value.get("status") or _default_status(section)),
            importance=float(value.get("importance", 0.8)),
            confidence=float(value.get("confidence", 0.85)),
            enforcement=str(value.get("enforcement") or _default_enforcement(section)),
        )


@dataclass(frozen=True)
class RagCase:
    query: str
    expect: list[RagExpectation]
    forbid: list[RagExpectation]
    seed: list[RagSeedMemory]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RagCase":
        return cls(
            query=str(value.get("query", "")),
            expect=[RagExpectation.from_value(item) for item in _list(value.get("expect"))],
            forbid=[RagExpectation.from_value(item) for item in _list(value.get("forbid"))],
            seed=[
                RagSeedMemory.from_dict(item)
                for item in _list(value.get("seed"))
                if isinstance(item, dict)
            ],
        )


@dataclass(frozen=True)
class RagEvalResult:
    passed: bool
    case_count: int
    section_accuracy: float
    context_relevance: float
    policy_leak_rate: float
    verifier_recall: float
    pass_rate: float
    score: float
    cases: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "case_count": self.case_count,
            "section_accuracy": self.section_accuracy,
            "context_relevance": self.context_relevance,
            "policy_leak_rate": self.policy_leak_rate,
            "verifier_recall": self.verifier_recall,
            "pass_rate": self.pass_rate,
            "score": self.score,
            "cases": self.cases,
        }


def load_rag_cases(path: Path) -> list[RagCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list):
        raise ValueError("rag eval case file must contain a list or {\"cases\": [...]}")
    return [RagCase.from_dict(case) for case in raw_cases if isinstance(case, dict)]


def evaluate_rag(store: Store, *, project_id: str, cases: list[RagCase]) -> RagEvalResult:
    rows: list[dict[str, Any]] = []
    section_total = 0.0
    relevance_total = 0.0
    verifier_total = 0.0
    policy_leaks = 0
    passed_cases = 0

    for case in cases:
        remove_seed_memories_by_source(store, project_id=project_id, source_kind="rag_eval_seed")
        seed_ids = _insert_seed_memories(store, project_id=project_id, seed=case.seed)
        try:
            pack = build_memory_pack(store, query=case.query, project_id=project_id)
            section_hits = _section_hits(pack, case.expect)
            forbidden_hits = _section_hits(pack, case.forbid)
            expected_count = len([expect for expect in case.expect if expect.term])
            section_accuracy = (len(section_hits) / expected_count) if expected_count else 1.0
            verifier_expected = [expect for expect in case.expect if expect.section == "verifier"]
            verifier_hits = [hit for hit in section_hits if hit["section"] == "verifier"]
            verifier_recall = (len(verifier_hits) / len(verifier_expected)) if verifier_expected else 1.0
            retrieved_count = sum(len(getattr(pack, section)) for section in SECTIONS)
            context_relevance = (
                len(section_hits) / retrieved_count
                if retrieved_count
                else (1.0 if not expected_count else 0.0)
            )
            policy_leak = any(hit["section"] == "policy" for hit in forbidden_hits)
            case_passed = section_accuracy == 1.0 and not forbidden_hits
        finally:
            _remove_seed_memories(store, seed_ids)

        policy_leaks += 1 if policy_leak else 0
        passed_cases += 1 if case_passed else 0
        section_total += section_accuracy
        relevance_total += context_relevance
        verifier_total += verifier_recall
        rows.append(
            {
                "query": case.query,
                "expect": [expect.__dict__ for expect in case.expect],
                "forbid": [forbid.__dict__ for forbid in case.forbid],
                "seed_count": len(case.seed),
                "section_hits": section_hits,
                "forbidden_hits": forbidden_hits,
                "pack": pack.as_dict(),
                "section_accuracy": section_accuracy,
                "context_relevance": context_relevance,
                "verifier_recall": verifier_recall,
                "passed": case_passed,
            }
        )

    count = len(cases)
    if count == 0:
        return RagEvalResult(True, 0, 1.0, 1.0, 0.0, 1.0, 1.0, 5.0, [])
    section_accuracy = section_total / count
    context_relevance = relevance_total / count
    policy_leak_rate = policy_leaks / count
    verifier_recall = verifier_total / count
    pass_rate = passed_cases / count
    score = _rag_score(
        section_accuracy=section_accuracy,
        context_relevance=context_relevance,
        policy_leak_rate=policy_leak_rate,
        verifier_recall=verifier_recall,
        pass_rate=pass_rate,
    )
    return RagEvalResult(
        passed=policy_leak_rate == 0.0 and pass_rate == 1.0 and verifier_recall == 1.0,
        case_count=count,
        section_accuracy=section_accuracy,
        context_relevance=context_relevance,
        policy_leak_rate=policy_leak_rate,
        verifier_recall=verifier_recall,
        pass_rate=pass_rate,
        score=score,
        cases=rows,
    )


def _section_hits(pack: MemoryPack, expectations: list[RagExpectation]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for expectation in expectations:
        if not expectation.term:
            continue
        sections = SECTIONS if expectation.section == "*" else (expectation.section,)
        for section in sections:
            memories = getattr(pack, section, [])
            if any(_contains(memory.as_dict(), expectation.term) for memory in memories):
                hits.append({"term": expectation.term, "section": section})
                break
    return hits


def _contains(memory: dict[str, Any], term: str) -> bool:
    lowered = term.lower()
    haystack = " ".join(
        [
            str(memory.get("content", "")),
            " ".join(str(item) for item in memory.get("tags", [])),
            " ".join(str(item) for item in memory.get("paths", [])),
        ]
    ).lower()
    return lowered in haystack


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def _default_type(section: str) -> str:
    if section == "policy":
        return "rule"
    if section == "verifier":
        return "workflow"
    return "lesson"


def _default_status(section: str) -> str:
    if section == "policy":
        return "block_policy"
    if section == "verifier":
        return "durable"
    return "active"


def _default_enforcement(section: str) -> str:
    return "block" if section == "policy" else "none"


def _insert_seed_memories(store: Store, *, project_id: str, seed: list[RagSeedMemory]) -> list[str]:
    ids: list[str] = []
    for memory in seed:
        if not memory.content:
            continue
        memory_id = store.add_memory(
            scope_type="project",
            project_id=project_id,
            type=memory.type,
            content=memory.content,
            reason="Temporary memory inserted by rag eval fixture.",
            tags=memory.tags,
            paths=memory.paths,
            status=memory.status,
            importance=memory.importance,
            confidence=memory.confidence,
            enforcement=memory.enforcement,
            source_kind="rag_eval_seed",
            source_ref=f"rag_eval:{uuid.uuid4().hex[:12]}",
        )
        ids.append(memory_id)
    return ids


def _remove_seed_memories(store: Store, memory_ids: list[str]) -> None:
    if not memory_ids:
        return
    placeholders = ", ".join("?" for _ in memory_ids)
    store.conn.execute(f"DELETE FROM memory_links WHERE source_id IN ({placeholders})", memory_ids)
    store.conn.execute(f"DELETE FROM memory_links WHERE target_id IN ({placeholders})", memory_ids)
    store.conn.execute(f"DELETE FROM memory_fts WHERE memory_id IN ({placeholders})", memory_ids)
    store.conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", memory_ids)
    store.conn.commit()


def _rag_score(
    *,
    section_accuracy: float,
    context_relevance: float,
    policy_leak_rate: float,
    verifier_recall: float,
    pass_rate: float,
) -> float:
    policy_safety = max(0.0, 1.0 - policy_leak_rate)
    weighted = (
        section_accuracy * 0.35
        + context_relevance * 0.20
        + policy_safety * 0.20
        + verifier_recall * 0.15
        + pass_rate * 0.10
    )
    return round(weighted * 5.0, 3)
