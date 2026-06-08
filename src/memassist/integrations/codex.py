from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from memassist.hooks import codex_hooks_status, install_codex_hooks, uninstall_codex_hooks
from memassist.paths import project_memassist_home
from memassist.project import Project

from .base import (
    InstallResult,
    IntegrationStatus,
    ToolMode,
    TurnSource,
    content_text,
    iter_transcript_objects,
    lifecycle_capabilities,
)


class CodexIntegration:
    name = "codex"

    def install(self, project: Project, *, mode: ToolMode, scope: str) -> InstallResult:
        local_home = project_memassist_home(project.root) if scope == "project" else None
        path = install_codex_hooks(scope=scope, project_root=project.root, mode=mode, memassist_home=local_home)
        return InstallResult(
            tool=self.name,
            action="install",
            path=path,
            installed=True,
            detail=f"installed Codex integration in {scope} scope",
        )

    def uninstall(self, project: Project, *, scope: str) -> InstallResult:
        path = uninstall_codex_hooks(scope=scope, project_root=project.root)
        return InstallResult(
            tool=self.name,
            action="uninstall",
            path=path,
            installed=False,
            detail=f"removed Codex integration from {scope} scope",
        )

    def status(self, project: Project, *, scope: str) -> IntegrationStatus:
        raw = codex_hooks_status(scope=scope, project_root=project.root)
        events = tuple(str(event) for event in raw.get("events", []))
        return IntegrationStatus(
            tool=self.name,
            installed=bool(raw["installed"]),
            path=Path(str(raw["path"])),
            events=events,
            detail="installed" if raw["installed"] else "not installed",
            capabilities=lifecycle_capabilities(events, isolated_memory_judgment=bool(raw["installed"])),
        )

    def extract_turn_source(self, payload: dict[str, Any], *, session_id: str) -> TurnSource | None:
        transcript_path = str(payload.get("transcript_path") or payload.get("transcriptPath") or "")
        if transcript_path:
            latest = ""
            for obj in iter_transcript_objects(Path(transcript_path)):
                text = _codex_user_text(obj)
                if text:
                    latest = text
            if latest.strip():
                return TurnSource(content=latest, source_ref=transcript_path, source_kind="transcript")
        history = _latest_user_prompt_from_codex_history(session_id)
        if history.strip():
            return TurnSource(
                content=history,
                source_ref=f"codex_history:{session_id}",
                source_kind="codex_history",
            )
        return None


def _codex_user_text(obj: dict[str, Any]) -> str:
    payload = obj.get("payload")
    if isinstance(payload, dict):
        if payload.get("type") == "user_message":
            message = payload.get("message")
            if isinstance(message, str):
                return message
        message = payload.get("message")
        if isinstance(message, dict):
            text = content_text(message.get("content"))
            if text:
                return text
    if obj.get("role") == "user":
        text = content_text(obj.get("content"))
        if text:
            return text
    return ""


def _latest_user_prompt_from_codex_history(session_id: str) -> str:
    if not session_id:
        return ""
    history_path = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "history.jsonl"
    if not history_path.exists():
        return ""
    latest = ""
    try:
        with history_path.open(encoding="utf-8", errors="replace") as file:
            for line in file:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict) or obj.get("session_id") != session_id:
                    continue
                text = obj.get("text")
                if isinstance(text, str) and text.strip():
                    latest = text
    except OSError:
        return ""
    return latest
