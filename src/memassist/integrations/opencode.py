from __future__ import annotations

import json
from pathlib import Path

from memassist.hooks import _python_hook_command
from memassist.project import Project

from .base import MODE_EVENTS, InstallResult, IntegrationStatus, ToolMode, lifecycle_capabilities


class OpenCodeIntegration:
    name = "opencode"

    def install(self, project: Project, *, mode: ToolMode, scope: str) -> InstallResult:
        path = _plugin_path(project, scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_plugin_source(mode), encoding="utf-8")
        return InstallResult(
            tool=self.name,
            action="install",
            path=path,
            installed=True,
            detail=f"installed OpenCode integration in {scope} scope",
        )

    def uninstall(self, project: Project, *, scope: str) -> InstallResult:
        path = _plugin_path(project, scope)
        if path.exists():
            path.unlink()
        return InstallResult(
            tool=self.name,
            action="uninstall",
            path=path,
            installed=False,
            detail=f"removed OpenCode integration from {scope} scope",
        )

    def status(self, project: Project, *, scope: str) -> IntegrationStatus:
        path = _plugin_path(project, scope)
        if not path.exists():
            return IntegrationStatus(self.name, False, path, (), "not installed")
        text = path.read_text(encoding="utf-8")
        events = tuple(event for event in MODE_EVENTS["full"] if event in text)
        return IntegrationStatus(
            self.name,
            "memassist" in text,
            path,
            events,
            "installed" if "memassist" in text else "not installed",
            lifecycle_capabilities(events, llm_directive_interpretation=False),
        )


def _plugin_path(project: Project, scope: str) -> Path:
    if scope == "project":
        return project.root / ".opencode" / "plugins" / "memassist.js"
    if scope == "user":
        return Path("~/.config/opencode/plugins/memassist.js").expanduser()
    raise ValueError(f"invalid integration scope: {scope}")


def _plugin_source(mode: ToolMode) -> str:
    events = set(MODE_EVENTS[mode])
    command_by_event = {
        event: _python_hook_command(hook_event)
        for event, hook_event in {
            "UserPromptSubmit": "user-prompt-submit",
            "PreToolUse": "pre-tool-use",
            "PostToolUse": "post-tool-use",
            "Stop": "stop",
        }.items()
        if event in events
    }
    lines = [
        "import { spawnSync } from \"node:child_process\"",
        "",
        "const MEMASSIST_COMMANDS = " + json.dumps(command_by_event),
        "",
        "function runMemassist(eventName, payload) {",
        "  const command = MEMASSIST_COMMANDS[eventName]",
        "  if (!command) return null",
        "  const result = spawnSync(\"sh\", [\"-lc\", command], {",
        "    input: JSON.stringify(payload || {}),",
        "    encoding: \"utf-8\"",
        "  })",
        "  if (result.error) throw result.error",
        "  if (result.status && result.stderr) throw new Error(result.stderr)",
        "  if (!result.stdout) return null",
        "  try { return JSON.parse(result.stdout) } catch { return { raw: result.stdout } }",
        "}",
        "",
        "export const MemassistPlugin = async ({ directory }) => {",
        "  const hooks = {}",
    ]
    if "UserPromptSubmit" in events:
        lines.extend(
            [
                "  hooks[\"tui.prompt.append\"] = async (input, output) => {",
                "    const prompt = output?.prompt || output?.value || input?.prompt || input?.value || \"\"",
                "    const response = runMemassist(\"UserPromptSubmit\", { cwd: directory, prompt })",
                "    const context = response?.hookSpecificOutput?.additionalContext",
                "    if (context && output) output.prompt = [prompt, context].filter(Boolean).join(\"\\n\\n\")",
                "  }",
            ]
        )
    if "PreToolUse" in events:
        lines.extend(
            [
                "  hooks[\"tool.execute.before\"] = async (input, output) => {",
                "    const response = runMemassist(\"PreToolUse\", { cwd: directory, toolName: input?.tool, toolArgs: output?.args || input?.args || {} })",
                "    const decision = response?.hookSpecificOutput?.permissionDecision",
                "    if (decision === \"deny\") throw new Error(response.hookSpecificOutput.permissionDecisionReason || \"memassist denied this tool call\")",
                "  }",
            ]
        )
    if "PostToolUse" in events:
        lines.extend(
            [
                "  hooks[\"tool.execute.after\"] = async (input, output) => {",
                "    runMemassist(\"PostToolUse\", { cwd: directory, toolName: input?.tool, toolArgs: input?.args || {}, tool_response: output })",
                "  }",
            ]
        )
    if "Stop" in events:
        lines.extend(
            [
                "  hooks.event = async ({ event }) => {",
                "    if (event?.type === \"session.idle\") runMemassist(\"Stop\", { cwd: directory, event })",
                "  }",
            ]
        )
    lines.extend(["  return hooks", "}", ""])
    return "\n".join(lines)
