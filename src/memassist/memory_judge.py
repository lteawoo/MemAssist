from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .directives import _instruction_tags, _normalize_project_files
from .integrations import status_tools
from .models import ENFORCEMENTS, MEMORY_TYPES
from .project import Project
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
MAX_SOURCE_CHARS = 600
MAX_HINTS = 8
MAX_DEBUG_CHARS = 4000


@dataclass(frozen=True)
class MemoryIntentEvent:
    event_id: str
    immediate: bool


@dataclass(frozen=True)
class MemoryJudgeCandidate:
    should_store: bool
    memory_content: str
    source_quote: str
    memory_type: str
    enforcement: str
    activation: str
    candidate_paths: list[str]
    meaning_preserved: bool
    contamination_risk: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "should_store": self.should_store,
            "memory_content": self.memory_content,
            "source_quote": self.source_quote,
            "memory_type": self.memory_type,
            "enforcement": self.enforcement,
            "activation": self.activation,
            "candidate_paths": self.candidate_paths,
            "meaning_preserved": self.meaning_preserved,
            "contamination_risk": self.contamination_risk,
            "reason": self.reason,
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


class MemoryJudge(Protocol):
    name: str

    def judge(self, payload: dict[str, object], *, project: Project) -> MemoryJudgeResult:
        ...


def observe_memory_intent(
    store: Store,
    *,
    session_id: str,
    project_id: str,
    prompt: str,
) -> MemoryIntentEvent | None:
    content = " ".join(prompt.strip().split())
    if not content or not _looks_like_memory_intent(content):
        return None
    immediate = _looks_like_explicit_directive(content)
    event_id = store.add_trace_event(
        session_id=session_id,
        project_id=project_id,
        event_type="memory_intent_observed",
        tool_name="memassist",
        input_json={
            "source_role": "user",
            "content": content[:MAX_SOURCE_CHARS],
            "immediate": immediate,
            "status": "pending",
            "allowed_hints": {},
        },
    )
    return MemoryIntentEvent(event_id=event_id, immediate=immediate)


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
    judgment_event_id = _record_judge_result(
        store,
        session_id=session_id,
        project_id=project.id,
        source_event_id=source_event_id,
        result=result,
    )
    memory_id = store_judge_result(
        store,
        project=project,
        session_id=session_id,
        source_event_id=source_event_id,
        result=result,
    )
    return StoredJudgment(
        event_id=judgment_event_id,
        memory_id=memory_id,
        decision="stored" if memory_id else "not_stored",
    )


def process_pending_memory_intents(
    store: Store,
    *,
    project: Project,
    session_id: str,
    limit: int = 10,
) -> list[StoredJudgment]:
    processed = _processed_source_event_ids(store, session_id=session_id)
    results: list[StoredJudgment] = []
    for event in store.trace_events(session_id, limit=200):
        if event["event_type"] != "memory_intent_observed" or event["id"] in processed:
            continue
        results.append(
            process_memory_intent_event(
                store,
                project=project,
                session_id=session_id,
                source_event_id=str(event["id"]),
            )
        )
        if len(results) >= limit:
            break
    return results


def build_judge_payload(
    store: Store,
    *,
    project: Project,
    source_event: dict[str, Any],
) -> dict[str, object]:
    input_json = _event_input(source_event)
    content = str(input_json.get("content") or "")[:MAX_SOURCE_CHARS]
    session_id = str(source_event.get("session_id") or "")
    recent_files = _recent_files(store, session_id=session_id)
    conflicts = _memory_conflicts(store, project_id=project.id, content=content)
    return {
        "source_event": {
            "id": str(source_event["id"]),
            "role": "user",
            "content": content,
        },
        "project_hints": {
            "recent_files": recent_files[:MAX_HINTS],
            "project_root_name": project.root.name,
        },
        "existing_conflicts": conflicts[:MAX_HINTS],
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
        "You are an isolated memory judge. Return only one compact JSON object with fields: "
        "should_store boolean, memory_content string, source_quote string, memory_type one of "
        "fact/preference/rule/decision/lesson/workflow/open_thread/directive, enforcement one of "
        "none/warn/block, activation one of candidate/active/auto_active/rejected, "
        "candidate_paths string array, meaning_preserved boolean, "
        "contamination_risk one of low/medium/high, reason string. "
        "Use only source_event and project_hints. Do not infer from assistant responses or retrieved memory. "
        "Write memory_content as only the durable future memory in the user's source language when possible. "
        "Do not include one-shot current-turn instructions, output formatting requests, or commands like "
        "'respond only OK' in memory_content unless the user explicitly asks to remember that response format "
        "for future turns. Preserve the full original wording separately in source_quote. "
        "Judge payload: "
        + json.dumps(payload, ensure_ascii=False)
    )


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
        env = os.environ.copy()
        env[JUDGE_ACTIVE_ENV] = "1"
        env[INTERPRETER_ACTIVE_ENV] = "1"
        timeout = float(os.environ.get(JUDGE_TIMEOUT_ENV, "30"))
        instruction = _build_judge_instruction(payload)
        command = self._command(instruction, project)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
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
    if not candidate or not candidate.should_store or not candidate.meaning_preserved:
        return None
    if candidate.contamination_risk == "high":
        return None
    memory_type = candidate.memory_type if candidate.memory_type in MEMORY_TYPES else "preference"
    enforcement = candidate.enforcement if candidate.enforcement in ENFORCEMENTS else "none"
    normalized_paths = _normalize_project_files(candidate.candidate_paths, project.root)
    policy_like = enforcement in {"warn", "block"}
    status = _activation_status(candidate.activation)
    if status == "candidate" and policy_like:
        status = "active"
    content = (candidate.memory_content.strip() or candidate.source_quote.strip())[:500]
    if not content:
        return None
    existing = store.find_memory(
        project_id=project.id,
        content=content,
        type=memory_type,
        statuses=("candidate", "draft", "auto_active", "active", "long_term", "durable"),
    )
    if existing:
        return existing.id
    memory_id = store.add_memory(
        scope_type="project",
        project_id=project.id,
        session_id=session_id,
        type=memory_type,
        content=content,
        reason=(
            f"Isolated memory judge: {candidate.reason} "
            f"Normalized memory: {candidate.memory_content}."
        ),
        tags=_judge_tags(candidate),
        paths=normalized_paths,
        status=status,
        importance=0.85 if status in {"active", "auto_active"} else 0.65,
        confidence=0.85 if candidate.contamination_risk == "low" else 0.7,
        enforcement=enforcement,
        source_kind="isolated_memory_judge",
        source_ref=source_event_id,
    )
    memory = store.get_memory(memory_id)
    store.add_lifecycle_event(
        session_id=session_id,
        project_id=project.id,
        memory_id=memory_id,
        candidate={
            "judge": candidate.as_dict(),
            "source_event_id": source_event_id,
            "source_quote": candidate.source_quote,
            "payload": result.payload,
        },
        decision="isolated_judge_memory_stored",
        risk="medium" if policy_like else "low",
        reason=memory.reason if memory else candidate.reason,
    )
    return memory_id


def likely_distorted_or_echo_memory(content: str) -> bool:
    lowered = content.lower()
    assistant_echo = any(term in lowered for term in {"하겠습니다", "i will", "i'll", "앞으로"}) and any(
        term in lowered for term in {"승인 요청", "기억", "remember", "확인하고 진행"}
    )
    refresh_drift = "리프레시" in lowered and "토큰" not in lowered and any(
        term in lowered for term in {"브라우저", "페이지", "서버"}
    )
    return assistant_echo or refresh_drift


def _record_judge_result(
    store: Store,
    *,
    session_id: str,
    project_id: str,
    source_event_id: str,
    result: MemoryJudgeResult,
) -> str:
    files: list[str] = []
    if result.candidate:
        files = list(result.candidate.candidate_paths)
    return store.add_trace_event(
        session_id=session_id,
        project_id=project_id,
        event_type="memory_judged",
        tool_name="memassist",
        input_json={
            "source_event_id": source_event_id,
            **result.as_dict(),
        },
        policy_decision="store" if result.candidate and result.candidate.should_store else "skip",
        files=files,
    )


def _candidate_from_output(output: str) -> MemoryJudgeCandidate:
    raw = _extract_json_object(output)
    should_store = _required_bool(raw, "should_store")
    meaning_preserved = _required_bool(raw, "meaning_preserved")
    memory_type = _required_str(raw, "memory_type")
    if memory_type not in MEMORY_TYPES:
        raise ValueError(f"invalid memory_type: {memory_type}")
    enforcement = _required_str(raw, "enforcement")
    if enforcement not in ENFORCEMENTS:
        raise ValueError(f"invalid enforcement: {enforcement}")
    activation = _required_str(raw, "activation")
    if activation not in {"candidate", "active", "auto_active", "rejected"}:
        raise ValueError(f"invalid activation: {activation}")
    contamination_risk = _required_str(raw, "contamination_risk")
    if contamination_risk not in {"low", "medium", "high"}:
        raise ValueError(f"invalid contamination_risk: {contamination_risk}")
    return MemoryJudgeCandidate(
        should_store=should_store,
        memory_content=_required_str(raw, "memory_content"),
        source_quote=_required_str(raw, "source_quote"),
        memory_type=memory_type,
        enforcement=enforcement,
        activation=activation,
        candidate_paths=_string_list(raw.get("candidate_paths")),
        meaning_preserved=meaning_preserved,
        contamination_risk=contamination_risk,
        reason=_required_str(raw, "reason"),
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
            if "should_store" in event:
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
            if "should_store" in value:
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
        with path.open("a", encoding="utf-8") as file:
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


def _looks_like_memory_intent(content: str) -> bool:
    lowered = content.lower()
    terms = {
        "remember",
        "always",
        "앞으로",
        "항상",
        "다음부터",
        "담부터",
        "기억",
        "하지마",
        "하지 마",
        "건드리지",
        "먼저",
        "전에",
        "before",
        "ask",
        "tell",
    }
    return any(term in lowered for term in terms)


def _looks_like_explicit_directive(content: str) -> bool:
    lowered = content.lower()
    future = any(term in lowered for term in {"remember", "always", "앞으로", "항상", "다음부터", "담부터", "기억"})
    gate = any(term in lowered for term in {"먼저", "전에", "전", "before", "ask", "tell", "확인", "알려"})
    durable = any(term in lowered for term in {"하지마", "하지 마", "해야", "must", "require"})
    durable = durable or "건드리지" in lowered
    return future or durable or (gate and any(term in lowered for term in {"수정", "변경", "change", "edit", "고치"}))


def _trace_event_by_id(store: Store, event_id: str) -> dict[str, Any] | None:
    row = store.conn.execute("SELECT * FROM trace_events WHERE id = ?", (event_id,)).fetchone()
    return dict(row) if row else None


def _processed_source_event_ids(store: Store, *, session_id: str) -> set[str]:
    processed: set[str] = set()
    for event in store.trace_events(session_id, limit=200):
        if event["event_type"] != "memory_judged":
            continue
        source_event_id = _event_input(dict(event)).get("source_event_id")
        if isinstance(source_event_id, str):
            processed.add(source_event_id)
    return processed


def _event_input(event: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(str(event.get("input_json") or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


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


def _memory_conflicts(store: Store, *, project_id: str, content: str) -> list[dict[str, object]]:
    tokens = {token for token in re.findall(r"[A-Za-z0-9_가-힣]+", content.lower()) if len(token) > 2}
    conflicts: list[dict[str, object]] = []
    for memory in store.list_memories(project_id=project_id, include_global=True):
        haystack = " ".join([memory.content, " ".join(memory.tags), " ".join(memory.paths)]).lower()
        if tokens and tokens & set(re.findall(r"[A-Za-z0-9_가-힣]+", haystack)):
            conflicts.append(
                {
                    "id": memory.id,
                    "type": memory.type,
                    "status": memory.status,
                    "enforcement": memory.enforcement,
                }
            )
    return conflicts


def _activation_status(activation: str) -> str:
    if activation in {"active", "auto_active"}:
        return activation
    return "candidate"


def _judge_tags(candidate: MemoryJudgeCandidate) -> list[str]:
    tags = ["isolated_judge", candidate.memory_type]
    if candidate.enforcement != "none":
        tags.append(candidate.enforcement)
    tags.extend(_instruction_tags(candidate.source_quote))
    tags.extend(_instruction_tags(candidate.memory_content))
    return list(dict.fromkeys(tags))


def _required_bool(raw: dict[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def _required_str(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("expected string list")
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("expected string list")
        if item.strip():
            strings.append(item.strip())
    return strings
