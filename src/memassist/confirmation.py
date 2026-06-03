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


AFFIRMATIVE = {
    "yes",
    "y",
    "ok",
    "okay",
    "approve",
    "remember",
    "응",
    "어",
    "그래",
    "좋아",
    "기억해",
    "승인",
}

NEGATIVE = {
    "no",
    "n",
    "reject",
    "cancel",
    "아니",
    "아니야",
    "하지마",
    "취소",
    "거절",
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


def classify_confirmation_response(prompt: str) -> str | None:
    normalized = prompt.strip().lower()
    if not normalized:
        return None
    if normalized in AFFIRMATIVE:
        return "approve"
    if normalized in NEGATIVE:
        return "reject"
    if any(word in normalized for word in AFFIRMATIVE) and len(normalized) <= 40:
        return "approve"
    if any(word in normalized for word in NEGATIVE) and len(normalized) <= 40:
        return "reject"
    return None


def pending_confirmation_context(memories: list[Memory]) -> str:
    if not memories:
        return ""
    lines = ["Pending memassist confirmation:"]
    for memory in memories[:3]:
        lines.append(f"- {memory.content}")
    lines.append("If the user approves, memassist will apply the memory automatically.")
    return "\n".join(lines)


def handle_pending_confirmation(
    store: Store,
    *,
    project_root: Path,
    project_id: str,
    prompt: str,
) -> ConfirmationResult:
    response = classify_confirmation_response(prompt)
    pending = store.list_memories(project_id=project_id, include_global=False, status="pending_confirmation")
    if not response or not pending:
        return ConfirmationResult("none", [], [], [], None)
    if response == "reject":
        rejected: list[str] = []
        for memory in pending:
            store.update_status(memory.id, "rejected")
            rejected.append(memory.id)
            store.add_lifecycle_event(
                session_id=memory.session_id,
                project_id=project_id,
                memory_id=memory.id,
                candidate=memory.as_dict(),
                decision="confirmation_rejected",
                risk="medium",
                reason="User rejected pending memory confirmation.",
            )
        return ConfirmationResult("reject", [], rejected, [], "Pending memassist memories were rejected.")

    applied: list[str] = []
    simulations: list[dict[str, object]] = []
    for memory in pending:
        simulation: PolicySimulationResult | None = None
        if memory.enforcement in {"require_approval", "block"}:
            protected_path = _infer_protected_path(store, memory, project_root)
            if protected_path:
                append_protected_path(project_root, protected_path)
                simulation = simulate_protected_path(project_root, protected_path)
                simulations.append(simulation.as_dict())
                if simulation.passed:
                    store.update_status(memory.id, "policy_active")
                    applied.append(memory.id)
                    decision = "confirmation_policy_active"
                else:
                    store.update_status(memory.id, "pending_confirmation")
                    decision = "confirmation_policy_failed"
            else:
                store.update_status(memory.id, "active")
                applied.append(memory.id)
                decision = "confirmation_active_no_path"
        else:
            store.update_status(memory.id, "active")
            applied.append(memory.id)
            decision = "confirmation_active"
        store.add_lifecycle_event(
            session_id=memory.session_id,
            project_id=project_id,
            memory_id=memory.id,
            candidate=memory.as_dict(),
            decision=decision,
            risk="high" if memory.enforcement != "none" else "medium",
            reason="User approved pending memory confirmation.",
        )
    message = "Pending memassist memories were applied." if applied else "No pending memories were applied."
    return ConfirmationResult("approve", applied, [], simulations, message)


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
    return [part for part in re.split(r"[\s/_.-]+", path) if len(part) > 2]


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
