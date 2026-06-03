from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .policy import PolicyConfig, PolicyDecision
from .storage import Store

APPROVAL_TTL_MINUTES = 10


@dataclass(frozen=True)
class ApprovalGrant:
    event_id: str
    paths: list[str]


def grant_approval_from_prompt(
    store: Store,
    *,
    session_id: str,
    project_id: str,
    prompt: str,
    policy: PolicyConfig,
) -> ApprovalGrant | None:
    if not is_explicit_approval_prompt(prompt):
        return None
    paths = _unique(policy.protected_paths + policy.sensitive_paths)
    if not paths:
        return None
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=APPROVAL_TTL_MINUTES)).isoformat(
        timespec="seconds"
    )
    event_id = store.add_trace_event(
        session_id=session_id,
        project_id=project_id,
        event_type="approval_granted",
        tool_name="memassist",
        input_json={
            "prompt": prompt,
            "paths": paths,
            "expires_at": expires_at,
            "scope": "session",
            "uses": 1,
        },
        files=paths,
    )
    return ApprovalGrant(event_id=event_id, paths=paths)


def apply_session_approval(
    store: Store,
    *,
    session_id: str,
    project_id: str,
    policy: PolicyConfig,
    tool: str,
    args: dict[str, Any],
    decision: PolicyDecision,
) -> PolicyDecision:
    if decision.action not in {"block", "warn"}:
        return decision
    target_paths = _target_policy_paths(tool=tool, args=args, policy=policy, reason=decision.reason)
    if not target_paths:
        return decision
    grant = _latest_matching_grant(
        store,
        session_id=session_id,
        project_id=project_id,
        target_paths=target_paths,
    )
    if not grant:
        return decision
    store.add_trace_event(
        session_id=session_id,
        project_id=project_id,
        event_type="approval_consumed",
        tool_name=tool or None,
        input_json={
            "grant_event_id": grant.event_id,
            "target_paths": target_paths,
            "original_decision": decision.as_dict(),
        },
        policy_decision="allow",
        files=target_paths,
    )
    return PolicyDecision("allow", f"user approval consumed for {', '.join(target_paths)}")


def is_explicit_approval_prompt(prompt: str) -> bool:
    normalized = re.sub(r"\s+", " ", prompt.strip().lower())
    if not normalized:
        return False
    exact_terms = {
        "승인",
        "허용",
        "진행",
        "계속",
        "approve",
        "approved",
        "allow",
        "go ahead",
        "continue",
        "proceed",
    }
    if normalized in exact_terms:
        return True
    return bool(
        re.fullmatch(
            r"(승인|허용|진행|계속)(해|해줘|합니다|하세요|해도 돼|해도 됩니다)?[.!?]?",
            normalized,
        )
    )


def _latest_matching_grant(
    store: Store,
    *,
    session_id: str,
    project_id: str,
    target_paths: list[str],
) -> ApprovalGrant | None:
    events = [dict(row) for row in store.trace_events(session_id, limit=200) if row["project_id"] == project_id]
    consumed = {
        _event_input(event).get("grant_event_id")
        for event in events
        if event["event_type"] == "approval_consumed"
    }
    for event in reversed(events):
        if event["event_type"] != "approval_granted" or event["id"] in consumed:
            continue
        input_json = _event_input(event)
        if _is_expired(str(input_json.get("expires_at") or "")):
            continue
        paths = [str(path) for path in input_json.get("paths", []) if isinstance(path, str)]
        if any(_matches_any(target, paths) for target in target_paths):
            return ApprovalGrant(event_id=str(event["id"]), paths=paths)
    return None


def _target_policy_paths(
    *,
    tool: str,
    args: dict[str, Any],
    policy: PolicyConfig,
    reason: str,
) -> list[str]:
    paths: list[str] = []
    explicit = args.get("path") or args.get("file") or args.get("target")
    if explicit:
        paths.append(str(explicit))
    reason_match = re.search(r"(?:protected|sensitive) path: (.+)$", reason)
    if reason_match:
        paths.append(reason_match.group(1).strip())
    command_text = str(args.get("command", ""))
    tool_name = tool.lower()
    if tool_name in {"apply_patch", "edit", "write"} and command_text:
        paths.extend(_paths_from_patch(command_text))
        for pattern in policy.protected_paths + policy.sensitive_paths:
            hint = _glob_literal_hint(pattern)
            if hint and hint in command_text:
                paths.append(hint)
    return _unique(paths)


def _paths_from_patch(text: str) -> list[str]:
    paths: list[str] = []
    for line in text.splitlines():
        match = re.match(r"\*\*\* (?:Add|Update|Delete) File: (.+)$", line)
        if match:
            paths.append(match.group(1).strip())
    return paths


def _event_input(event: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(str(event.get("input_json") or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _is_expired(expires_at: str) -> bool:
    if not expires_at:
        return True
    try:
        parsed = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def _matches_any(path: str, patterns: list[str]) -> bool:
    normalized = path[2:] if path.startswith("./") else path
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def _glob_literal_hint(pattern: str) -> str:
    indexes = [pattern.find(char) for char in ("*", "?", "[") if pattern.find(char) >= 0]
    end = min(indexes) if indexes else len(pattern)
    return pattern[:end].rstrip("/")


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
