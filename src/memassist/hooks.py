from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from .paths import codex_home

CODEX_MODE_EVENTS = {
    "full": ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"),
    "context": ("UserPromptSubmit",),
    "trace": ("PostToolUse", "Stop"),
}


CODEX_MEMASSIST_HOOKS = {
    "PreToolUse": [
        {
            "matcher": "*",
            "hooks": [
                {
                    "type": "command",
                    "command": "python3 -m memassist hook pre-tool-use",
                    "timeout": 30,
                    "statusMessage": "memassist trace logging",
                }
            ],
        }
    ],
    "PostToolUse": [
        {
            "matcher": "*",
            "hooks": [
                {
                    "type": "command",
                    "command": "python3 -m memassist hook post-tool-use",
                    "timeout": 30,
                    "statusMessage": "memassist trace logging",
                }
            ],
        }
    ],
    "UserPromptSubmit": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "python3 -m memassist hook user-prompt-submit",
                    "timeout": 30,
                    "statusMessage": "memassist memory retrieval",
                }
            ],
        }
    ],
    "Stop": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "python3 -m memassist hook stop",
                    "timeout": 30,
                    "statusMessage": "memassist memory lifecycle",
                }
            ],
        }
    ],
}


def install_codex_hooks(
    *,
    scope: str = "project",
    project_root: Path | None = None,
    home: Path | None = None,
    mode: str = "full",
    memassist_home: Path | None = None,
) -> Path:
    path = _hooks_path(scope=scope, project_root=project_root, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(".json.memassist.bak")
        if not backup.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"hooks": {}}
    hooks = data.setdefault("hooks", {})
    for event, groups in _codex_memassist_hooks(mode=mode, memassist_home=memassist_home).items():
        existing = hooks.setdefault(event, [])
        existing[:] = [group for group in existing if not _is_memassist_group(group)]
        existing.extend(groups)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def uninstall_codex_hooks(*, scope: str = "project", project_root: Path | None = None, home: Path | None = None) -> Path:
    path = _hooks_path(scope=scope, project_root=project_root, home=home)
    if not path.exists():
        return path
    data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})
    for event in list(hooks):
        hooks[event] = [group for group in hooks[event] if not _is_memassist_group(group)]
        if not hooks[event]:
            del hooks[event]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def codex_hooks_status(*, scope: str = "project", project_root: Path | None = None, home: Path | None = None) -> dict[str, object]:
    path = _hooks_path(scope=scope, project_root=project_root, home=home)
    if not path.exists():
        return {"installed": False, "scope": scope, "path": str(path), "events": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    events = [
        event
        for event, groups in data.get("hooks", {}).items()
        if any(_is_memassist_group(group) for group in groups)
    ]
    return {"installed": bool(events), "scope": scope, "path": str(path), "events": events}


def _hooks_path(*, scope: str, project_root: Path | None, home: Path | None) -> Path:
    if scope == "project":
        if project_root is None:
            project_root = Path.cwd()
        return project_root / ".codex" / "hooks.json"
    if scope == "user":
        return (home or codex_home()) / "hooks.json"
    raise ValueError(f"invalid hook scope: {scope}")


def _codex_memassist_hooks(*, mode: str = "full", memassist_home: Path | None = None) -> dict[str, list[dict[str, object]]]:
    if mode not in CODEX_MODE_EVENTS:
        raise ValueError(f"invalid hook mode: {mode}")
    hooks = json.loads(json.dumps(CODEX_MEMASSIST_HOOKS))
    hooks = {event: hooks[event] for event in CODEX_MODE_EVENTS[mode]}
    for groups in hooks.values():
        for group in groups:
            for hook in group.get("hooks", []):
                hook_event = str(hook["command"]).rsplit(" ", 1)[-1]
                hook["command"] = _python_hook_command(hook_event, memassist_home=memassist_home, mode=mode)
    return hooks


def _python_hook_command(hook_event: str, *, memassist_home: Path | None = None, mode: str | None = None) -> str:
    env: list[str] = []
    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "memassist").exists():
        env.append(f"PYTHONPATH={shlex.quote(str(source_root))}:$PYTHONPATH")
    home = str(memassist_home) if memassist_home else os.environ.get("MEMASSIST_HOME")
    if home:
        env.append(f"MEMASSIST_HOME={shlex.quote(home)}")
    if mode:
        env.append(f"MEMASSIST_HOOK_MODE={shlex.quote(mode)}")
    prefix = " ".join(env)
    command = f"python3 -m memassist hook {hook_event}"
    return f"{prefix} {command}" if prefix else command


def _is_memassist_group(group: dict[str, object]) -> bool:
    for hook in group.get("hooks", []):  # type: ignore[union-attr]
        if isinstance(hook, dict) and "memassist" in str(hook.get("command", "")):
            return True
    return False
