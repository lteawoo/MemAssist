from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
class RagCase:
    query: str
    expect: list[RagExpectation]
    forbid: list[RagExpectation]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RagCase":
        return cls(
            query=str(value.get("query", "")),
            expect=[RagExpectation.from_value(item) for item in _list(value.get("expect"))],
            forbid=[RagExpectation.from_value(item) for item in _list(value.get("forbid"))],
        )


@dataclass(frozen=True)
class RagEvalResult:
    passed: bool
    case_count: int
    section_accuracy: float
    context_relevance: float
    policy_leak_rate: float
    verifier_recall: float
    cases: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "case_count": self.case_count,
            "section_accuracy": self.section_accuracy,
            "context_relevance": self.context_relevance,
            "policy_leak_rate": self.policy_leak_rate,
            "verifier_recall": self.verifier_recall,
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

    for case in cases:
        pack = build_memory_pack(store, query=case.query, project_id=project_id)
        section_hits = _section_hits(pack, case.expect)
        forbidden_hits = _section_hits(pack, case.forbid)
        expected_count = len([expect for expect in case.expect if expect.term])
        section_accuracy = (len(section_hits) / expected_count) if expected_count else 1.0
        verifier_expected = [expect for expect in case.expect if expect.section == "verifier"]
        verifier_hits = [hit for hit in section_hits if hit["section"] == "verifier"]
        verifier_recall = (len(verifier_hits) / len(verifier_expected)) if verifier_expected else 1.0
        retrieved_count = sum(len(getattr(pack, section)) for section in SECTIONS)
        context_relevance = (len(section_hits) / retrieved_count) if retrieved_count else (1.0 if not expected_count else 0.0)
        policy_leak = any(hit["section"] == "policy" for hit in forbidden_hits)
        policy_leaks += 1 if policy_leak else 0
        section_total += section_accuracy
        relevance_total += context_relevance
        verifier_total += verifier_recall
        rows.append(
            {
                "query": case.query,
                "expect": [expect.__dict__ for expect in case.expect],
                "forbid": [forbid.__dict__ for forbid in case.forbid],
                "section_hits": section_hits,
                "forbidden_hits": forbidden_hits,
                "pack": pack.as_dict(),
                "section_accuracy": section_accuracy,
                "context_relevance": context_relevance,
                "verifier_recall": verifier_recall,
                "passed": section_accuracy == 1.0 and not forbidden_hits,
            }
        )

    count = len(cases)
    if count == 0:
        return RagEvalResult(True, 0, 1.0, 1.0, 0.0, 1.0, [])
    section_accuracy = section_total / count
    context_relevance = relevance_total / count
    policy_leak_rate = policy_leaks / count
    verifier_recall = verifier_total / count
    return RagEvalResult(
        passed=section_accuracy == 1.0 and policy_leak_rate == 0.0 and verifier_recall == 1.0,
        case_count=count,
        section_accuracy=section_accuracy,
        context_relevance=context_relevance,
        policy_leak_rate=policy_leak_rate,
        verifier_recall=verifier_recall,
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
