from __future__ import annotations

from sqlite3 import Row

from .session import summarize_session


def lesson_from_session(events: list[Row], feedback: str | None = None) -> str:
    summary = summarize_session(events)
    pieces: list[str] = []
    if feedback:
        pieces.append(feedback.strip())
    if summary.denied_events:
        pieces.append(f"{summary.denied_events} host-denied action(s) occurred.")
    if summary.files:
        pieces.append("Review future changes touching: " + ", ".join(summary.files[:5]))
    if summary.commands:
        pieces.append("Observed commands: " + ", ".join(sorted(set(summary.commands))[:3]))
    if not pieces:
        pieces.append("Review this session before turning it into a candidate lesson.")
    return " ".join(pieces)
