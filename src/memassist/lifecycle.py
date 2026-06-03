from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .storage import Store


@dataclass(frozen=True)
class CleanupResult:
    expired: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"expired": self.expired}


def cleanup_memories(store: Store) -> CleanupResult:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = store.conn.execute(
        """
        SELECT id FROM memories
        WHERE expires_at IS NOT NULL
          AND expires_at <= ?
          AND status NOT IN ('expired', 'deleted', 'disabled')
        """,
        (now,),
    ).fetchall()
    expired = [str(row["id"]) for row in rows]
    for memory_id in expired:
        store.update_status(memory_id, "expired")
    return CleanupResult(expired=expired)

