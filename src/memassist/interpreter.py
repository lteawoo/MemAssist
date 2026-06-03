from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .integrations import status_tools
from .project import Project

ENFORCEMENT_VALUES = {"none", "remember", "warn", "block"}
DEFAULT_CONFIDENCE_THRESHOLD = 0.85
DRAFT_CONFIDENCE_THRESHOLD = 0.60
INTERPRETER_ACTIVE_ENV = "MEMASSIST_INTERPRETER_ACTIVE"


@dataclass(frozen=True)
class DirectiveCandidate:
    is_directive: bool
    intent: str
    subject: str
    enforcement: str
    scope_terms: list[str]
    candidate_paths: list[str]
    confidence: float
    rationale: str
    normalized_prompt: str
    original_prompt: str

    def as_dict(self) -> dict[str, object]:
        return {
            "is_directive": self.is_directive,
            "intent": self.intent,
            "subject": self.subject,
            "enforcement": self.enforcement,
            "scope_terms": self.scope_terms,
            "candidate_paths": self.candidate_paths,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "normalized_prompt": self.normalized_prompt,
            "original_prompt": self.original_prompt,
        }

    @property
    def memory_enforcement(self) -> str:
        return self.enforcement if self.enforcement in {"warn", "block"} else "none"


@dataclass(frozen=True)
class InterpretationResult:
    candidate: DirectiveCandidate
    adapter_name: str
    fallback_used: bool
    diagnostics: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.as_dict(),
            "adapter_name": self.adapter_name,
            "fallback_used": self.fallback_used,
            "diagnostics": self.diagnostics,
        }


class DirectiveInterpreter(Protocol):
    name: str

    def interpret(self, prompt: str, *, project: Project) -> InterpretationResult:
        ...


def interpret_directive(prompt: str, *, project: Project) -> InterpretationResult:
    interpreter = select_interpreter(project)
    return interpreter.interpret(prompt, project=project)


def select_interpreter(project: Project) -> DirectiveInterpreter:
    mode = os.environ.get("MEMASSIST_INTERPRETER_MODE", "auto").lower()
    if mode in {"fallback", "off", "disabled"}:
        return FallbackDirectiveInterpreter(reason=f"interpreter mode is {mode}")
    if os.environ.get(INTERPRETER_ACTIVE_ENV) == "1":
        return FallbackDirectiveInterpreter(reason="recursion guard active")
    for status in status_tools(project, tools=[], scope="project"):
        if status.installed and status.tool == "codex":
            return CodexDirectiveInterpreter()
    return FallbackDirectiveInterpreter(reason="no initialized tool with interpreter adapter")


def interpreter_diagnostics(project: Project) -> dict[str, object]:
    statuses = status_tools(project, tools=[], scope="project")
    initialized = [status.tool for status in statuses if status.installed]
    selected = select_interpreter(project)
    llm_available = isinstance(selected, ToolDirectiveInterpreter) and bool(shutil.which(selected.executable))
    detail = getattr(selected, "detail", "ready")
    if isinstance(selected, ToolDirectiveInterpreter) and not llm_available:
        detail = f"{selected.executable} executable not found"
    return {
        "initialized_tools": initialized,
        "adapter": selected.name,
        "llm_available": llm_available,
        "fallback": isinstance(selected, FallbackDirectiveInterpreter),
        "detail": detail,
    }


class FallbackDirectiveInterpreter:
    name = "fallback"

    def __init__(self, *, reason: str = "fallback interpreter") -> None:
        self.detail = reason

    def interpret(self, prompt: str, *, project: Project) -> InterpretationResult:
        candidate = _fallback_candidate(prompt)
        diagnostics = [self.detail]
        return InterpretationResult(candidate, self.name, True, diagnostics)


class ToolDirectiveInterpreter:
    name = "tool"
    executable = ""

    def interpret(self, prompt: str, *, project: Project) -> InterpretationResult:
        fixture = os.environ.get("MEMASSIST_INTERPRETER_FIXTURE_RESPONSE")
        if fixture:
            return _result_from_tool_output(fixture, adapter_name=self.name, original_prompt=prompt)
        if not shutil.which(self.executable):
            fallback = FallbackDirectiveInterpreter(reason=f"{self.executable} executable not found")
            result = fallback.interpret(prompt, project=project)
            return InterpretationResult(result.candidate, self.name, True, result.diagnostics)
        try:
            output = self._run_tool(prompt, project=project)
        except (OSError, subprocess.TimeoutExpired) as exc:
            fallback = FallbackDirectiveInterpreter(reason=f"{self.name} interpreter failed: {exc}")
            result = fallback.interpret(prompt, project=project)
            return InterpretationResult(result.candidate, self.name, True, result.diagnostics)
        return _result_from_tool_output(output, adapter_name=self.name, original_prompt=prompt)

    def _run_tool(self, prompt: str, *, project: Project) -> str:
        raise NotImplementedError


class CodexDirectiveInterpreter(ToolDirectiveInterpreter):
    name = "codex"
    executable = "codex"

    def _run_tool(self, prompt: str, *, project: Project) -> str:
        env = os.environ.copy()
        env[INTERPRETER_ACTIVE_ENV] = "1"
        timeout = float(os.environ.get("MEMASSIST_INTERPRETER_TIMEOUT", "8"))
        instruction = (
            "Return only one compact JSON object for a memassist directive interpretation. "
            "Fields: is_directive boolean, intent string, subject string, enforcement one of "
            "none/remember/warn/block, scope_terms string array, candidate_paths string array, "
            "confidence number 0..1, rationale string, normalized_prompt string. "
            "Do not include markdown. User prompt: "
            + json.dumps(prompt, ensure_ascii=False)
        )
        result = subprocess.run(
            ["codex", "exec", "--cd", str(project.root), instruction],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.stdout or result.stderr


def _result_from_tool_output(output: str, *, adapter_name: str, original_prompt: str) -> InterpretationResult:
    diagnostics: list[str] = []
    try:
        raw = _extract_json_object(output)
        candidate = _candidate_from_mapping(raw, original_prompt=original_prompt)
    except ValueError as exc:
        fallback = _fallback_candidate(original_prompt)
        diagnostics.append(f"invalid interpreter output: {exc}")
        return InterpretationResult(fallback, adapter_name, True, diagnostics)
    return InterpretationResult(candidate, adapter_name, False, diagnostics)


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
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                message = event.get("message") or event.get("output") or event.get("result")
                if isinstance(message, str):
                    candidates.append(message.strip())
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object found")


def _candidate_from_mapping(raw: dict[str, object], *, original_prompt: str) -> DirectiveCandidate:
    is_directive = _required_bool(raw, "is_directive")
    intent = _required_str(raw, "intent")
    subject = _required_str(raw, "subject")
    enforcement = _required_str(raw, "enforcement").lower()
    if enforcement not in ENFORCEMENT_VALUES:
        raise ValueError(f"invalid enforcement: {enforcement}")
    confidence = _float_between(raw.get("confidence"), 0.0, 1.0)
    rationale = _required_str(raw, "rationale")
    normalized_prompt = _required_str(raw, "normalized_prompt")
    return DirectiveCandidate(
        is_directive=is_directive,
        intent=intent,
        subject=subject,
        enforcement=enforcement,
        scope_terms=_string_list(raw.get("scope_terms")),
        candidate_paths=_string_list(raw.get("candidate_paths")),
        confidence=confidence,
        rationale=rationale,
        normalized_prompt=normalized_prompt,
        original_prompt=original_prompt,
    )


def _fallback_candidate(prompt: str) -> DirectiveCandidate:
    content = " ".join(prompt.strip().split())
    lowered = _normalize_text(content)
    is_directive = _looks_like_directive(lowered)
    enforcement = _fallback_enforcement(lowered) if is_directive else "none"
    scope_terms = _fallback_scope_terms(lowered)
    subject = " ".join(scope_terms[:4])
    confidence = _fallback_confidence(lowered, is_directive=is_directive, enforcement=enforcement, scope_terms=scope_terms)
    return DirectiveCandidate(
        is_directive=is_directive,
        intent="future_memory_directive" if is_directive else "none",
        subject=subject,
        enforcement=enforcement,
        scope_terms=scope_terms,
        candidate_paths=[],
        confidence=confidence,
        rationale="deterministic fallback interpretation",
        normalized_prompt=content,
        original_prompt=prompt,
    )


def _normalize_text(text: str) -> str:
    replacements = {
        "리프레쉬": "리프레시",
        "담부터": "다음부터",
        "고치기": "수정",
        "고칠": "수정",
        "말해줘": "알려",
        "말해 줘": "알려",
    }
    lowered = text.lower()
    for src, dst in replacements.items():
        lowered = lowered.replace(src, dst)
    return lowered


def _looks_like_directive(text: str) -> bool:
    future_terms = {"remember", "always", "앞으로", "항상", "다음부터", "담부터", "future"}
    gate_terms = {"before", "먼저", "전에", "전", "확인", "허락", "알려", "ask", "tell"}
    directive_terms = {"기억", "해야", "하지마", "하지 마", "묻지", "수정하지", "바꾸지"}
    return any(term in text for term in future_terms | directive_terms) or (
        any(term in text for term in gate_terms) and any(term in text for term in {"수정", "변경", "change", "edit"})
    )


def _fallback_enforcement(text: str) -> str:
    block_terms = {"do not", "don't", "never", "must not", "without asking", "절대", "금지", "하지마", "하지 마", "수정하지", "바꾸지"}
    warn_terms = {"warn", "remind", "careful", "알려", "확인", "허락", "먼저", "전에", "before", "ask", "tell"}
    if any(term in text for term in block_terms):
        return "block"
    if any(term in text for term in warn_terms):
        return "warn"
    return "remember"


def _fallback_scope_terms(text: str) -> list[str]:
    terms: list[str] = []
    synonyms = {
        "리프레시": "refresh",
        "토큰": "token",
        "인증": "auth",
        "정책": "policy",
        "세션": "session",
        "쿠키": "cookie",
    }
    for korean, english in synonyms.items():
        if korean in text:
            terms.append(english)
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text):
        terms.append(token.lower())
    return list(dict.fromkeys(terms))


def _fallback_confidence(text: str, *, is_directive: bool, enforcement: str, scope_terms: list[str]) -> float:
    if not is_directive:
        return 0.0
    score = 0.55
    if enforcement in {"warn", "block"}:
        score += 0.18
    if scope_terms:
        score += 0.12
    if any(term in text for term in {"앞으로", "항상", "다음부터", "remember", "always"}):
        score += 0.10
    if any(term in text for term in {"확인", "알려", "먼저", "허락", "before", "ask", "tell"}):
        score += 0.08
    return min(0.95, score)


def _float_between(value: object, minimum: float, maximum: float) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError("confidence must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be numeric") from exc
    if not minimum <= number <= maximum:
        raise ValueError("confidence out of range")
    return number


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
