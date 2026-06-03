from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from memassist.project import Project

ToolMode = Literal["full", "context", "trace", "guard"]

MODE_EVENTS: dict[ToolMode, tuple[str, ...]] = {
    "full": ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"),
    "context": ("UserPromptSubmit",),
    "trace": ("PostToolUse", "Stop"),
    "guard": ("PreToolUse",),
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

    def as_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "installed": self.installed,
            "path": str(self.path),
            "events": list(self.events),
            "detail": self.detail,
        }


class ToolIntegration(Protocol):
    name: str

    def install(self, project: Project, *, mode: ToolMode, scope: str) -> InstallResult:
        ...

    def uninstall(self, project: Project, *, scope: str) -> InstallResult:
        ...

    def status(self, project: Project, *, scope: str) -> IntegrationStatus:
        ...
