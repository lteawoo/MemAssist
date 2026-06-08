from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from memassist.project import Project

ToolMode = Literal["full", "context", "trace"]


@dataclass(frozen=True)
class TurnSource:
    """Turn-end user source extracted by an agent adapter.

    Defined here (not in memory_judge) so integration adapters can return it
    without importing memory_judge, keeping the dependency direction
    ``memory_judge -> integrations`` one-way.
    """

    content: str
    source_ref: str
    source_kind: str


def content_text(content: object) -> str:
    """Flatten a message ``content`` field (string or block list) into text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def payload_prompt(payload: dict[str, Any]) -> str:
    """Return a user prompt carried directly in a hook payload, if any."""
    for key in ("prompt", "message", "content", "user_prompt", "userPrompt"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def iter_transcript_objects(path: Path) -> Iterator[dict[str, Any]]:
    """Yield JSON objects from a JSONL transcript, skipping malformed lines."""
    if not path.exists():
        return
    try:
        with path.open(encoding="utf-8", errors="replace") as file:
            for line in file:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return

MODE_EVENTS: dict[ToolMode, tuple[str, ...]] = {
    "full": ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"),
    "context": ("UserPromptSubmit",),
    "trace": ("PostToolUse", "Stop"),
}


@dataclass(frozen=True)
class InstallResult:
    tool: str
    action: str
    path: Path
    installed: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "action": self.action,
            "path": str(self.path),
            "installed": self.installed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class IntegrationStatus:
    tool: str
    installed: bool
    path: Path
    events: tuple[str, ...]
    detail: str
    capabilities: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "installed": self.installed,
            "path": str(self.path),
            "events": list(self.events),
            "detail": self.detail,
            "capabilities": dict(self.capabilities),
        }


class ToolIntegration(Protocol):
    name: str

    def install(self, project: Project, *, mode: ToolMode, scope: str) -> InstallResult:
        ...

    def uninstall(self, project: Project, *, scope: str) -> InstallResult:
        ...

    def status(self, project: Project, *, scope: str) -> IntegrationStatus:
        ...

    def extract_turn_source(self, payload: dict[str, Any], *, session_id: str) -> TurnSource | None:
        """Extract the turn-end user source from this agent's payload/transcript.

        Return ``None`` when this adapter cannot find a user source in the
        payload. Direct payload prompts are handled by the dispatcher before
        adapters are consulted, so adapters only need to parse their own
        transcript/history format.
        """
        ...


def lifecycle_capabilities(events: tuple[str, ...], *, isolated_memory_judgment: bool = False) -> dict[str, bool]:
    event_set = set(events)
    return {
        "prompt_memory_injection": "UserPromptSubmit" in event_set,
        "pre_tool_trace_capture": "PreToolUse" in event_set,
        "pre_tool_trace_only": "PreToolUse" in event_set,
        "post_tool_trace_extraction": "PostToolUse" in event_set,
        "stop_lifecycle_memory_extraction": "Stop" in event_set,
        "isolated_memory_judgment": isolated_memory_judgment,
    }
