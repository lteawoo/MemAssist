from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ENFORCEMENTS, MEMORY_STATUSES, MEMORY_TYPES, Memory
from .paths import db_path
from .project import Project


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY,
              root_path TEXT NOT NULL,
              git_remote TEXT,
              created_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
              id TEXT PRIMARY KEY,
              scope_type TEXT NOT NULL,
              project_id TEXT,
              session_id TEXT,
              type TEXT NOT NULL,
              content TEXT NOT NULL,
              reason TEXT,
              tags_json TEXT NOT NULL,
              paths_json TEXT NOT NULL,
              status TEXT NOT NULL,
              importance REAL NOT NULL,
              confidence REAL NOT NULL,
              enforcement TEXT NOT NULL,
              source_kind TEXT NOT NULL,
              source_ref TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_used_at TEXT,
              expires_at TEXT,
              superseded_by TEXT
            );

            CREATE TABLE IF NOT EXISTS trace_events (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              project_id TEXT,
              event_type TEXT NOT NULL,
              tool_name TEXT,
              input_json TEXT,
              output_summary TEXT,
              policy_decision TEXT,
              files_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lifecycle_events (
              id TEXT PRIMARY KEY,
              session_id TEXT,
              project_id TEXT,
              memory_id TEXT,
              candidate_json TEXT,
              decision TEXT NOT NULL,
              risk TEXT NOT NULL,
              reason TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        try:
            self.conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(memory_id UNINDEXED, content, tags);
                """
            )
        except sqlite3.OperationalError:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_fts (
                  memory_id TEXT PRIMARY KEY,
                  content TEXT,
                  tags TEXT
                );
                """
            )
        self.conn.commit()

    def upsert_project(self, project: Project) -> None:
        ts = now_iso()
        self.conn.execute(
            """
            INSERT INTO projects (id, root_path, git_remote, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              root_path = excluded.root_path,
              git_remote = excluded.git_remote,
              last_seen_at = excluded.last_seen_at
            """,
            (project.id, str(project.root), project.git_remote, ts, ts),
        )
        self.conn.commit()

    def add_memory(
        self,
        *,
        scope_type: str,
        project_id: str | None,
        session_id: str | None = None,
        type: str,
        content: str,
        reason: str | None = None,
        tags: list[str] | None = None,
        paths: list[str] | None = None,
        status: str = "active",
        importance: float = 0.5,
        confidence: float = 0.8,
        enforcement: str = "none",
        source_kind: str = "manual",
        source_ref: str | None = None,
        expires_at: str | None = None,
    ) -> str:
        if scope_type not in {"global", "project", "session"}:
            raise ValueError(f"invalid scope_type: {scope_type}")
        if type not in MEMORY_TYPES:
            raise ValueError(f"invalid memory type: {type}")
        if status not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {status}")
        if enforcement not in ENFORCEMENTS:
            raise ValueError(f"invalid enforcement: {enforcement}")
        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        ts = now_iso()
        tags = tags or []
        paths = paths or []
        self.conn.execute(
            """
            INSERT INTO memories (
              id, scope_type, project_id, session_id, type, content, reason,
              tags_json, paths_json, status, importance, confidence,
              enforcement, source_kind, source_ref, created_at, updated_at,
              expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                scope_type,
                project_id,
                session_id,
                type,
                content,
                reason,
                json.dumps(tags),
                json.dumps(paths),
                status,
                importance,
                confidence,
                enforcement,
                source_kind,
                source_ref,
                ts,
                ts,
                expires_at,
            ),
        )
        self._upsert_fts(memory_id, content, tags)
        self.conn.commit()
        return memory_id

    def list_memories(
        self,
        *,
        project_id: str | None = None,
        include_global: bool = True,
        status: str | None = None,
    ) -> list[Memory]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id and include_global:
            clauses.append("(project_id = ? OR scope_type = 'global')")
            params.append(project_id)
        elif project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        elif include_global:
            clauses.append("scope_type = 'global'")
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM memories {where} ORDER BY importance DESC, updated_at DESC",
            params,
        ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def get_memory(self, memory_id: str) -> Memory | None:
        row = self.conn.execute(
            "SELECT * FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def search_memories(
        self,
        query: str,
        *,
        project_id: str | None,
        limit: int = 10,
    ) -> list[Memory]:
        active_statuses = ("active", "auto_active", "policy_active", "pinned")
        scope_clause = "(m.scope_type = 'global' OR m.project_id = ?)"
        params: list[Any] = [_fts_query(query), project_id, *active_statuses]
        try:
            rows = self.conn.execute(
                f"""
                SELECT m.*, bm25(memory_fts) AS text_score
                FROM memory_fts
                JOIN memories m ON m.id = memory_fts.memory_id
                WHERE memory_fts MATCH ?
                  AND {scope_clause}
                  AND m.status IN (?, ?, ?, ?)
                ORDER BY
                  CASE m.status WHEN 'pinned' THEN 1 ELSE 0 END DESC,
                  text_score ASC,
                  m.importance DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        except sqlite3.OperationalError:
            like = f"%{query}%"
            rows = self.conn.execute(
                f"""
                SELECT m.*
                FROM memories m
                WHERE (m.content LIKE ? OR m.tags_json LIKE ?)
                  AND {scope_clause}
                  AND m.status IN (?, ?, ?, ?)
                ORDER BY m.importance DESC, m.updated_at DESC
                LIMIT ?
                """,
                [like, like, project_id, *active_statuses, limit],
            ).fetchall()
        memories = [self._row_to_memory(row) for row in rows]
        for memory in memories:
            self.touch_memory(memory.id)
        return memories

    def update_status(self, memory_id: str, status: str) -> None:
        if status not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {status}")
        self.conn.execute(
            "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), memory_id),
        )
        self.conn.commit()

    def supersede(self, old_id: str, new_id: str) -> None:
        self.conn.execute(
            """
            UPDATE memories
            SET status = 'superseded', superseded_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_id, now_iso(), old_id),
        )
        self.conn.commit()

    def touch_memory(self, memory_id: str) -> None:
        self.conn.execute(
            "UPDATE memories SET last_used_at = ? WHERE id = ?",
            (now_iso(), memory_id),
        )
        self.conn.commit()

    def add_trace_event(
        self,
        *,
        session_id: str,
        project_id: str | None,
        event_type: str,
        tool_name: str | None = None,
        input_json: dict[str, Any] | None = None,
        output_summary: str | None = None,
        policy_decision: str | None = None,
        files: list[str] | None = None,
    ) -> str:
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            """
            INSERT INTO trace_events (
              id, session_id, project_id, event_type, tool_name, input_json,
              output_summary, policy_decision, files_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                session_id,
                project_id,
                event_type,
                tool_name,
                json.dumps(input_json or {}),
                output_summary,
                policy_decision,
                json.dumps(files or []),
                now_iso(),
            ),
        )
        self.conn.commit()
        return event_id

    def trace_events(
        self,
        session_id: str | None = None,
        *,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[sqlite3.Row]:
        if session_id:
            return self.conn.execute(
                "SELECT * FROM trace_events WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        if project_id:
            return self.conn.execute(
                """
                SELECT * FROM trace_events
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM trace_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def latest_session_id(self, project_id: str | None = None) -> str | None:
        if project_id:
            row = self.conn.execute(
                """
                SELECT session_id FROM trace_events
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT session_id FROM trace_events ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return str(row["session_id"]) if row else None

    def memory_exists(
        self,
        *,
        project_id: str | None,
        content: str,
        type: str,
        statuses: tuple[str, ...] | None = None,
    ) -> bool:
        clauses = ["content = ?", "type = ?"]
        params: list[Any] = [content, type]
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        else:
            clauses.append("project_id IS NULL")
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        row = self.conn.execute(
            f"SELECT 1 FROM memories WHERE {' AND '.join(clauses)} LIMIT 1",
            params,
        ).fetchone()
        return row is not None

    def add_lifecycle_event(
        self,
        *,
        session_id: str | None,
        project_id: str | None,
        memory_id: str | None,
        candidate: dict[str, Any],
        decision: str,
        risk: str,
        reason: str,
    ) -> str:
        event_id = f"life_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            """
            INSERT INTO lifecycle_events (
              id, session_id, project_id, memory_id, candidate_json,
              decision, risk, reason, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                session_id,
                project_id,
                memory_id,
                json.dumps(candidate, ensure_ascii=False),
                decision,
                risk,
                reason,
                now_iso(),
            ),
        )
        self.conn.commit()
        return event_id

    def lifecycle_events(
        self,
        session_id: str | None = None,
        *,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.conn.execute(
            f"SELECT * FROM lifecycle_events {where} ORDER BY created_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()

    def _upsert_fts(self, memory_id: str, content: str, tags: list[str]) -> None:
        self.conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        self.conn.execute(
            "INSERT INTO memory_fts (memory_id, content, tags) VALUES (?, ?, ?)",
            (memory_id, content, " ".join(tags)),
        )

    def _row_to_memory(self, row: sqlite3.Row) -> Memory:
        return Memory(
            id=row["id"],
            scope_type=row["scope_type"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            type=row["type"],
            content=row["content"],
            reason=row["reason"],
            tags=json.loads(row["tags_json"] or "[]"),
            paths=json.loads(row["paths_json"] or "[]"),
            status=row["status"],
            importance=row["importance"],
            confidence=row["confidence"],
            enforcement=row["enforcement"],
            source_kind=row["source_kind"],
            source_ref=row["source_ref"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_used_at=row["last_used_at"],
            expires_at=row["expires_at"],
            superseded_by=row["superseded_by"],
        )


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_가-힣]+", query.lower())
    if not tokens:
        return query
    deduped = list(dict.fromkeys(tokens))
    return " OR ".join(f'"{token}"' for token in deduped[:8])
