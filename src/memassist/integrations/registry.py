from __future__ import annotations

from memassist.project import Project

from .base import InstallResult, IntegrationStatus, ToolIntegration, ToolMode
from .claude import ClaudeIntegration
from .codex import CodexIntegration
from .opencode import OpenCodeIntegration

SUPPORTED_TOOLS = ("codex", "claude", "opencode")

_REGISTRY: dict[str, ToolIntegration] = {
    "codex": CodexIntegration(),
    "claude": ClaudeIntegration(),
    "opencode": OpenCodeIntegration(),
}


def normalize_tools(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        pieces = [piece.strip().lower() for piece in raw.split(",")]
    else:
        pieces = []
        for item in raw:
            pieces.extend(piece.strip().lower() for piece in item.split(","))
    tools = [piece for piece in pieces if piece]
    if not tools:
        return []
    if "all" in tools:
        return list(SUPPORTED_TOOLS)
    unknown = sorted(set(tools) - set(SUPPORTED_TOOLS))
    if unknown:
        supported = ", ".join((*SUPPORTED_TOOLS, "all"))
        raise ValueError(f"unknown tool(s): {', '.join(unknown)}; supported: {supported}")
    return list(dict.fromkeys(tools))


def install_tools(project: Project, *, tools: list[str], mode: ToolMode, scope: str) -> list[InstallResult]:
    return [_REGISTRY[tool].install(project, mode=mode, scope=scope) for tool in tools]


def uninstall_tools(project: Project, *, tools: list[str], scope: str) -> list[InstallResult]:
    return [_REGISTRY[tool].uninstall(project, scope=scope) for tool in tools]


def repair_tools(project: Project, *, tools: list[str], mode: ToolMode, scope: str) -> list[InstallResult]:
    for tool in tools:
        _REGISTRY[tool].uninstall(project, scope=scope)
    return install_tools(project, tools=tools, mode=mode, scope=scope)


def status_tools(project: Project, *, tools: list[str], scope: str) -> list[IntegrationStatus]:
    selected = tools or list(SUPPORTED_TOOLS)
    return [_REGISTRY[tool].status(project, scope=scope) for tool in selected]
