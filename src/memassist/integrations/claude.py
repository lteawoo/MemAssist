from __future__ import annotations

import json
from pathlib import Path

from typing import Any

from memassist.hooks import _is_memassist_group, _python_hook_command
from memassist.paths import project_memassist_home
from memassist.project import Project

from .base import (
    MODE_EVENTS,
    InstallResult,
    IntegrationStatus,
    ToolMode,
    TurnSource,
    content_text,
    iter_transcript_objects,
    lifecycle_capabilities,
)

CLAUDE_EVENT_TO_HOOK = {
    "UserPromptSubmit": "user-prompt-submit",
    "PreToolUse": "pre-tool-use",
    "PostToolUse": "post-tool-use",
    "Stop": "stop",
}


class ClaudeIntegration:
    name = "claude"

    def install(self, project: Project, *, mode: ToolMode, scope: str) -> InstallResult:
        path = _settings_path(project, scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _read_json(path)
        hooks = data.setdefault("hooks", {})
        for event in MODE_EVENTS[mode]:
            existing = hooks.setdefault(event, [])
            existing[:] = [group for group in existing if not _is_memassist_group(group)]
            existing.append(_group(event, mode=mode, memassist_home=project_memassist_home(project.root) if scope == "project" else None))
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return InstallResult(
            tool=self.name,
            action="install",
            path=path,
            installed=True,
            detail=f"installed Claude Code integration in {scope} scope",
        )

    def uninstall(self, project: Project, *, scope: str) -> InstallResult:
        path = _settings_path(project, scope)
        if path.exists():
            data = _read_json(path)
            hooks = data.get("hooks", {})
            for event in list(hooks):
                hooks[event] = [group for group in hooks[event] if not _is_memassist_group(group)]
                if not hooks[event]:
                    del hooks[event]
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return InstallResult(
            tool=self.name,
            action="uninstall",
            path=path,
            installed=False,
            detail=f"removed Claude Code integration from {scope} scope",
        )

    def status(self, project: Project, *, scope: str) -> IntegrationStatus:
        path = _settings_path(project, scope)
        if not path.exists():
            return IntegrationStatus(self.name, False, path, (), "not installed")
        data = _read_json(path)
        events = tuple(
            event
            for event, groups in data.get("hooks", {}).items()
            if any(_is_memassist_group(group) for group in groups)
        )
        return IntegrationStatus(
            self.name,
            bool(events),
            path,
            events,
            "installed" if events else "not installed",
            lifecycle_capabilities(events, isolated_memory_judgment=bool(events)),
        )

    def extract_turn_source(self, payload: dict[str, Any], *, session_id: str) -> TurnSource | None:
        transcript_path = str(payload.get("transcript_path") or payload.get("transcriptPath") or "")
        if not transcript_path:
            return None
        latest = ""
        for obj in iter_transcript_objects(Path(transcript_path)):
            text = _claude_user_text(obj)
            if text:
                latest = text
        if not latest.strip():
            return None
        return TurnSource(content=latest, source_ref=transcript_path, source_kind="transcript")


def _claude_user_text(obj: dict[str, Any]) -> str:
    # Claude Code transcript line: {"type":"user","message":{"role":"user","content": str|list}}
    message = obj.get("message")
    is_user = obj.get("type") == "user" or (isinstance(message, dict) and message.get("role") == "user")
    if is_user and isinstance(message, dict):
        return content_text(message.get("content"))
    return ""


def _settings_path(project: Project, scope: str) -> Path:
    if scope == "project":
        return project.root / ".claude" / "settings.json"
    if scope == "user":
        return Path("~/.claude/settings.json").expanduser()
    raise ValueError(f"invalid integration scope: {scope}")


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _group(event: str, *, mode: ToolMode = "full", memassist_home: Path | None = None) -> dict[str, object]:
    hook_event = CLAUDE_EVENT_TO_HOOK[event]
    group: dict[str, object] = {
        "hooks": [
            {
                "type": "command",
                "command": _python_hook_command(hook_event, memassist_home=memassist_home, mode=mode, agent="claude"),
                "timeout": 30,
            }
        ]
    }
    if event in {"PreToolUse", "PostToolUse"}:
        group["matcher"] = "*"
    return group
