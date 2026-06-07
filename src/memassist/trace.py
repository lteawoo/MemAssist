from __future__ import annotations

import re
from typing import Any

from .storage import Store


def record_tool_event(
    store: Store,
    *,
    session_id: str,
    project_id: str | None,
    event_type: str,
    tool_name: str | None,
    payload: dict[str, Any],
    tool_decision: str | None = None,
) -> str:
    files = extract_files(payload)
    output_summary = summarize_output(payload)
    return store.add_trace_event(
        session_id=session_id,
        project_id=project_id,
        event_type=event_type,
        tool_name=tool_name,
        input_json=payload,
        output_summary=output_summary,
        tool_decision=tool_decision,
        files=files,
    )


def extract_files(payload: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for key in ("path", "file", "target"):
        value = payload.get(key)
        if isinstance(value, str):
            files.append(value)
    command = _command_text(payload)
    if command:
        files.extend(_files_from_apply_patch(command))
        files.extend(_files_from_shell_command(command))
    return sorted(set(file for file in files if file))


def summarize_output(payload: dict[str, Any]) -> str | None:
    for key in ("tool_response", "output", "response", "result"):
        value = payload.get(key)
        if value:
            text = str(value).strip()
            return text[:500] if text else None
    return None


def _command_text(payload: dict[str, Any]) -> str:
    command = payload.get("command")
    if isinstance(command, str):
        return command
    tool_input = payload.get("tool_input") or payload.get("toolInput")
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        return str(tool_input["command"])
    return ""


def _files_from_apply_patch(command: str) -> list[str]:
    files: list[str] = []
    for line in command.splitlines():
        for prefix in ("*** Update File: ", "*** Add File: ", "*** Delete File: "):
            if line.startswith(prefix):
                files.append(line.removeprefix(prefix).strip())
    return files


def _files_from_shell_command(command: str) -> list[str]:
    files: list[str] = []
    if not command.strip().startswith(("sed ", "cat ", "rg ", "python", "npm", "git ")):
        return files
    for token in re.findall(r"(?<![-\w./])(?:[\w.-]+/)+[\w.-]+", command):
        files.append(token.strip("'\""))
    return files
