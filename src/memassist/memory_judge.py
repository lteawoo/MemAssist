from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .conflict_resolver import MemoryDraft, resolve_memory_conflicts
from .integrations import status_tools
from .integrations.base import TurnSource
from .integrations.registry import extract_turn_source as _dispatch_turn_source
from .models import MEMORY_TYPES
from .project import Project
from .source_ledger import append_source_record
from .storage import Store

# Hook recursion guard. The judge sets this on child tool subprocesses so any
# memassist hook invoked by that child session short-circuits in cmd_hook_event.
# Name and string value are preserved from the removed interpreter module so the
# guard behaves identically for already-installed hooks.
INTERPRETER_ACTIVE_ENV = "MEMASSIST_INTERPRETER_ACTIVE"

JUDGE_ACTIVE_ENV = "MEMASSIST_MEMORY_JUDGE_ACTIVE"
JUDGE_DEBUG_ENV = "MEMASSIST_MEMORY_JUDGE_DEBUG"
JUDGE_FIXTURE_ENV = "MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"
JUDGE_TIMEOUT_ENV = "MEMASSIST_MEMORY_JUDGE_TIMEOUT"
MAX_JUDGE_SOURCE_CHARS = 600
MAX_JUDGE_SOURCE_CHUNK_CHARS = 1200
MAX_HINTS = 8
MAX_DEBUG_CHARS = 4000
MAX_JUDGE_ATTEMPTS = 3
SOURCE_EVENT_TYPE = "memory_source_observed"
SOURCE_INTEGRITY_LEVELS = {"clean", "uncertain", "contaminated"}


@dataclass(frozen=True)
class MemorySourceEvent:
    event_id: str
    source_id: str | None = None


@dataclass(frozen=True)
class ExtractedMemory:
    content: str
    type: str
    source_quote: str

    def as_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "type": self.type,
            "source_quote": self.source_quote,
        }


@dataclass(frozen=True)
class MemoryJudgeCandidate:
    memory: ExtractedMemory | None
    source_integrity: str
    reason: str
    reject_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "memory": self.memory.as_dict() if self.memory else None,
            "source_integrity": self.source_integrity,
            "reason": self.reason,
            "reject_reason": self.reject_reason,
        }


@dataclass(frozen=True)
class MemoryJudgeResult:
    candidate: MemoryJudgeCandidate | None
    adapter_name: str
    diagnostics: list[str]
    payload: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.as_dict() if self.candidate else None,
            "adapter_name": self.adapter_name,
            "diagnostics": self.diagnostics,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class StoredJudgment:
    event_id: str
    memory_id: str | None
    decision: str


# Turn-end source type now lives in integrations.base so agent adapters can
# produce it without importing memory_judge. Alias kept for internal references.
TurnEndMemorySource = TurnSource


class MemoryJudge(Protocol):
    name: str

    def judge(self, payload: dict[str, object], *, project: Project) -> MemoryJudgeResult:
        ...


def extract_turn_end_memory_source(
    payload: dict[str, Any],
    *,
    session_id: str,
    agent: str | None = None,
) -> TurnSource | None:
    """Extract the turn-end user source via the calling agent's adapter.

    Dispatch order (direct payload prompt -> known agent adapter -> compatible
    fallback) lives in ``integrations.registry`` so per-agent parsing stays in
    the adapters and adding a new agent needs no change here.
    """
    return _dispatch_turn_source(agent, payload, session_id=session_id)


def observe_turn_end_memory_source(
    store: Store,
    *,
    session_id: str,
    project_id: str,
    payload: dict[str, Any],
    agent: str | None = None,
) -> MemorySourceEvent | None:
    source = extract_turn_end_memory_source(payload, session_id=session_id, agent=agent)
    if not source or not source.content.strip():
        return None
    source_record = append_source_record(
        store.path.parent,
        kind=source.source_kind,
        text=source.content,
        session_id=session_id,
        project_id=project_id,
        source_ref=source.source_ref,
        agent=agent,
    )
    existing = _find_existing_source_event(
        store,
        session_id=session_id,
        content=source.content,
        source_ref=source.source_ref,
    )
    if existing:
        return MemorySourceEvent(event_id=existing, source_id=source_record.id)
    event_id = store.add_trace_event(
        session_id=session_id,
        project_id=project_id,
        event_type="memory_source_observed",
        tool_name="memassist",
        input_json={
            "source_id": source_record.id,
            "source_hash": source_record.source_hash,
            "source_role": "user",
            "content": source.content,
            "source_ref": source.source_ref,
            "source_kind": source.source_kind,
            "agent": agent,
            "status": "pending",
        },
    )
    return MemorySourceEvent(event_id=event_id, source_id=source_record.id)


def process_memory_intent_event(
    store: Store,
    *,
    project: Project,
    session_id: str,
    source_event_id: str,
) -> StoredJudgment:
    source_event = _trace_event_by_id(store, source_event_id)
    if not source_event:
        return StoredJudgment(event_id="", memory_id=None, decision="missing_source_event")
    payload = build_judge_payload(store, project=project, source_event=source_event)
    judge = select_memory_judge(project)
    result = judge.judge(payload, project=project)
    if result.candidate is None and result.adapter_name != "unavailable":
        prior_failures = _failed_attempt_count(store, session_id=session_id, source_event_id=source_event_id)
        if prior_failures + 1 >= MAX_JUDGE_ATTEMPTS:
            judgment_event_id = _record_judge_result(
                store, session_id=session_id, project_id=project.id,
                source_event_id=source_event_id, result=result, gave_up=True,
            )
            return StoredJudgment(event_id=judgment_event_id, memory_id=None, decision="judge_failed_gave_up")
        failure_event_id = _record_judge_failure(
            store, session_id=session_id, project_id=project.id,
            source_event_id=source_event_id, result=result,
        )
        return StoredJudgment(event_id=failure_event_id, memory_id=None, decision="judge_failed_retry")
    judgment_event_id = _record_judge_result(
        store, session_id=session_id, project_id=project.id,
        source_event_id=source_event_id, result=result,
    )
    memory_id = store_judge_result(
        store, project=project, session_id=session_id,
        source_event_id=source_event_id, result=result,
    )
    return StoredJudgment(
        event_id=judgment_event_id, memory_id=memory_id,
        decision="stored" if memory_id else "not_stored",
    )


def process_pending_memory_intents(
    store: Store,
    *,
    project: Project,
    session_id: str,
    limit: int = 10,
) -> list[StoredJudgment]:
    results: list[StoredJudgment] = []
    for event in pending_memory_source_events(store, session_id=session_id, limit=limit):
        results.append(
            process_memory_intent_event(
                store,
                project=project,
                session_id=session_id,
                source_event_id=str(event["id"]),
            )
        )
    return results


def pending_memory_source_events(
    store: Store,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    processed = _terminal_source_event_ids(store, session_id=session_id, project_id=project_id)
    rows = _source_event_rows(store, session_id=session_id, project_id=project_id)
    pending: list[dict[str, Any]] = []
    for row in rows:
        event = dict(row)
        if event["id"] in processed:
            continue
        pending.append(event)
        if len(pending) >= limit:
            break
    return pending


def build_judge_payload(
    store: Store,
    *,
    project: Project,
    source_event: dict[str, Any],
) -> dict[str, object]:
    input_json = _event_input(source_event)
    full_content = str(input_json.get("content") or "")
    content = full_content[:MAX_JUDGE_SOURCE_CHARS]
    session_id = str(source_event.get("session_id") or "")
    recent_files = _recent_files(store, session_id=session_id)
    trace_signals = _session_trace_signals(store, session_id=session_id)
    return {
        "source_event": {
            "id": str(source_event["id"]),
            "source_id": str(input_json.get("source_id") or ""),
            "source_hash": str(input_json.get("source_hash") or ""),
            "role": "user",
            "content": content,
            "content_chunks": _judge_source_chunks(full_content),
            "content_chars": len(full_content),
            "content_truncated": len(full_content) > len(content),
            "source_ref": str(input_json.get("source_ref") or source_event["id"]),
            "source_kind": str(input_json.get("source_kind") or source_event.get("event_type") or "unknown"),
        },
        "project_hints": {
            "recent_files": recent_files[:MAX_HINTS],
            "project_root_name": project.root.name,
        },
        "session_trace": trace_signals,
        "exclusions": [
            "assistant_responses",
            "retrieved_memory_context",
            "system_prompts",
            "developer_prompts",
            "agent_reasoning",
            "full_conversation_history",
        ],
    }


def select_memory_judge(project: Project) -> MemoryJudge:
    mode = os.environ.get("MEMASSIST_MEMORY_JUDGE_MODE", "auto").lower()
    if mode in {"off", "disabled"}:
        return UnavailableMemoryJudge(reason=f"memory judge mode is {mode}")
    if os.environ.get(JUDGE_ACTIVE_ENV) == "1" or os.environ.get(INTERPRETER_ACTIVE_ENV) == "1":
        return UnavailableMemoryJudge(reason="recursion guard active")
    if os.environ.get(JUDGE_FIXTURE_ENV):
        return FixtureMemoryJudge()
    adapters: tuple[tuple[str, type[_SubprocessMemoryJudge]], ...] = (
        ("codex", CodexMemoryJudge),
        ("claude", ClaudeMemoryJudge),
    )
    installed = {status.tool for status in status_tools(project, tools=[], scope="project") if status.installed}
    for tool_name, adapter in adapters:
        if tool_name in installed:
            return adapter()
    return UnavailableMemoryJudge(reason="no initialized tool with memory judge adapter")


def judge_backend_diagnostics(project: Project) -> dict[str, object]:
    """Report which initialized tool backs the isolated judge, for diagnostics.

    Env-independent (does not honor fixture/recursion-guard envs), so `doctor`
    reports the backend a normal hook run would select based on installed tools.
    """
    statuses = status_tools(project, tools=[], scope="project")
    installed = {status.tool for status in statuses if status.installed}
    adapters: tuple[tuple[str, type[_SubprocessMemoryJudge]], ...] = (
        ("codex", CodexMemoryJudge),
        ("claude", ClaudeMemoryJudge),
    )
    for tool_name, adapter in adapters:
        if tool_name in installed:
            executable = adapter().executable
            available = bool(shutil.which(executable))
            detail = (
                f"judge backend={tool_name} ({executable})"
                if available
                else f"judge backend={tool_name}; {executable} executable not found on PATH"
            )
            return {"backend": tool_name, "available": available, "detail": detail}
    return {
        "backend": None,
        "available": False,
        "detail": "no initialized tool provides an isolated judge backend",
    }


class UnavailableMemoryJudge:
    name = "unavailable"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason

    def judge(self, payload: dict[str, object], *, project: Project) -> MemoryJudgeResult:
        return MemoryJudgeResult(None, self.name, [self.reason], payload)


class FixtureMemoryJudge:
    name = "fixture"

    def judge(self, payload: dict[str, object], *, project: Project) -> MemoryJudgeResult:
        fixture = os.environ.get(JUDGE_FIXTURE_ENV, "")
        try:
            candidate = _candidate_from_output(fixture)
        except ValueError as exc:
            return MemoryJudgeResult(None, self.name, [f"invalid judge output: {exc}"], payload)
        return MemoryJudgeResult(candidate, self.name, [], payload)


def _build_judge_instruction(payload: dict[str, object]) -> str:
    return (
        "You are an isolated memory extraction judge. Return only one compact JSON object. "
        "If the source contains a durable future memory, return fields: "
        "memory object with content string, type one of "
        "fact/preference/rule/decision/lesson/workflow/open_thread/directive, "
        "and source_quote string; source_integrity one of clean/uncertain/contaminated; "
        "reason string. If there is no durable memory, return memory null, "
        "source_integrity, reject_reason string, and reason string. "
        "Do not return file path predictions. Use only source_event, project_hints, and session_trace. "
        "Do not infer from assistant responses or retrieved memory. "
        "source_event.content is a preview; when source_event.content_chunks exists, scan all chunks in order "
        "before deciding whether durable memory exists. "
        "Write memory.content as only the persistent future memory in the user's source language when possible. "
        "Do not include one-shot current-turn instructions, output formatting requests, or commands like "
        "'respond only OK' in memory.content unless the user explicitly asks to remember that response format "
        "for future turns. Preserve the full original wording separately in source_quote. "
        "Judge payload: "
        + json.dumps(payload, ensure_ascii=False)
    )


def _judge_subprocess_env() -> dict[str, str]:
    """Environment for the judge subprocess: de-nested from the host Claude Code
    session (strip CLAUDECODE / CLAUDE_CODE_* so a nested `claude -p` runs as a clean
    top-level invocation) plus the recursion-guard vars."""
    env = os.environ.copy()
    for key in list(env):
        if key == "CLAUDECODE" or key.startswith("CLAUDE_CODE_"):
            env.pop(key, None)
    env[JUDGE_ACTIVE_ENV] = "1"
    env[INTERPRETER_ACTIVE_ENV] = "1"
    return env


class _SubprocessMemoryJudge:
    """Run the isolated judge in a separate tool process.

    Subclasses set ``name``/``executable`` and build the command. The shared flow
    sets the recursion-guard environment, runs the process, writes optional debug
    records, and parses the judge JSON from the process output. The recursion guard
    is load-bearing: child sessions that re-trigger hooks short-circuit because
    ``cmd_hook_event`` returns early when ``INTERPRETER_ACTIVE_ENV`` is set.
    """

    name = "subprocess"
    executable = ""

    def _command(self, instruction: str, project: Project) -> list[str]:
        raise NotImplementedError

    def _stdin(self) -> int | None:
        return None

    def judge(self, payload: dict[str, object], *, project: Project) -> MemoryJudgeResult:
        if not shutil.which(self.executable):
            return MemoryJudgeResult(None, self.name, [f"{self.executable} executable not found"], payload)
        env = _judge_subprocess_env()
        timeout = float(os.environ.get(JUDGE_TIMEOUT_ENV, "60"))
        instruction = _build_judge_instruction(payload)
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
            diagnostics = [f"memory judge failed: {exc}"]
            if isinstance(exc, subprocess.TimeoutExpired):
                diagnostics.extend(_judge_process_diagnostics(None, exc.stdout, exc.stderr))
            _write_judge_debug(
                project,
                {
                    "adapter": self.name,
                    "command": _debug_command(command),
                    "error": str(exc),
                    "diagnostics": diagnostics,
                    "payload": payload,
                },
            )
            return MemoryJudgeResult(None, self.name, diagnostics, payload)
        _write_judge_debug(
            project,
            {
                "adapter": self.name,
                "command": _debug_command(command),
                "returncode": result.returncode,
                "stdout": _snippet(result.stdout),
                "stderr": _snippet(result.stderr),
                "payload": payload,
            },
        )
        try:
            candidate = _candidate_from_output(result.stdout or result.stderr)
        except ValueError as exc:
            diagnostics = [
                f"invalid judge output: {exc}",
                *_judge_process_diagnostics(result.returncode, result.stdout, result.stderr),
            ]
            return MemoryJudgeResult(None, self.name, diagnostics, payload)
        return MemoryJudgeResult(candidate, self.name, [], payload)


class CodexMemoryJudge(_SubprocessMemoryJudge):
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


class ClaudeMemoryJudge(_SubprocessMemoryJudge):
    name = "claude"
    executable = "claude"

    def _command(self, instruction: str, project: Project) -> list[str]:
        # Keep ``instruction`` last so _debug_command masks it. stdin is closed so
        # the CLI does not block waiting for piped input. No sandbox flag: the judge
        # prompt is read-only by construction, and recursion (not file writes) is the
        # real risk, which the guard environment covers. The judge runs on a low-cost
        # model by default so it is affordable per turn; override via env.
        model = os.environ.get("MEMASSIST_MEMORY_JUDGE_MODEL", "haiku")
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


def store_judge_result(
    store: Store,
    *,
    project: Project,
    session_id: str,
    source_event_id: str,
    result: MemoryJudgeResult,
) -> str | None:
    candidate = result.candidate
    if not candidate or not candidate.memory:
        return None
    if candidate.source_integrity == "contaminated":
        return None
    memory_type = candidate.memory.type if candidate.memory.type in MEMORY_TYPES else "preference"
    content = candidate.memory.content.strip() or candidate.memory.source_quote.strip()
    if not content:
        return None
    source_event = _trace_event_by_id(store, source_event_id) or {}
    source_input = _event_input(source_event)
    source_ref = str(source_input.get("source_ref") or source_event_id)
    source_id = source_input.get("source_id")
    source_ids = [str(source_id)] if isinstance(source_id, str) and source_id else []
    draft = MemoryDraft(
        content=content,
        type=memory_type,
        source_integrity=candidate.source_integrity,
        source_quote=candidate.memory.source_quote,
        reason=candidate.reason,
        tags=_judge_tags(candidate),
        source_ref=source_ref,
        source_ids=source_ids,
        judge=candidate.as_dict(),
        source_event_id=source_event_id,
        source_id=source_id,
        payload=result.payload,
    )
    resolution = resolve_memory_conflicts(
        store,
        project=project,
        session_id=session_id,
        draft=draft,
    )
    return resolution.memory_id


def _record_judge_result(
    store: Store,
    *,
    session_id: str,
    project_id: str,
    source_event_id: str,
    result: MemoryJudgeResult,
    gave_up: bool = False,
) -> str:
    input_json: dict[str, object] = {
        "source_event_id": source_event_id,
        **result.as_dict(),
    }
    if gave_up:
        input_json["gave_up"] = True
    return store.add_trace_event(
        session_id=session_id,
        project_id=project_id,
        event_type="memory_judged",
        tool_name="memassist",
        input_json=input_json,
        tool_decision="store" if result.candidate and result.candidate.memory else "skip",
        files=[],
    )


def _record_judge_failure(
    store: Store,
    *,
    session_id: str,
    project_id: str,
    source_event_id: str,
    result: MemoryJudgeResult,
) -> str:
    return store.add_trace_event(
        session_id=session_id,
        project_id=project_id,
        event_type="memory_judge_failed",
        tool_name="memassist",
        input_json={
            "source_event_id": source_event_id,
            **result.as_dict(),
        },
        tool_decision="retry",
        files=[],
    )


def _failed_attempt_count(store: Store, *, session_id: str, source_event_id: str) -> int:
    count = 0
    for event in store.trace_events(session_id, limit=200):
        if event["event_type"] != "memory_judge_failed":
            continue
        if _event_input(dict(event)).get("source_event_id") == source_event_id:
            count += 1
    return count


def _candidate_from_output(output: str) -> MemoryJudgeCandidate:
    raw = _extract_json_object(output)
    source_integrity = _required_str(raw, "source_integrity")
    if source_integrity not in SOURCE_INTEGRITY_LEVELS:
        raise ValueError(f"invalid source_integrity: {source_integrity}")
    memory_value = raw.get("memory")
    memory: ExtractedMemory | None = None
    reject_reason: str | None = None
    if memory_value is None:
        reject_reason = _required_str(raw, "reject_reason")
    elif isinstance(memory_value, dict):
        memory_type = _required_str(memory_value, "type")
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"invalid memory type: {memory_type}")
        memory = ExtractedMemory(
            content=_required_str(memory_value, "content"),
            type=memory_type,
            source_quote=_required_str(memory_value, "source_quote"),
        )
    else:
        raise ValueError("memory must be an object or null")
    return MemoryJudgeCandidate(
        memory=memory,
        source_integrity=source_integrity,
        reason=_required_str(raw, "reason"),
        reject_reason=reject_reason,
    )


def _json_candidates(value: str) -> list[str]:
    """Parse-candidates from a string that may wrap a JSON object in a code fence
    or surrounding prose. Greedy {...} extraction drops surrounding ``` fences
    because they fall outside the first '{' and last '}'."""
    out = [value]
    match = re.search(r"\{.*\}", value, flags=re.DOTALL)
    if match:
        out.append(match.group(0))
    return out


def _extract_json_object(output: str) -> dict[str, object]:
    text = output.strip()
    if not text:
        raise ValueError("empty output")
    candidates = [text]
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            if "source_integrity" in event and "memory" in event:
                return event
            item = event.get("item")
            if isinstance(item, dict):
                item_text = item.get("text")
                if isinstance(item_text, str):
                    candidates.extend(_json_candidates(item_text))
            event_text = event.get("text")
            if isinstance(event_text, str):
                candidates.extend(_json_candidates(event_text))
            result_text = event.get("result")
            if isinstance(result_text, str):
                candidates.extend(_json_candidates(result_text))
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    # Prefer a judgment object. A CLI result envelope (e.g. Claude's
    # {"type":"result",...,"result":"<judge json>"}) is itself a valid dict, so
    # returning the first dict would yield the envelope; the judgment lives in the
    # nested "result"/"text" string that was appended to candidates above.
    fallback: dict[str, object] | None = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if "source_integrity" in value and "memory" in value:
                return value
            if fallback is None:
                fallback = value
    if fallback is not None:
        return fallback
    raise ValueError("no JSON object found")


def _judge_process_diagnostics(returncode: int | None, stdout: object, stderr: object) -> list[str]:
    diagnostics: list[str] = []
    if returncode is not None:
        diagnostics.append(f"judge returncode: {returncode}")
    stdout_text = _coerce_output(stdout)
    stderr_text = _coerce_output(stderr)
    if stdout_text:
        diagnostics.append(f"judge stdout: {_snippet(stdout_text)}")
    if stderr_text:
        diagnostics.append(f"judge stderr: {_snippet(stderr_text)}")
    if not stdout_text and not stderr_text:
        diagnostics.append("judge produced no stdout or stderr")
    return diagnostics


def _write_judge_debug(project: Project, record: dict[str, object]) -> None:
    debug = os.environ.get(JUDGE_DEBUG_ENV, "")
    if not debug:
        return
    path = Path(debug).expanduser() if debug not in {"1", "true", "yes"} else project.root / ".memassist" / "judge-debug.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # errors="replace" keeps debug logging from ever crashing the hook on lone
        # surrogates that may already exist in stored payloads from earlier bad runs.
        with path.open("a", encoding="utf-8", errors="replace") as file:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return


def _debug_command(command: list[str]) -> list[str]:
    if len(command) <= 2:
        return command
    return [*command[:-1], "<judge-instruction>"]


def _snippet(value: object, *, limit: int = MAX_DEBUG_CHARS) -> str:
    text = _coerce_output(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... <truncated {len(text) - limit} chars>"


def _coerce_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _trace_event_by_id(store: Store, event_id: str) -> dict[str, Any] | None:
    row = store.conn.execute("SELECT * FROM trace_events WHERE id = ?", (event_id,)).fetchone()
    return dict(row) if row else None


def _source_event_rows(
    store: Store,
    *,
    session_id: str | None,
    project_id: str | None,
) -> list[Any]:
    where = ["event_type = ?"]
    params: list[object] = [SOURCE_EVENT_TYPE]
    if session_id:
        where.append("session_id = ?")
        params.append(session_id)
    if project_id:
        where.append("project_id = ?")
        params.append(project_id)
    return store.conn.execute(
        f"SELECT * FROM trace_events WHERE {' AND '.join(where)} ORDER BY created_at",
        params,
    ).fetchall()


def _terminal_source_event_ids(
    store: Store,
    *,
    session_id: str | None,
    project_id: str | None,
) -> set[str]:
    where = ["event_type = ?"]
    params: list[object] = ["memory_judged"]
    if session_id:
        where.append("session_id = ?")
        params.append(session_id)
    if project_id:
        where.append("project_id = ?")
        params.append(project_id)
    rows = store.conn.execute(
        f"SELECT input_json FROM trace_events WHERE {' AND '.join(where)}",
        params,
    ).fetchall()
    processed: set[str] = set()
    for row in rows:
        source_event_id = _event_input({"input_json": row["input_json"]}).get("source_event_id")
        if isinstance(source_event_id, str):
            processed.add(source_event_id)
    return processed


def _find_existing_source_event(
    store: Store,
    *,
    session_id: str,
    content: str,
    source_ref: str,
) -> str | None:
    for event in store.trace_events(session_id, limit=200):
        if event["event_type"] != SOURCE_EVENT_TYPE:
            continue
        input_json = _event_input(dict(event))
        if input_json.get("content") == content and input_json.get("source_ref") == source_ref:
            return str(event["id"])
    return None


def _event_input(event: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(str(event.get("input_json") or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _session_trace_signals(store: Store, *, session_id: str) -> dict[str, object]:
    raw_commands: list[str] = []
    touched_files: list[str] = []
    denied_tool_events: list[dict[str, object]] = []
    seen_commands: set[str] = set()
    seen_files: set[str] = set()
    for event in store.trace_events(session_id, limit=100):
        try:
            payload = json.loads(event["input_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        command = payload.get("command")
        if not isinstance(command, str):
            tool_input = payload.get("tool_input") or payload.get("toolInput")
            if isinstance(tool_input, dict):
                command = tool_input.get("command")
        if isinstance(command, str) and command.strip() and command not in seen_commands:
            seen_commands.add(command)
            raw_commands.append(command[:300])

        try:
            files = json.loads(event["files_json"] or "[]")
        except json.JSONDecodeError:
            files = []
        for file in files:
            if isinstance(file, str) and file not in seen_files:
                seen_files.add(file)
                touched_files.append(file)

        if event["tool_decision"] in {"block", "deny"}:
            denied_tool_events.append(
                {
                    "tool_name": event["tool_name"] or "",
                    "tool_decision": event["tool_decision"],
                }
            )
    return {
        "raw_commands": raw_commands[:MAX_HINTS],
        "touched_files": touched_files[:MAX_HINTS],
        "denied_tool_events": denied_tool_events[:MAX_HINTS],
    }


def _judge_source_chunks(text: str) -> list[dict[str, object]]:
    if not text:
        return []
    chunks: list[dict[str, object]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + MAX_JUDGE_SOURCE_CHUNK_CHARS)
        chunks.append(
            {
                "index": len(chunks),
                "start": start,
                "end": end,
                "text": text[start:end],
            }
        )
        start = end
    return chunks


def _recent_files(store: Store, *, session_id: str) -> list[str]:
    files: list[str] = []
    for event in reversed(store.trace_events(session_id, limit=100)):
        try:
            raw = json.loads(event["files_json"] or "[]")
        except json.JSONDecodeError:
            raw = []
        for file in raw:
            if isinstance(file, str) and file not in files:
                files.append(file)
    return files

def _judge_tags(candidate: MemoryJudgeCandidate) -> list[str]:
    return ["isolated_judge"]


def _required_str(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()
