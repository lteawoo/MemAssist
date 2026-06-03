from __future__ import annotations

import json
from pathlib import Path

from .paths import codex_home


CODEX_MEMASSIST_HOOKS = {
    "PreToolUse": [
        {
            "matcher": "*",
            "hooks": [
                {
                    "type": "command",
                    "command": "python3 -m memassist hook pre-tool-use",
                    "timeout": 30,
                    "statusMessage": "memassist policy check",
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
                    "statusMessage": "memassist verification check",
                }
            ],
        }
    ],
}


def install_codex_hooks(*, scope: str = "project", project_root: Path | None = None, home: Path | None = None) -> Path:
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
    for event, groups in CODEX_MEMASSIST_HOOKS.items():
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


def _is_memassist_group(group: dict[str, object]) -> bool:
    for hook in group.get("hooks", []):  # type: ignore[union-attr]
        if isinstance(hook, dict) and "memassist" in str(hook.get("command", "")):
            return True
    return False
