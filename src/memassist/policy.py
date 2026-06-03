from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"action": self.action, "reason": self.reason}


def default_policy_yaml() -> str:
    return """# memassist project policy
sensitive_paths: []
protected_paths: []
dangerous_commands: []
verification_commands: []
"""


class PolicyConfig:
    def __init__(
        self,
        *,
        sensitive_paths: list[str] | None = None,
        protected_paths: list[str] | None = None,
        dangerous_commands: list[str] | None = None,
        verification_commands: list[str] | None = None,
    ) -> None:
        self.sensitive_paths = list(sensitive_paths or [])
        self.protected_paths = list(protected_paths or [])
        self.dangerous_commands = list(dangerous_commands or [])
        self.verification_commands = list(verification_commands or [])


def load_policy(project_root: Path) -> PolicyConfig:
    path = project_root / ".memassist" / "policy.yaml"
    if not path.exists():
        return PolicyConfig()
    text = path.read_text(encoding="utf-8")
    data = _parse_minimal_yaml(text)
    return PolicyConfig(
        sensitive_paths=data.get("sensitive_paths") or [],
        protected_paths=data.get("protected_paths") or [],
        dangerous_commands=data.get("dangerous_commands") or [],
        verification_commands=data.get("verification_commands") or [],
    )


def append_protected_path(project_root: Path, path: str) -> bool:
    return _append_policy_path(project_root, "protected_paths", path)


def append_sensitive_path(project_root: Path, path: str) -> bool:
    return _append_policy_path(project_root, "sensitive_paths", path)


def _append_policy_path(project_root: Path, key: str, path: str) -> bool:
    policy_path = project_root / ".memassist" / "policy.yaml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    if not policy_path.exists():
        policy_path.write_text(default_policy_yaml(), encoding="utf-8")
    text = policy_path.read_text(encoding="utf-8")
    config = load_policy(project_root)
    existing_paths = config.protected_paths if key == "protected_paths" else config.sensitive_paths
    if path in existing_paths:
        return False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == f"{key}: []":
            lines[index] = f"{key}:"
            lines.insert(index + 1, f'  - "{path}"')
            policy_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            return True
        if line.strip() == f"{key}:":
            insert_at = index + 1
            while insert_at < len(lines) and lines[insert_at].startswith("  - "):
                insert_at += 1
            lines.insert(insert_at, f'  - "{path}"')
            policy_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            return True
    policy_path.write_text(text.rstrip() + f'\n\n{key}:\n  - "{path}"\n', encoding="utf-8")
    return True


def remove_protected_path(project_root: Path, path: str) -> bool:
    return _remove_policy_path(project_root, "protected_paths", path)


def remove_sensitive_path(project_root: Path, path: str) -> bool:
    return _remove_policy_path(project_root, "sensitive_paths", path)


def _remove_policy_path(project_root: Path, key: str, path: str) -> bool:
    policy_path = project_root / ".memassist" / "policy.yaml"
    if not policy_path.exists():
        return False
    config = load_policy(project_root)
    existing_paths = config.protected_paths if key == "protected_paths" else config.sensitive_paths
    if path not in existing_paths:
        return False
    lines = policy_path.read_text(encoding="utf-8").splitlines()
    changed = False
    next_lines: list[str] = []
    in_target_section = False
    for line in lines:
        stripped = line.strip()
        if line and not line.startswith(" ") and stripped.endswith(":"):
            in_target_section = stripped == f"{key}:"
        if in_target_section and stripped.startswith("-"):
            value = stripped[1:].strip().strip('"').strip("'")
            if value == path:
                changed = True
                continue
        next_lines.append(line)
    if changed:
        policy_path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")
    return changed


class PolicyEngine:
    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    def check_pre_tool(self, *, tool: str, args: dict[str, Any]) -> PolicyDecision:
        tool_name = tool.lower()
        if tool in {"shell", "bash", "Bash"}:
            command = str(args.get("command", ""))
            for pattern in self.config.dangerous_commands:
                if re.search(pattern, command):
                    return PolicyDecision("block", f"dangerous command matched: {pattern}")
        path = args.get("path") or args.get("file") or args.get("target")
        if path:
            path_text = str(path)
            if _matches_any(path_text, self.config.protected_paths):
                return PolicyDecision("block", f"protected path: {path_text}")
            if _matches_any(path_text, self.config.sensitive_paths):
                return PolicyDecision("warn", f"sensitive path: {path_text}")
        command_text = str(args.get("command", ""))
        if tool_name in {"apply_patch", "edit", "write"} and command_text:
            protected_path = _first_embedded_match(command_text, self.config.protected_paths)
            if protected_path:
                return PolicyDecision("block", f"protected path: {protected_path}")
            sensitive_path = _first_embedded_match(command_text, self.config.sensitive_paths)
            if sensitive_path:
                return PolicyDecision("warn", f"sensitive path: {sensitive_path}")
        return PolicyDecision("allow", "no policy matched")


def _matches_any(path: str, patterns: list[str]) -> bool:
    normalized = path[2:] if path.startswith("./") else path
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def _first_embedded_match(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        if _glob_literal_hint(pattern) and _glob_literal_hint(pattern) in text:
            return _glob_literal_hint(pattern)
    return None


def _glob_literal_hint(pattern: str) -> str:
    special = ["*", "?", "["]
    indexes = [pattern.find(char) for char in special if pattern.find(char) >= 0]
    end = min(indexes) if indexes else len(pattern)
    return pattern[:end].rstrip("/")


def _parse_minimal_yaml(text: str) -> dict[str, list[str]]:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None
    if isinstance(loaded, dict):
        return {
            key: value
            for key, value in loaded.items()
            if isinstance(value, list) and all(isinstance(item, str) for item in value)
        }

    parsed: dict[str, list[str]] = {}
    current_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(":") and not line.startswith("-"):
            current_key = line[:-1].strip()
            parsed.setdefault(current_key, [])
            continue
        if current_key and line.startswith("-"):
            value = line[1:].strip().strip('"').strip("'")
            parsed[current_key].append(value)
    return parsed
