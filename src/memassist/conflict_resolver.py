from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol

from .integrations import status_tools
from .models import Memory
from .project import Project
from .storage import Store


INTERPRETER_ACTIVE_ENV = "MEMASSIST_INTERPRETER_ACTIVE"
RELATION_JUDGE_ACTIVE_ENV = "MEMASSIST_MEMORY_RELATION_JUDGE_ACTIVE"
RELATION_JUDGE_FIXTURE_ENV = "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE"
RELATION_JUDGE_TIMEOUT_ENV = "MEMASSIST_MEMORY_RELATION_JUDGE_TIMEOUT"
MAX_RELATION_CANDIDATES = 20
MAX_RELATION_CONTENT_CHARS = 500
RELATION_TYPES = {
    "duplicate",
    "complementary",
    "candidate_supersedes",
    "existing_supersedes",
    "conflicts",
    "unrelated",
}
RELATION_PRIORITY = {
    "conflicts": 6,
    "candidate_supersedes": 5,
    "existing_supersedes": 4,
    "duplicate": 3,
    "complementary": 2,
    "unrelated": 1,
}


@dataclass(frozen=True)
class MemoryDraft:
    content: str
    type: str
    source_integrity: str
    source_quote: str
    reason: str
    tags: list[str]
    source_ref: str
    source_ids: list[str]
    judge: dict[str, object]
    source_event_id: str
    source_id: object | None
    payload: dict[str, object]


@dataclass(frozen=True)
class Relation:
    existing_memory_id: str
    relation: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "existing_memory_id": self.existing_memory_id,
            "relation": self.relation,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RelationJudgeResult:
    relations: list[Relation]
    adapter_name: str
    diagnostics: list[str]
    payload: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "relations": [relation.as_dict() for relation in self.relations],
            "adapter_name": self.adapter_name,
            "diagnostics": self.diagnostics,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class ResolutionResult:
    memory_id: str | None
    decision: str
    relations: list[Relation]
    diagnostics: list[str]


class RelationJudge(Protocol):
    name: str

    def judge(self, payload: dict[str, object], *, project: Project) -> RelationJudgeResult:
        ...


def resolve_memory_conflicts(
    store: Store,
    *,
    project: Project,
    session_id: str,
    draft: MemoryDraft,
) -> ResolutionResult:
    exact_duplicate = store.find_memory(
        project_id=project.id,
        content=draft.content,
        statuses=("candidate", "active", "archived"),
    )
    if exact_duplicate:
        reinforced = store.reinforce_memory(exact_duplicate.id)
        _record_lifecycle(
            store,
            project=project,
            session_id=session_id,
            memory_id=exact_duplicate.id,
            draft=draft,
            decision="reinforce_duplicate",
            risk="low",
            reason=(
                "Exact duplicate project memory already exists; strength and recurrence were reinforced."
                if reinforced
                else "Exact duplicate project memory already exists."
            ),
            relations=[Relation(exact_duplicate.id, "duplicate", "exact content duplicate")],
        )
        return ResolutionResult(
            memory_id=exact_duplicate.id,
            decision="reinforce_duplicate",
            relations=[Relation(exact_duplicate.id, "duplicate", "exact content duplicate")],
            diagnostics=[],
        )

    candidates = discover_conflict_candidates(store, project=project, draft=draft)
    if not candidates:
        status = _activation_status(draft)
        memory_id = _store_draft(
            store,
            project=project,
            session_id=session_id,
            draft=draft,
            status=status,
            reason=f"Isolated memory judge: {draft.reason} Normalized memory: {draft.content}.",
        )
        decision = "stored_active_no_conflict" if status == "active" else "stored_candidate_uncertain"
        _record_lifecycle(
            store,
            project=project,
            session_id=session_id,
            memory_id=memory_id,
            draft=draft,
            decision=decision,
            risk="medium" if status == "candidate" else "low",
            reason="No project-local conflict candidates were found.",
            relations=[],
        )
        return ResolutionResult(memory_id=memory_id, decision=decision, relations=[], diagnostics=[])

    relation_payload = build_relation_judge_payload(draft=draft, existing_memories=candidates)
    relation_result = select_relation_judge(project).judge(relation_payload, project=project)
    allowed_ids = {memory.id for memory in candidates}
    relations = [
        relation
        for relation in relation_result.relations
        if relation.existing_memory_id in allowed_ids and relation.relation != "unrelated"
    ]
    if relation_result.diagnostics:
        memory_id = _store_draft(
            store,
            project=project,
            session_id=session_id,
            draft=draft,
            status="candidate",
            reason=f"Relation judge unavailable; held candidate. Isolated memory judge: {draft.reason}",
        )
        _record_lifecycle(
            store,
            project=project,
            session_id=session_id,
            memory_id=memory_id,
            draft=draft,
            decision="held_relation_judge_unavailable",
            risk="medium",
            reason="Conflict candidates existed but relation judgment was unavailable or invalid.",
            relations=[],
            relation_result=relation_result,
        )
        return ResolutionResult(
            memory_id=memory_id,
            decision="held_relation_judge_unavailable",
            relations=[],
            diagnostics=relation_result.diagnostics,
        )

    if not relations:
        status = _activation_status(draft)
        memory_id = _store_draft(
            store,
            project=project,
            session_id=session_id,
            draft=draft,
            status=status,
            reason=f"Isolated memory judge: {draft.reason} Normalized memory: {draft.content}.",
        )
        decision = "stored_active_no_conflict" if status == "active" else "stored_candidate_uncertain"
        _record_lifecycle(
            store,
            project=project,
            session_id=session_id,
            memory_id=memory_id,
            draft=draft,
            decision=decision,
            risk="medium" if status == "candidate" else "low",
            reason="Relation judge found no relevant project-local conflicts.",
            relations=[],
            relation_result=relation_result,
        )
        return ResolutionResult(memory_id=memory_id, decision=decision, relations=[], diagnostics=[])

    primary = _primary_relation(relations)
    existing = _memory_by_id(candidates, primary.existing_memory_id)
    if primary.relation == "duplicate":
        reinforced = store.reinforce_memory(primary.existing_memory_id)
        _record_lifecycle(
            store,
            project=project,
            session_id=session_id,
            memory_id=primary.existing_memory_id,
            draft=draft,
            decision="reinforce_duplicate",
            risk="low",
            reason=(
                f"Relation judge classified candidate as duplicate of {primary.existing_memory_id}: {primary.reason}"
                if reinforced
                else f"Relation judge classified candidate as duplicate of {primary.existing_memory_id}."
            ),
            relations=relations,
            relation_result=relation_result,
        )
        return ResolutionResult(
            memory_id=primary.existing_memory_id,
            decision="reinforce_duplicate",
            relations=relations,
            diagnostics=[],
        )

    if primary.relation == "conflicts":
        memory_id = _store_draft(
            store,
            project=project,
            session_id=session_id,
            draft=draft,
            status="candidate",
            reason=f"Candidate held due to project-local memory conflict: {primary.reason}",
        )
        _link_relation(store, project=project, source_id=memory_id, relation=primary)
        _record_lifecycle(
            store,
            project=project,
            session_id=session_id,
            memory_id=memory_id,
            draft=draft,
            decision="held_candidate_conflict",
            risk="high",
            reason=primary.reason,
            relations=relations,
            relation_result=relation_result,
        )
        return ResolutionResult(memory_id=memory_id, decision="held_candidate_conflict", relations=relations, diagnostics=[])

    if primary.relation == "candidate_supersedes":
        if draft.source_integrity == "clean":
            memory_id = _store_draft(
                store,
                project=project,
                session_id=session_id,
                draft=draft,
                status="active",
                reason=f"Candidate supersedes existing project memory {primary.existing_memory_id}: {primary.reason}",
            )
            store.supersede(primary.existing_memory_id, memory_id)
            _link_relation(store, project=project, source_id=memory_id, relation=primary)
            _record_lifecycle(
                store,
                project=project,
                session_id=session_id,
                memory_id=memory_id,
                draft=draft,
                decision="superseded_existing_project_memory",
                risk="medium",
                reason=primary.reason,
                relations=relations,
                relation_result=relation_result,
            )
            return ResolutionResult(
                memory_id=memory_id,
                decision="superseded_existing_project_memory",
                relations=relations,
                diagnostics=[],
            )
        memory_id = _store_draft(
            store,
            project=project,
            session_id=session_id,
            draft=draft,
            status="candidate",
            reason=f"Uncertain candidate held instead of superseding {primary.existing_memory_id}: {primary.reason}",
        )
        _link_relation(store, project=project, source_id=memory_id, relation=primary)
        _record_lifecycle(
            store,
            project=project,
            session_id=session_id,
            memory_id=memory_id,
            draft=draft,
            decision="held_candidate_supersession_uncertain",
            risk="medium",
            reason=primary.reason,
            relations=relations,
            relation_result=relation_result,
        )
        return ResolutionResult(
            memory_id=memory_id,
            decision="held_candidate_supersession_uncertain",
            relations=relations,
            diagnostics=[],
        )

    if primary.relation == "existing_supersedes":
        if existing:
            store.reinforce_memory(existing.id, confidence_delta=0.02, importance_delta=0.01)
        memory_id = _store_draft(
            store,
            project=project,
            session_id=session_id,
            draft=draft,
            status="candidate",
            reason=f"Candidate held because existing project memory takes precedence: {primary.reason}",
        )
        _link_relation(store, project=project, source_id=memory_id, relation=primary)
        _record_lifecycle(
            store,
            project=project,
            session_id=session_id,
            memory_id=memory_id,
            draft=draft,
            decision="held_existing_supersedes_candidate",
            risk="medium",
            reason=primary.reason,
            relations=relations,
            relation_result=relation_result,
        )
        return ResolutionResult(
            memory_id=memory_id,
            decision="held_existing_supersedes_candidate",
            relations=relations,
            diagnostics=[],
        )

    status = _activation_status(draft)
    memory_id = _store_draft(
        store,
        project=project,
        session_id=session_id,
        draft=draft,
        status=status,
        reason=f"Complementary project memory stored: {primary.reason}",
    )
    if primary.relation == "complementary":
        _link_relation(store, project=project, source_id=memory_id, relation=primary)
    decision = "stored_complementary" if primary.relation == "complementary" else (
        "stored_active_no_conflict" if status == "active" else "stored_candidate_uncertain"
    )
    _record_lifecycle(
        store,
        project=project,
        session_id=session_id,
        memory_id=memory_id,
        draft=draft,
        decision=decision,
        risk="medium" if status == "candidate" else "low",
        reason=primary.reason,
        relations=relations,
        relation_result=relation_result,
    )
    return ResolutionResult(memory_id=memory_id, decision=decision, relations=relations, diagnostics=[])


def discover_conflict_candidates(store: Store, *, project: Project, draft: MemoryDraft) -> list[Memory]:
    memories = [
        memory
        for memory in store.list_memories(project_id=project.id, include_global=False, status=None)
        if memory.status in {"active", "candidate"}
    ]
    draft_terms = _terms(" ".join([draft.content, draft.source_quote, " ".join(draft.tags)]))
    scored: list[tuple[float, Memory]] = []
    for memory in memories:
        memory_terms = _terms(" ".join([memory.content, memory.source_quote or "", " ".join(memory.tags)]))
        score = _discovery_score(draft_terms, memory_terms, draft, memory)
        if score > 0:
            scored.append((score, memory))
    if not scored:
        return sorted(memories, key=lambda item: (item.importance, item.updated_at), reverse=True)[:MAX_RELATION_CANDIDATES]
    return [
        memory
        for _, memory in sorted(scored, key=lambda item: (item[0], item[1].importance, item[1].updated_at), reverse=True)[
            :MAX_RELATION_CANDIDATES
        ]
    ]


def build_relation_judge_payload(*, draft: MemoryDraft, existing_memories: list[Memory]) -> dict[str, object]:
    return {
        "candidate_memory": {
            "content": draft.content,
            "type": draft.type,
            "source_integrity": draft.source_integrity,
            "source_quote": draft.source_quote,
        },
        "existing_memories": [
            {
                "id": memory.id,
                "content": memory.content[:MAX_RELATION_CONTENT_CHARS],
                "type": memory.type,
                "status": memory.status,
                "source_quote": (memory.source_quote or "")[:MAX_RELATION_CONTENT_CHARS],
            }
            for memory in existing_memories
        ],
        "allowed_relations": sorted(RELATION_TYPES),
        "instructions": [
            "Classify only durable semantic relationships between the candidate and existing project memories.",
            "Do not decide lifecycle status or storage actions.",
            "Do not rely on language-specific keyword rules.",
            "Return unrelated when the existing memory should not affect storage policy.",
        ],
    }


def select_relation_judge(project: Project) -> RelationJudge:
    mode = os.environ.get("MEMASSIST_MEMORY_RELATION_JUDGE_MODE", "auto").lower()
    if mode in {"off", "disabled"}:
        return UnavailableRelationJudge(reason=f"relation judge mode is {mode}")
    if os.environ.get(RELATION_JUDGE_ACTIVE_ENV) == "1" or os.environ.get(INTERPRETER_ACTIVE_ENV) == "1":
        return UnavailableRelationJudge(reason="recursion guard active")
    if os.environ.get(RELATION_JUDGE_FIXTURE_ENV):
        return FixtureRelationJudge()
    adapters: tuple[tuple[str, type[_SubprocessRelationJudge]], ...] = (
        ("codex", CodexRelationJudge),
        ("claude", ClaudeRelationJudge),
    )
    installed = {status.tool for status in status_tools(project, tools=[], scope="project") if status.installed}
    for tool_name, adapter in adapters:
        if tool_name in installed:
            return adapter()
    return UnavailableRelationJudge(reason="no initialized tool with relation judge adapter")


class UnavailableRelationJudge:
    name = "unavailable"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason

    def judge(self, payload: dict[str, object], *, project: Project) -> RelationJudgeResult:
        return RelationJudgeResult([], self.name, [self.reason], payload)


class FixtureRelationJudge:
    name = "fixture"

    def judge(self, payload: dict[str, object], *, project: Project) -> RelationJudgeResult:
        fixture = os.environ.get(RELATION_JUDGE_FIXTURE_ENV, "")
        try:
            relations = _relations_from_output(fixture)
        except ValueError as exc:
            return RelationJudgeResult([], self.name, [f"invalid relation judge output: {exc}"], payload)
        return RelationJudgeResult(relations, self.name, [], payload)


class _SubprocessRelationJudge:
    name = "subprocess"
    executable = ""

    def _command(self, instruction: str, project: Project) -> list[str]:
        raise NotImplementedError

    def _stdin(self) -> int | None:
        return None

    def judge(self, payload: dict[str, object], *, project: Project) -> RelationJudgeResult:
        if not shutil.which(self.executable):
            return RelationJudgeResult([], self.name, [f"{self.executable} executable not found"], payload)
        env = os.environ.copy()
        env[RELATION_JUDGE_ACTIVE_ENV] = "1"
        env[INTERPRETER_ACTIVE_ENV] = "1"
        timeout = float(os.environ.get(RELATION_JUDGE_TIMEOUT_ENV, "60"))
        instruction = _build_relation_instruction(payload)
        command = self._command(instruction, project)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
                stdin=self._stdin(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return RelationJudgeResult([], self.name, [f"relation judge failed: {exc}"], payload)
        try:
            relations = _relations_from_output(result.stdout or result.stderr)
        except ValueError as exc:
            diagnostics = [f"invalid relation judge output: {exc}", f"returncode={result.returncode}"]
            if result.stderr:
                diagnostics.append(f"stderr={_snippet(result.stderr)}")
            if result.stdout:
                diagnostics.append(f"stdout={_snippet(result.stdout)}")
            return RelationJudgeResult([], self.name, diagnostics, payload)
        return RelationJudgeResult(relations, self.name, [], payload)


class CodexRelationJudge(_SubprocessRelationJudge):
    name = "codex"
    executable = "codex"

    def _command(self, instruction: str, project: Project) -> list[str]:
        return [
            "codex",
            "exec",
            "--json",
            "--cd",
            str(project.root),
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            instruction,
        ]


class ClaudeRelationJudge(_SubprocessRelationJudge):
    name = "claude"
    executable = "claude"

    def _command(self, instruction: str, project: Project) -> list[str]:
        model = os.environ.get("MEMASSIST_MEMORY_RELATION_JUDGE_MODEL", "haiku")
        return [
            "claude",
            "-p",
            "--model",
            model,
            "--output-format",
            "json",
            instruction,
        ]

    def _stdin(self) -> int | None:
        return subprocess.DEVNULL


def _build_relation_instruction(payload: dict[str, object]) -> str:
    return (
        "You are an isolated memory relation judge. Return only one compact JSON object. "
        "Return {\"relations\":[...]} where each item has existing_memory_id, relation, and reason. "
        "relation must be one of duplicate, complementary, candidate_supersedes, existing_supersedes, "
        "conflicts, unrelated. Judge only semantic relationships between the candidate and listed "
        "project-local memories. Do not decide storage, lifecycle status, activation, archival, or reinforcement. "
        "Do not use language-specific keyword rules. "
        "Payload: "
        + json.dumps(payload, ensure_ascii=False)
    )


def _relations_from_output(output: str) -> list[Relation]:
    raw = _extract_json_object(output)
    value = raw.get("relations")
    if not isinstance(value, list):
        raise ValueError("relations must be a list")
    relations: list[Relation] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("relation item must be an object")
        existing_memory_id = _required_str(item, "existing_memory_id")
        relation = _required_str(item, "relation")
        if relation not in RELATION_TYPES:
            raise ValueError(f"invalid relation: {relation}")
        relations.append(
            Relation(
                existing_memory_id=existing_memory_id,
                relation=relation,
                reason=_required_str(item, "reason"),
            )
        )
    return relations


def _extract_json_object(output: str) -> dict[str, object]:
    text = output.strip()
    if not text:
        raise ValueError("empty output")
    candidates = [text]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            candidates.append(stripped)
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                for key in ("result", "text"):
                    nested = value.get(key)
                    if isinstance(nested, str):
                        candidates.extend(_json_candidates(nested))
                item = value.get("item")
                if isinstance(item, dict):
                    item_text = item.get("text")
                    if isinstance(item_text, str):
                        candidates.extend(_json_candidates(item_text))
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    errors: list[str] = []
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
            continue
        if isinstance(value, dict) and "relations" in value:
            return value
    raise ValueError("no relation JSON object found" + (f": {errors[-1]}" if errors else ""))


def _json_candidates(value: str) -> list[str]:
    out = [value]
    match = re.search(r"\{.*\}", value, flags=re.DOTALL)
    if match:
        out.append(match.group(0))
    return out


def _store_draft(
    store: Store,
    *,
    project: Project,
    session_id: str,
    draft: MemoryDraft,
    status: str,
    reason: str,
) -> str:
    return store.add_memory(
        scope_type="project",
        project_id=project.id,
        session_id=session_id,
        type=draft.type,
        content=draft.content,
        reason=reason,
        tags=draft.tags,
        paths=[],
        status=status,
        importance=0.85 if status == "active" else 0.65,
        confidence=0.85 if draft.source_integrity == "clean" else 0.7,
        source_kind="isolated_memory_judge",
        source_ref=draft.source_ref,
        source_quote=draft.source_quote,
        source_ids=draft.source_ids,
    )


def _record_lifecycle(
    store: Store,
    *,
    project: Project,
    session_id: str,
    memory_id: str | None,
    draft: MemoryDraft,
    decision: str,
    risk: str,
    reason: str,
    relations: list[Relation],
    relation_result: RelationJudgeResult | None = None,
) -> None:
    candidate: dict[str, object] = {
        "judge": draft.judge,
        "source_event_id": draft.source_event_id,
        "source_id": draft.source_id,
        "source_ref": draft.source_ref,
        "source_quote": draft.source_quote,
        "relations": [relation.as_dict() for relation in relations],
    }
    if draft.payload:
        candidate["payload"] = draft.payload
    if relation_result:
        candidate["relation_judge"] = relation_result.as_dict()
    store.add_lifecycle_event(
        session_id=session_id,
        project_id=project.id,
        memory_id=memory_id,
        candidate=candidate,
        decision=decision,
        risk=risk,
        reason=reason,
    )


def _link_relation(store: Store, *, project: Project, source_id: str, relation: Relation) -> None:
    store.add_memory_link(
        source_id=source_id,
        target_id=relation.existing_memory_id,
        project_id=project.id,
        relation=relation.relation,
        strength=1.0,
        reason=relation.reason,
    )


def _primary_relation(relations: list[Relation]) -> Relation:
    return sorted(relations, key=lambda relation: RELATION_PRIORITY[relation.relation], reverse=True)[0]


def _memory_by_id(memories: list[Memory], memory_id: str) -> Memory | None:
    for memory in memories:
        if memory.id == memory_id:
            return memory
    return None


def _activation_status(draft: MemoryDraft) -> str:
    return "active" if draft.source_integrity == "clean" else "candidate"


def _discovery_score(draft_terms: set[str], memory_terms: set[str], draft: MemoryDraft, memory: Memory) -> float:
    if not draft_terms or not memory_terms:
        return 0.0
    overlap = len(draft_terms & memory_terms) / max(len(draft_terms), 1)
    tag_overlap = len(set(draft.tags) & set(memory.tags)) / max(len(set(draft.tags)), 1)
    return overlap + tag_overlap * 0.25


def _terms(text: str) -> set[str]:
    normalized = text.lower()
    raw = re.findall(r"[A-Za-z0-9_가-힣]+", normalized)
    return {token for token in raw if len(token) > 1}


def _required_str(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _snippet(text: str) -> str:
    return " ".join(text.strip().split())[:400]
