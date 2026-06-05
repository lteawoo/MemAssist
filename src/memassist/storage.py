from __future__ import annotations

from dataclasses import replace
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_artifacts import (
    delete_memory_artifact,
    find_memory_artifact,
    load_memory_artifacts,
    memory_content_hash,
    read_memory_artifact_by_id,
    sync_memory_artifact,
)
from .models import MEMORY_STATUSES, MEMORY_TYPES, Memory
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
              strength REAL NOT NULL DEFAULT 0.5,
              recurrence INTEGER NOT NULL DEFAULT 1,
              retrieval_count INTEGER NOT NULL DEFAULT 0,
              utility REAL NOT NULL DEFAULT 0.0,
              half_life_days REAL NOT NULL DEFAULT 30.0,
              source_kind TEXT NOT NULL,
              source_ref TEXT,
              source_ids_json TEXT NOT NULL DEFAULT '[]',
              source_quote TEXT,
              content_hash TEXT,
              artifact_path TEXT,
              indexed_at TEXT,
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
              tool_decision TEXT,
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

            CREATE TABLE IF NOT EXISTS memory_links (
              id TEXT PRIMARY KEY,
              project_id TEXT,
              source_id TEXT NOT NULL,
              target_id TEXT NOT NULL,
              relation TEXT NOT NULL,
              strength REAL NOT NULL,
              reason TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(source_id, target_id, relation)
            );

            CREATE TABLE IF NOT EXISTS memory_embeddings (
              memory_id TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              profile_id TEXT NOT NULL DEFAULT 'legacy',
              profile_fingerprint TEXT NOT NULL DEFAULT '',
              provider TEXT NOT NULL,
              model TEXT NOT NULL,
              quantization TEXT NOT NULL DEFAULT 'none',
              dimension INTEGER NOT NULL,
              embedding_json TEXT NOT NULL,
              indexed_at TEXT NOT NULL,
              PRIMARY KEY(memory_id, content_hash, profile_id, profile_fingerprint, provider, model, quantization, dimension)
            );
            """
        )
        self._ensure_memory_lifecycle_columns()
        self._ensure_embedding_cache_columns()
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

    def _ensure_memory_lifecycle_columns(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(memories)").fetchall()}
        defaults = {
            "strength": "REAL NOT NULL DEFAULT 0.5",
            "recurrence": "INTEGER NOT NULL DEFAULT 1",
            "retrieval_count": "INTEGER NOT NULL DEFAULT 0",
            "utility": "REAL NOT NULL DEFAULT 0.0",
            "half_life_days": "REAL NOT NULL DEFAULT 30.0",
            "source_kind": "TEXT NOT NULL DEFAULT 'manual'",
            "source_ref": "TEXT",
            "source_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "source_quote": "TEXT",
            "content_hash": "TEXT",
            "artifact_path": "TEXT",
            "indexed_at": "TEXT",
        }
        for name, definition in defaults.items():
            if name not in columns:
                try:
                    self.conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc):
                        raise

    def _ensure_embedding_cache_columns(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(memory_embeddings)").fetchall()}
        defaults = {
            "profile_id": "TEXT NOT NULL DEFAULT 'legacy'",
            "profile_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "quantization": "TEXT NOT NULL DEFAULT 'none'",
        }
        for name, definition in defaults.items():
            if name not in columns:
                try:
                    self.conn.execute(f"ALTER TABLE memory_embeddings ADD COLUMN {name} {definition}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc):
                        raise

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
        strength: float | None = None,
        recurrence: int = 1,
        retrieval_count: int = 0,
        utility: float = 0.0,
        half_life_days: float | None = None,
        source_kind: str = "manual",
        source_ref: str | None = None,
        source_quote: str | None = None,
        source_ids: list[str] | None = None,
        expires_at: str | None = None,
    ) -> str:
        if scope_type not in {"global", "project", "session"}:
            raise ValueError(f"invalid scope_type: {scope_type}")
        if type not in MEMORY_TYPES:
            raise ValueError(f"invalid memory type: {type}")
        if status not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {status}")
        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        ts = now_iso()
        tags = tags or []
        paths = paths or []
        strength = _clamp(strength if strength is not None else (importance + confidence) / 2)
        half_life_days = half_life_days if half_life_days is not None else _default_half_life_days(type, status, strength=strength)
        memory = Memory(
            id=memory_id,
            scope_type=scope_type,
            project_id=project_id,
            session_id=session_id,
            type=type,
            content=content,
            reason=reason,
            tags=tags,
            paths=paths,
            status=status,
            importance=importance,
            confidence=confidence,
            strength=strength,
            recurrence=recurrence,
            retrieval_count=retrieval_count,
            utility=utility,
            half_life_days=half_life_days,
            source_kind=source_kind,
            source_ref=source_ref,
            created_at=ts,
            updated_at=ts,
            last_used_at=None,
            expires_at=expires_at,
            superseded_by=None,
            source_quote=source_quote,
            source_ids=source_ids or [],
            content_hash=memory_content_hash(content),
        )
        sync_memory_artifact(self.path.parent, memory, source_quote=source_quote)
        self._upsert_memory_index(memory)
        self._refresh_embedding_cache(memory)
        self.conn.commit()
        return memory_id

    def link_related_memories(self, memory_id: str, *, project_id: str | None, limit: int = 5) -> list[str]:
        memory = self.get_memory(memory_id)
        if not memory:
            return []
        candidates = [
            candidate
            for candidate in self.list_memories(project_id=project_id, include_global=True)
            if candidate.id != memory_id
            and candidate.status != "archived"
        ]
        scored: list[tuple[float, Memory, str]] = []
        memory_tags = set(memory.tags)
        memory_paths = set(memory.paths)
        memory_tokens = set(re.findall(r"[A-Za-z0-9_가-힣]+", memory.content.lower()))
        for candidate in candidates:
            tag_overlap = len(memory_tags & set(candidate.tags))
            path_overlap = len(memory_paths & set(candidate.paths))
            candidate_tokens = set(re.findall(r"[A-Za-z0-9_가-힣]+", candidate.content.lower()))
            token_overlap = len(memory_tokens & candidate_tokens) / max(len(memory_tokens), 1)
            strength = min(1.0, tag_overlap * 0.25 + path_overlap * 0.35 + token_overlap * 0.40)
            if strength >= 0.25:
                reason = "Related by shared tags, paths, or content terms."
                scored.append((strength, candidate, reason))
        linked: list[str] = []
        for strength, target, reason in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]:
            link_id = f"link_{uuid.uuid4().hex[:12]}"
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO memory_links (
                  id, project_id, source_id, target_id, relation, strength, reason, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (link_id, project_id, memory_id, target.id, "related", strength, reason, now_iso()),
            )
            if cursor.rowcount:
                linked.append(target.id)
        self.conn.commit()
        return linked

    def memory_links(self, memory_id: str) -> list[dict[str, Any]]:
        if not read_memory_artifact_by_id(self.path.parent, memory_id):
            return []
        rows = self.conn.execute(
            """
            SELECT l.*
            FROM memory_links l
            WHERE l.source_id = ?
            ORDER BY l.strength DESC, l.created_at DESC
            """,
            (memory_id,),
        ).fetchall()
        links: list[dict[str, Any]] = []
        for row in rows:
            target = self.get_memory(str(row["target_id"]))
            if not target:
                continue
            item = dict(row)
            item["target_content"] = target.content
            item["target_type"] = target.type
            item["target_status"] = target.status
            links.append(item)
        return links

    def list_memories(
        self,
        *,
        project_id: str | None = None,
        include_global: bool = True,
        status: str | None = None,
    ) -> list[Memory]:
        memories = [
            self._with_index_telemetry(memory)
            for memory in load_memory_artifacts(self.path.parent)
            if _memory_in_scope(memory, project_id=project_id, include_global=include_global)
            and (status is None or memory.status == status)
        ]
        return sorted(memories, key=lambda memory: (memory.importance, memory.updated_at), reverse=True)

    def get_memory(self, memory_id: str) -> Memory | None:
        memory = read_memory_artifact_by_id(self.path.parent, memory_id)
        return self._with_index_telemetry(memory) if memory else None

    def search_memories(
        self,
        query: str,
        *,
        project_id: str | None,
        limit: int = 10,
    ) -> list[Memory]:
        active_statuses = ("active",)
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
                  AND m.status IN (?)
                ORDER BY
                  text_score ASC,
                  m.strength DESC,
                  m.utility DESC,
                  m.confidence DESC,
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
                  AND m.status IN (?)
                ORDER BY
                  m.strength DESC,
                  m.utility DESC,
                  m.confidence DESC,
                  m.importance DESC,
                  m.updated_at DESC
                LIMIT ?
                """,
                [like, like, project_id, *active_statuses, limit],
            ).fetchall()
        memories: list[Memory] = []
        stale_ids: list[str] = []
        for row in rows:
            memory_id = str(row["id"])
            memory = read_memory_artifact_by_id(self.path.parent, memory_id)
            if not memory or memory.status != "active":
                stale_ids.append(memory_id)
                continue
            memories.append(self._with_index_telemetry(memory))
        for memory_id in stale_ids:
            self._delete_memory_index(memory_id)
        if stale_ids:
            self.conn.commit()
        for memory in memories:
            self.touch_memory(memory.id)
        return memories

    def update_status(self, memory_id: str, status: str) -> None:
        if status not in MEMORY_STATUSES:
            raise ValueError(f"invalid memory status: {status}")
        memory = self.get_memory(memory_id)
        if memory:
            updated = replace(memory, status=status, updated_at=now_iso())
            sync_memory_artifact(self.path.parent, updated)
            self._upsert_memory_index(updated)
            self._refresh_embedding_cache(updated)
            self.conn.commit()

    def update_paths(self, memory_id: str, paths: list[str]) -> None:
        memory = self.get_memory(memory_id)
        if memory:
            updated = replace(memory, paths=paths, updated_at=now_iso())
            sync_memory_artifact(self.path.parent, updated)
            self._upsert_memory_index(updated)
            self._refresh_embedding_cache(updated)
            self.conn.commit()

    def supersede(self, old_id: str, new_id: str) -> None:
        memory = self.get_memory(old_id)
        if memory:
            updated = replace(memory, status="archived", superseded_by=new_id, updated_at=now_iso())
            sync_memory_artifact(self.path.parent, updated)
            self._upsert_memory_index(updated)
            self._refresh_embedding_cache(updated)
            self.conn.commit()

    def delete_memory(self, memory_id: str) -> bool:
        deleted = delete_memory_artifact(self.path.parent, memory_id)
        self._delete_memory_index(memory_id)
        self.conn.commit()
        return deleted

    def touch_memory(self, memory_id: str) -> None:
        self.conn.execute(
            """
            UPDATE memories
            SET last_used_at = ?,
                retrieval_count = retrieval_count + 1,
                utility = min(1.0, utility + 0.02),
                strength = min(1.0, strength + 0.01)
            WHERE id = ?
            """,
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
        tool_decision: str | None = None,
        files: list[str] | None = None,
    ) -> str:
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            """
            INSERT INTO trace_events (
              id, session_id, project_id, event_type, tool_name, input_json,
              output_summary, tool_decision, files_json, created_at
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
                tool_decision,
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
        return self.find_memory(project_id=project_id, content=content, type=type, statuses=statuses) is not None

    def find_memory(
        self,
        *,
        project_id: str | None,
        content: str,
        type: str,
        statuses: tuple[str, ...] | None = None,
    ) -> Memory | None:
        candidates = [
            memory
            for memory in self.list_memories(project_id=project_id, include_global=False, status=None)
            if memory.content == content
            and memory.type == type
            and (statuses is None or memory.status in statuses)
        ]
        return sorted(candidates, key=lambda memory: memory.updated_at, reverse=True)[0] if candidates else None

    def reinforce_memory(
        self,
        memory_id: str,
        *,
        confidence_delta: float = 0.05,
        importance_delta: float = 0.03,
    ) -> Memory | None:
        memory = self.get_memory(memory_id)
        if not memory:
            return None
        confidence = min(1.0, memory.confidence + confidence_delta)
        importance = min(1.0, memory.importance + importance_delta)
        strength = min(1.0, memory.strength + 0.08)
        recurrence = memory.recurrence + 1
        status = memory.status
        updated = replace(
            memory,
            confidence=confidence,
            importance=importance,
            strength=strength,
            recurrence=recurrence,
            status=status,
            updated_at=now_iso(),
        )
        sync_memory_artifact(self.path.parent, updated)
        self._upsert_memory_index(updated)
        self._refresh_embedding_cache(updated)
        self.conn.commit()
        return updated

    def rebuild_memory_index_from_artifacts(self, *, prune_missing: bool = True, embedding_profile_id: str | None = None) -> list[str]:
        indexed: list[str] = []
        for memory in load_memory_artifacts(self.path.parent):
            normalized = _with_required_timestamps(memory)
            self._upsert_memory_index(normalized)
            self._refresh_embedding_cache(normalized, profile_id=embedding_profile_id)
            indexed.append(normalized.id)
        if prune_missing:
            self._prune_memory_index(set(indexed))
        self.conn.commit()
        return indexed

    def upsert_memory_embedding(
        self,
        memory: Memory,
        *,
        profile: Any,
        embedding: list[float],
    ) -> None:
        if not embedding:
            return
        content_hash = memory.content_hash or memory_content_hash(memory.content)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO memory_embeddings (
              memory_id, content_hash, profile_id, profile_fingerprint, provider, model,
              quantization, dimension, embedding_json, indexed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.id,
                content_hash,
                profile.id,
                profile.fingerprint,
                profile.provider,
                profile.model,
                profile.quantization,
                len(embedding),
                json.dumps(embedding),
                now_iso(),
            ),
        )
        self.conn.commit()

    def memory_embedding_rows_for_profile(
        self,
        *,
        project_id: str,
        profile: Any,
        dimension: int,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        rows = self.conn.execute(
            """
            SELECT e.*
            FROM memory_embeddings e
            JOIN memories m ON m.id = e.memory_id
            WHERE e.profile_id = ?
              AND e.profile_fingerprint = ?
              AND e.provider = ?
              AND e.model = ?
              AND e.quantization = ?
              AND e.dimension = ?
              AND (m.scope_type = 'global' OR m.project_id = ?)
              AND m.status = 'active'
            """,
            (profile.id, profile.fingerprint, profile.provider, profile.model, profile.quantization, dimension, project_id),
        ).fetchall()
        results: list[dict[str, Any]] = []
        stale_count = 0
        for row in rows:
            memory = self.get_memory(str(row["memory_id"]))
            if not memory or memory.status != "active":
                stale_count += 1
                continue
            if row["content_hash"] != (memory.content_hash or memory_content_hash(memory.content)):
                stale_count += 1
                continue
            try:
                embedding = json.loads(row["embedding_json"])
            except json.JSONDecodeError:
                stale_count += 1
                continue
            if not isinstance(embedding, list):
                stale_count += 1
                continue
            results.append({"memory": memory, "embedding": [float(value) for value in embedding]})
        return results, {"row_count": len(rows), "stale_count": stale_count}

    def memory_embedding_rows(
        self,
        *,
        project_id: str,
        provider: str,
        model: str,
        dimension: int,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT e.*
            FROM memory_embeddings e
            JOIN memories m ON m.id = e.memory_id
            WHERE e.provider = ?
              AND e.model = ?
              AND e.dimension = ?
              AND (m.scope_type = 'global' OR m.project_id = ?)
              AND m.status = 'active'
            """,
            (provider, model, dimension, project_id),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            memory = self.get_memory(str(row["memory_id"]))
            if not memory or memory.status != "active":
                continue
            if row["content_hash"] != (memory.content_hash or memory_content_hash(memory.content)):
                continue
            try:
                embedding = json.loads(row["embedding_json"])
            except json.JSONDecodeError:
                continue
            if not isinstance(embedding, list):
                continue
            results.append({"memory": memory, "embedding": [float(value) for value in embedding]})
        return results

    def cleanup_embedding_cache(self, *, profile_id: str | None = None) -> int:
        if profile_id:
            cursor = self.conn.execute("DELETE FROM memory_embeddings WHERE profile_id = ?", (profile_id,))
        else:
            cursor = self.conn.execute("DELETE FROM memory_embeddings")
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def embedding_cache_summary(self, *, profile_id: str | None = None) -> dict[str, Any]:
        if profile_id:
            rows = self.conn.execute(
                "SELECT profile_id, embedding_json FROM memory_embeddings WHERE profile_id = ?",
                (profile_id,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT profile_id, embedding_json FROM memory_embeddings").fetchall()
        by_profile: dict[str, int] = {}
        bytes_by_profile: dict[str, int] = {}
        for row in rows:
            key = str(row["profile_id"])
            by_profile[key] = by_profile.get(key, 0) + 1
            bytes_by_profile[key] = bytes_by_profile.get(key, 0) + len(str(row["embedding_json"]).encode("utf-8"))
        return {
            "row_count": len(rows),
            "bytes": sum(bytes_by_profile.values()),
            "profiles": {
                key: {"row_count": by_profile[key], "bytes": bytes_by_profile.get(key, 0)}
                for key in sorted(by_profile)
            },
        }

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

    def _upsert_fts(
        self,
        memory_id: str,
        content: str,
        tags: list[str],
        *,
        paths: list[str],
        status: str,
    ) -> None:
        index_text = _memory_index_text(
            content=content,
            tags=tags,
            paths=paths,
            status=status,
        )
        tag_text = " ".join([*tags, *paths, status])
        self.conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        self.conn.execute(
            "INSERT INTO memory_fts (memory_id, content, tags) VALUES (?, ?, ?)",
            (memory_id, index_text, tag_text),
        )

    def _upsert_memory_index(self, memory: Memory) -> None:
        artifact = find_memory_artifact(self.path.parent, memory.id)
        content_hash = memory.content_hash or memory_content_hash(memory.content)
        indexed_at = now_iso()
        self.conn.execute(
            """
            INSERT INTO memories (
              id, scope_type, project_id, session_id, type, content, reason,
              tags_json, paths_json, status, importance, confidence,
              strength, recurrence, retrieval_count, utility, half_life_days,
              source_kind, source_ref, source_ids_json, source_quote,
              content_hash, artifact_path, indexed_at, created_at, updated_at,
              last_used_at, expires_at, superseded_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              scope_type = excluded.scope_type,
              project_id = excluded.project_id,
              session_id = excluded.session_id,
              type = excluded.type,
              content = excluded.content,
              reason = excluded.reason,
              tags_json = excluded.tags_json,
              paths_json = excluded.paths_json,
              status = excluded.status,
              importance = excluded.importance,
              confidence = excluded.confidence,
              strength = excluded.strength,
              recurrence = excluded.recurrence,
              retrieval_count = excluded.retrieval_count,
              utility = excluded.utility,
              half_life_days = excluded.half_life_days,
              source_kind = excluded.source_kind,
              source_ref = excluded.source_ref,
              source_ids_json = excluded.source_ids_json,
              source_quote = excluded.source_quote,
              content_hash = excluded.content_hash,
              artifact_path = excluded.artifact_path,
              indexed_at = excluded.indexed_at,
              created_at = excluded.created_at,
              updated_at = excluded.updated_at,
              last_used_at = excluded.last_used_at,
              expires_at = excluded.expires_at,
              superseded_by = excluded.superseded_by
            """,
            (
                memory.id,
                memory.scope_type,
                memory.project_id,
                memory.session_id,
                memory.type,
                memory.content,
                memory.reason,
                json.dumps(memory.tags),
                json.dumps(memory.paths),
                memory.status,
                memory.importance,
                memory.confidence,
                memory.strength,
                memory.recurrence,
                memory.retrieval_count,
                memory.utility,
                memory.half_life_days,
                memory.source_kind,
                memory.source_ref,
                json.dumps(memory.source_ids or []),
                memory.source_quote,
                content_hash,
                str(artifact) if artifact else None,
                indexed_at,
                memory.created_at,
                memory.updated_at,
                memory.last_used_at,
                memory.expires_at,
                memory.superseded_by,
            ),
        )
        self._upsert_fts(
            memory.id,
            memory.content,
            memory.tags,
            paths=memory.paths,
            status=memory.status,
        )

    def _prune_memory_index(self, artifact_ids: set[str]) -> None:
        rows = self.conn.execute("SELECT id FROM memories").fetchall()
        missing = [str(row["id"]) for row in rows if str(row["id"]) not in artifact_ids]
        if not missing:
            return
        placeholders = ", ".join("?" for _ in missing)
        self.conn.execute(f"DELETE FROM memory_links WHERE source_id IN ({placeholders})", missing)
        self.conn.execute(f"DELETE FROM memory_links WHERE target_id IN ({placeholders})", missing)
        self.conn.execute(f"DELETE FROM memory_fts WHERE memory_id IN ({placeholders})", missing)
        self.conn.execute(f"DELETE FROM memory_embeddings WHERE memory_id IN ({placeholders})", missing)
        self.conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", missing)

    def _delete_memory_index(self, memory_id: str) -> None:
        self.conn.execute("DELETE FROM memory_links WHERE source_id = ? OR target_id = ?", (memory_id, memory_id))
        self.conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
        self.conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
        self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    def _refresh_embedding_cache(self, memory: Memory, *, profile_id: str | None = None) -> None:
        try:
            from .embedding_profiles import get_embedding_profile
            from .embeddings import ensure_memory_embedding
        except ImportError:
            return
        try:
            profile = get_embedding_profile(profile_id, mem_dir=self.path.parent)
        except Exception:
            return
        ensure_memory_embedding(self, memory, profile=profile)

    def _with_index_telemetry(self, memory: Memory) -> Memory:
        row = self.conn.execute(
            """
            SELECT retrieval_count, utility, strength, recurrence, last_used_at
            FROM memories
            WHERE id = ?
            """,
            (memory.id,),
        ).fetchone()
        if not row:
            return memory
        return replace(
            memory,
            retrieval_count=int(row["retrieval_count"] or 0),
            utility=float(row["utility"] or memory.utility),
            strength=float(row["strength"] or memory.strength),
            recurrence=int(row["recurrence"] or memory.recurrence),
            last_used_at=row["last_used_at"],
        )

def _fts_query(query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_가-힣]+", query.lower())
    if not tokens:
        return query
    deduped = list(dict.fromkeys(tokens))
    return " OR ".join(f'"{token}"' for token in deduped[:8])


def _memory_index_text(
    *,
    content: str,
    tags: list[str],
    paths: list[str],
    status: str,
) -> str:
    section = "verifier" if type == "workflow" else "context"
    return " ".join(
        part
        for part in [
            content,
            f"type {type}",
            f"section {section}",
            f"status {status}",
            "tags " + " ".join(tags) if tags else "",
            "paths " + " ".join(paths) if paths else "",
        ]
        if part
    )


def _default_half_life_days(type: str, status: str, *, strength: float = 0.5) -> float:
    """D3: Compute half-life from strength signal, not from a closed type→days mapping.

    Base half-life scales continuously with strength (0..1):
    - min clamp: 7 days (floor to prevent instant expiry on weak memories)
    - max clamp: 180 days (ceiling to prevent immortal memories)
    - strength=0.5 (default) → 60 days (reasonable cold-start value)
    - strength=1.0 → 120 days; strength=0.0 → 7 days

    This is a continuous function applied uniformly to all memories regardless
    of type. `expires_at` (explicit) takes precedence over this decay signal.
    """
    _MIN_HALF_LIFE = 7.0
    _MAX_HALF_LIFE = 180.0
    _BASE_HALF_LIFE = 120.0  # at full strength
    clamped_strength = max(0.0, min(1.0, strength))
    # Linear interpolation: min + strength * (base - min), clamped to [min, max]
    half_life = _MIN_HALF_LIFE + clamped_strength * (_BASE_HALF_LIFE - _MIN_HALF_LIFE)
    return max(_MIN_HALF_LIFE, min(_MAX_HALF_LIFE, half_life))


def _with_required_timestamps(memory: Memory) -> Memory:
    ts = now_iso()
    created_at = memory.created_at or ts
    updated_at = memory.updated_at or created_at
    return replace(memory, created_at=created_at, updated_at=updated_at)


def _memory_in_scope(memory: Memory, *, project_id: str | None, include_global: bool) -> bool:
    if project_id and include_global:
        return memory.project_id == project_id or memory.scope_type == "global"
    if project_id:
        return memory.project_id == project_id
    if include_global:
        return memory.scope_type == "global"
    return True


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
