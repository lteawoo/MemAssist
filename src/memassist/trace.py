from __future__ import annotations

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
    policy_decision: str | None = None,
) -> str:
    files: list[str] = []
    for key in ("path", "file", "target"):
        value = payload.get(key)
        if isinstance(value, str):
            files.append(value)
    return store.add_trace_event(
        session_id=session_id,
        project_id=project_id,
        event_type=event_type,
        tool_name=tool_name,
        input_json=payload,
        output_summary=str(payload.get("output", ""))[:500] if payload.get("output") else None,
        policy_decision=policy_decision,
        files=files,
    )

