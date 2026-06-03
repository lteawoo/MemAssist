from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Row
from typing import Any

from .models import Memory
from .policy import append_protected_path
from .policy_simulator import PolicySimulationResult, simulate_protected_path
from .storage import Store


DIRECTIVE_TERMS = {
    "remember",
    "always",
    "must",
    "require",
    "앞으로",
    "항상",
    "기억",
    "해야",
    "받아야",
}

APPROVAL_POLICY_TERMS = {
    "approval",
    "approve",
    "confirmation",
    "승인",
    "확인",
}

@dataclass(frozen=True)
class ConfirmationResult:
    action: str
    applied: list[str]
    rejected: list[str]
    simulations: list[dict[str, object]]
    message: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "applied": self.applied,
            "rejected": self.rejected,
            "simulations": self.simulations,
            "message": self.message,
        }


def handle_direct_user_instruction(
    store: Store,
    *,
    project_root: Path,
    project_id: str,
    session_id: str,
    prompt: str,
) -> ConfirmationResult:
    content = _direct_instruction_content(prompt)
    if not content:
        return ConfirmationResult("none", [], [], [], None)
    enforcement = "require_approval" if _is_protective_instruction(content) else "none"

    existing = store.find_memory(
        project_id=project_id,
        content=content,
        type="preference",
        statuses=(
            "candidate",
            "draft",
            "auto_active",
            "active",
            "policy_active",
            "pinned",
            "long_term",
        ),
    )
    if existing:
        if enforcement == "none" and existing.status in {"active", "policy_active", "pinned", "long_term"}:
            return ConfirmationResult("direct_already_applied", [existing.id], [], [], None)
        if enforcement != "none" and existing.status == "policy_active":
            return ConfirmationResult("direct_already_applied", [existing.id], [], [], None)
        if enforcement != "none" and existing.enforcement != enforcement:
            store.update_enforcement(existing.id, enforcement)
            refreshed = store.get_memory(existing.id)
            if refreshed:
                existing = refreshed
        applied, simulation, decision = _apply_approved_memory(
            store,
            project_root=project_root,
            project_id=project_id,
            memory=existing,
        )
        simulations = [simulation.as_dict()] if simulation else []
        _record_confirmation_lifecycle(store, project_id=project_id, memory=existing, decision=decision)
        message = "Direct memassist instruction was applied." if applied else "Direct memassist instruction could not be applied."
        return ConfirmationResult("direct_apply", [existing.id] if applied else [], [], simulations, message)

    memory_id = store.add_memory(
        scope_type="project",
        project_id=project_id,
        session_id=session_id,
        type="preference",
        content=content,
        reason="User directly instructed memassist to remember this rule.",
        tags=_instruction_tags(content),
        status="active",
        importance=0.9,
        confidence=0.95,
        enforcement=enforcement,
        source_kind="user_prompt_directive",
        source_ref=session_id,
    )
    memory = store.get_memory(memory_id)
    if not memory:
        return ConfirmationResult("none", [], [], [], None)
    if enforcement == "none":
        store.add_lifecycle_event(
            session_id=session_id,
            project_id=project_id,
            memory_id=memory_id,
            candidate=memory.as_dict(),
            decision="user_prompt_active",
            risk="low",
            reason="User directly instructed memassist to remember this low-risk rule.",
        )
        return ConfirmationResult("direct_apply", [memory_id], [], [], "Direct memassist instruction was remembered.")

    applied, simulation, decision = _apply_approved_memory(
        store,
        project_root=project_root,
        project_id=project_id,
        memory=memory,
    )
    simulations = [simulation.as_dict()] if simulation else []
    _record_confirmation_lifecycle(store, project_id=project_id, memory=memory, decision=decision)
    message = "Direct memassist instruction was applied." if applied else "Direct memassist instruction could not be applied."
    return ConfirmationResult("direct_apply", [memory_id] if applied else [], [], simulations, message)


def _apply_approved_memory(
    store: Store,
    *,
    project_root: Path,
    project_id: str,
    memory: Memory,
) -> tuple[bool, PolicySimulationResult | None, str]:
    if memory.enforcement in {"require_approval", "block"}:
        protected_path = _infer_protected_path(store, memory, project_root)
        if protected_path:
            append_protected_path(project_root, protected_path)
            simulation = simulate_protected_path(project_root, protected_path)
            if simulation.passed:
                store.update_status(memory.id, "policy_active")
                return True, simulation, "confirmation_policy_active"
            store.update_status(memory.id, "active")
            return False, simulation, "confirmation_policy_failed"
        store.update_status(memory.id, "active")
        return True, None, "confirmation_active_no_path"
    store.update_status(memory.id, "active")
    return True, None, "confirmation_active"


def _record_confirmation_lifecycle(
    store: Store,
    *,
    project_id: str,
    memory: Memory,
    decision: str,
) -> None:
    store.add_lifecycle_event(
        session_id=memory.session_id,
        project_id=project_id,
        memory_id=memory.id,
        candidate=memory.as_dict(),
        decision=decision,
        risk="high" if memory.enforcement != "none" else "medium",
        reason="User directly approved this memory instruction.",
    )


def _infer_protected_path(store: Store, memory: Memory, project_root: Path) -> str | None:
    if memory.paths:
        return memory.paths[0]
    events = store.trace_events(memory.session_id) if memory.session_id else []
    files = _normalize_project_files(_files_from_events(events), project_root)
    content = memory.content.lower()
    matched = _best_content_path_match(files, content)
    if matched:
        return matched
    project_matches = _project_files_from_content(project_root, memory.content)
    return project_matches[0] if project_matches else None


def _files_from_events(events: list[Row]) -> list[str]:
    files: list[str] = []
    for event in events:
        try:
            raw_files = json.loads(event["files_json"] or "[]")
        except json.JSONDecodeError:
            raw_files = []
        for file in raw_files:
            if isinstance(file, str) and file not in files:
                files.append(file)
    return files


def _path_terms(path: str) -> list[str]:
    terms = [part for part in re.split(r"[\s/_.-]+", path) if len(part) > 2]
    text = path.lower()
    synonyms = {
        "리프레시": "refresh",
        "토큰": "token",
        "정책": "policy",
        "세션": "session",
        "쿠키": "cookie",
        "인증": "auth",
        "승인": "approval",
    }
    for korean, english in synonyms.items():
        if korean in text and english not in terms:
            terms.append(english)
    return terms


def _project_files_from_content(project_root: Path, content: str) -> list[str]:
    content_terms = set(_path_terms(content.lower()))
    if not content_terms:
        return []
    ignored_dirs = {".git", ".codex", ".memassist", "node_modules", "__pycache__"}
    scored: list[tuple[int, str]] = []
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(project_root).parts
        if any(part in ignored_dirs or part.startswith(".memassist-") for part in relative_parts):
            continue
        relative = path.relative_to(project_root).as_posix()
        path_terms = set(_path_terms(relative.lower()))
        score = len(content_terms & path_terms)
        if score:
            scored.append((score, relative))
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [relative for _score, relative in scored[:5]]


def _normalize_project_files(files: list[str], project_root: Path) -> list[str]:
    normalized: list[str] = []
    resolved_root = project_root.resolve()
    for file in files:
        path = Path(file)
        if path.is_absolute():
            try:
                file = path.resolve().relative_to(resolved_root).as_posix()
            except ValueError:
                file = path.as_posix()
        if file not in normalized:
            normalized.append(file)
    return normalized


def _best_content_path_match(files: list[str], content: str) -> str | None:
    content_terms = set(_path_terms(content))
    if not content_terms:
        return None
    scored: list[tuple[int, str]] = []
    for file in files:
        score = len(content_terms & set(_path_terms(file.lower())))
        if score:
            scored.append((score, file))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return scored[0][1]


def _direct_instruction_content(prompt: str) -> str:
    content = " ".join(prompt.strip().split())
    if not content:
        return ""
    if not _looks_like_new_user_directive(content.lower()):
        return ""
    return content[:300]


def _looks_like_new_user_directive(text: str) -> bool:
    has_directive = any(term in text for term in DIRECTIVE_TERMS)
    has_approval_policy = any(term in text for term in APPROVAL_POLICY_TERMS) and any(
        term in text for term in {"before", "change", "edit", "변경", "수정", "전에", "전"}
    )
    return has_directive or has_approval_policy


def _is_protective_instruction(content: str) -> bool:
    lowered = content.lower()
    return any(term in lowered for term in APPROVAL_POLICY_TERMS) and any(
        term in lowered for term in {"before", "change", "edit", "modify", "변경", "수정", "전에", "전"}
    )


def _instruction_tags(content: str) -> list[str]:
    lowered = content.lower()
    tags = ["explicit", "user_prompt"]
    if any(term in lowered for term in {"auth", "인증", "token", "토큰", "refresh", "리프레시"}):
        tags.append("auth")
    if any(term in lowered for term in {"token", "토큰"}):
        tags.append("token")
    if any(term in lowered for term in {"refresh", "리프레시"}):
        tags.append("refresh")
    if any(term in lowered for term in {"policy", "정책", "approval", "승인"}):
        tags.append("policy")
    return list(dict.fromkeys(tags))
