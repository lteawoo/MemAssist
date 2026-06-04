from __future__ import annotations

import json
from dataclasses import dataclass
from sqlite3 import Row
from typing import Any

from .trace import command_is_test


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    event_count: int
    tools: list[str]
    files: list[str]
    test_commands: list[str]
    denied_events: int
    last_message: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "event_count": self.event_count,
            "tools": self.tools,
            "files": self.files,
            "test_commands": self.test_commands,
            "denied_events": self.denied_events,
            "last_message": self.last_message,
        }


def summarize_session(events: list[Row]) -> SessionSummary:
    if not events:
        return SessionSummary("", 0, [], [], [], 0, None)
    tools: set[str] = set()
    files: set[str] = set()
    test_commands: list[str] = []
    denied_events = 0
    last_message: str | None = None
    for event in events:
        if event["tool_name"]:
            tools.add(str(event["tool_name"]))
        if event["tool_decision"] in {"block", "deny"}:
            denied_events += 1
        for file in json.loads(event["files_json"] or "[]"):
            files.add(str(file))
        payload = _payload(event)
        command = _command(payload)
        if command and command_is_test(command):
            test_commands.append(command)
        if event["event_type"] == "stop":
            message = payload.get("last_assistant_message")
            if isinstance(message, str) and message.strip():
                last_message = message.strip()
    return SessionSummary(
        session_id=str(events[0]["session_id"]),
        event_count=len(events),
        tools=sorted(tools),
        files=sorted(files),
        test_commands=test_commands,
        denied_events=denied_events,
        last_message=last_message,
    )


def _payload(event: Row) -> dict[str, Any]:
    try:
        value = json.loads(event["input_json"] or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _command(payload: dict[str, Any]) -> str:
    command = payload.get("command")
    if isinstance(command, str):
        return command
    tool_input = payload.get("tool_input") or payload.get("toolInput")
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        return str(tool_input["command"])
    return ""
