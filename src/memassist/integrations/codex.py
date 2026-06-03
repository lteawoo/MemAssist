from __future__ import annotations

from pathlib import Path

from memassist.hooks import codex_hooks_status, install_codex_hooks, uninstall_codex_hooks
from memassist.project import Project

from .base import InstallResult, IntegrationStatus, ToolMode, lifecycle_capabilities


class CodexIntegration:
    name = "codex"

    def install(self, project: Project, *, mode: ToolMode, scope: str) -> InstallResult:
        path = install_codex_hooks(scope=scope, project_root=project.root, mode=mode)
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
            capabilities=lifecycle_capabilities(events, llm_directive_interpretation=bool(raw["installed"])),
        )
