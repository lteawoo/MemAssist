from __future__ import annotations

import json
from pathlib import Path


def default_verification_config_yaml() -> str:
    return """# memassist project verification config
verification_commands: []
"""


class VerificationConfig:
    def __init__(
        self,
        *,
        verification_commands: list[str] | None = None,
    ) -> None:
        self.verification_commands = list(verification_commands or [])


def load_verification_config(project_root: Path) -> VerificationConfig:
    path = project_root / ".memassist" / "verification.yaml"
    if not path.exists():
        return VerificationConfig()
    text = path.read_text(encoding="utf-8")
    data = _parse_minimal_yaml(text)
    return VerificationConfig(
        verification_commands=data.get("verification_commands") or [],
    )


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
