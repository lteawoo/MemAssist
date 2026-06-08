from __future__ import annotations

from typing import Any

from memassist.project import Project

from .base import InstallResult, IntegrationStatus, ToolIntegration, ToolMode, TurnSource, payload_prompt
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


def extract_turn_source(agent: str | None, payload: dict[str, Any], *, session_id: str) -> TurnSource | None:
    """Dispatch turn-end source extraction to the calling agent's adapter.

    Order: (1) a prompt carried directly in the payload (agent-independent),
    (2) the adapter registered for ``agent`` when the identifier is known,
    (3) a compatible fallback that tries every registered adapter when the
    identifier is absent or unrecognized. Adding a new agent only requires
    registering its adapter; this dispatch needs no per-agent branching.
    """
    direct = payload_prompt(payload)
    if direct.strip():
        return TurnSource(content=direct, source_ref="hook_payload", source_kind="hook_payload")
    integration = _REGISTRY.get(agent) if agent else None
    if integration is not None:
        return integration.extract_turn_source(payload, session_id=session_id)
    for candidate in _REGISTRY.values():
        source = candidate.extract_turn_source(payload, session_id=session_id)
        if source is not None:
            return source
    return None
