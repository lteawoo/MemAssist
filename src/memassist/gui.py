from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
import threading
import webbrowser
from contextlib import contextmanager
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .integrations.registry import SUPPORTED_TOOLS, status_tools
from .memory_artifacts import load_memory_artifacts
from .models import Memory
from .paths import db_path
from .verification_config import load_verification_config
from .project import Project, detect_project


ALL_PROJECTS = "all"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
READ_ONLY_API_ROUTES = (
    "/api/summary",
    "/api/projects",
    "/api/memories",
    "/api/traces",
    "/api/lifecycle",
    "/api/verification-config",
    "/api/tools",
)
SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,})"
)
GUI_STATUS_WEIGHTS = {
    "active": 0.65,
    "candidate": 0.35,
    "archived": 0.10,
}
GUI_I18N = {
    "en": {
        "language": "Language",
        "currentProject": "Current project",
        "allProjects": "All projects",
        "currentSuffix": "current",
        "loading": "Loading",
        "ready": "Ready",
        "error": "Error",
        "searchMemories": "Search memories",
        "allStatuses": "All statuses",
        "allTypes": "All types",
        "sessionId": "Session id",
        "refresh": "Refresh",
        "memories": "Memories",
        "sessions": "Sessions",
        "traces": "Traces",
        "traceEvents": "Trace events",
        "lifecycle": "Lifecycle",
        "lifecycleEvents": "Lifecycle events",
        "verification": "Verification",
        "rawVerificationConfig": "Raw verification config",
        "cautionLevel": "Caution",
        "toolDecision": "Tool decision",
        "tools": "Tools",
        "type": "Type",
        "content": "Content",
        "paths": "Paths",
        "priority": "Priority",
        "relevance": "Relevance",
        "updated": "Updated",
        "time": "Time",
        "event": "Event",
        "files": "Files",
        "input": "Input",
        "decision": "Decision",
        "reason": "Reason",
        "memory": "Memory",
        "key": "Key",
        "values": "Values",
        "tool": "Tool",
        "state": "State",
        "path": "Path",
        "events": "Events",
        "detail": "Detail",
        "installed": "installed",
        "missing": "missing",
        "empty": "No records",
        "emptyValue": "empty",
        "confidence": "confidence",
        "strength": "strength",
        "utility": "utility",
        "uses": "uses",
        "recurrence": "recurs",
        "importance": "importance",
        "metricHelp": "metric help",
        "priorityTooltip": (
            "Priority is a 0-100 ranking score derived from status, strength, utility, "
            "confidence, importance, recurrence, and use count. It is not a correctness guarantee."
        ),
        "relevanceTooltip": (
            "Relevance is query-dependent. It combines search match strength with the memory's "
            "priority baseline."
        ),
        "confidenceTooltip": "Confidence estimates how reliable the memory is.",
        "strengthTooltip": "Strength rises when related or repeated evidence reinforces the memory.",
        "utilityTooltip": "Utility reflects whether the memory has been useful in retrieval.",
        "usesTooltip": "Uses is the stored retrieval count for this memory.",
        "recurrenceTooltip": "Recurrence is how many times equivalent meaning has been reinforced.",
        "importanceTooltip": "Importance is the stored significance assigned to the memory.",
    },
    "ko": {
        "language": "언어",
        "currentProject": "현재 프로젝트",
        "allProjects": "전체 프로젝트",
        "currentSuffix": "현재",
        "loading": "불러오는 중",
        "ready": "준비됨",
        "error": "오류",
        "searchMemories": "메모리 검색",
        "allStatuses": "전체 상태",
        "allTypes": "전체 유형",
        "sessionId": "세션 ID",
        "refresh": "새로고침",
        "memories": "메모리",
        "sessions": "세션",
        "traces": "트레이스",
        "traceEvents": "트레이스 이벤트",
        "lifecycle": "라이프사이클",
        "lifecycleEvents": "라이프사이클 이벤트",
        "verification": "검증",
        "rawVerificationConfig": "원본 검증 설정",
        "cautionLevel": "주의",
        "toolDecision": "도구 결정",
        "tools": "도구",
        "type": "유형",
        "content": "내용",
        "paths": "경로",
        "priority": "우선순위",
        "relevance": "관련도",
        "updated": "업데이트",
        "time": "시간",
        "event": "이벤트",
        "files": "파일",
        "input": "입력",
        "decision": "결정",
        "reason": "이유",
        "memory": "메모리",
        "key": "키",
        "values": "값",
        "tool": "도구",
        "state": "상태",
        "path": "경로",
        "events": "이벤트",
        "detail": "상세",
        "installed": "설치됨",
        "missing": "없음",
        "empty": "기록 없음",
        "emptyValue": "비어 있음",
        "confidence": "신뢰도",
        "strength": "강도",
        "utility": "효용",
        "uses": "사용",
        "recurrence": "반복",
        "importance": "중요도",
        "metricHelp": "지표 도움말",
        "priorityTooltip": (
            "우선순위는 상태, 강도, 효용, 신뢰도, 중요도, 반복, 사용 횟수에서 계산한 "
            "0-100 순위 점수입니다. 정답 보장은 아닙니다."
        ),
        "relevanceTooltip": "관련도는 검색어가 있을 때만 계산되며 검색 일치도와 메모리 우선순위를 함께 반영합니다.",
        "confidenceTooltip": "신뢰도는 이 메모리가 맞다고 볼 수 있는 정도입니다.",
        "strengthTooltip": "강도는 관련되거나 반복된 근거가 메모리를 강화할수록 올라갑니다.",
        "utilityTooltip": "효용은 이 메모리가 검색/주입에서 유용했는지를 나타냅니다.",
        "usesTooltip": "사용은 저장된 검색 사용 횟수입니다.",
        "recurrenceTooltip": "반복은 같은 의미가 강화된 횟수입니다.",
        "importanceTooltip": "중요도는 메모리에 부여된 중요도 값입니다.",
    },
}


def run_gui(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    """Run the read-only memassist web GUI until interrupted."""
    try:
        server = create_server(host=host, port=port)
    except (OSError, ValueError) as exc:
        print(f"memassist GUI failed to bind {host}:{port}: {exc}", file=sys.stderr)
        return 1

    url_host = "127.0.0.1" if host in {"localhost", "::1"} else host
    url = f"http://{url_host}:{port}/"
    print(f"memassist GUI: {url}", file=sys.stderr)
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nmemassist GUI stopped.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in LOCAL_HOSTS:
        raise ValueError("memassist GUI only binds to localhost addresses")
    return ThreadingHTTPServer((host, int(port)), MemassistRequestHandler)


class MemassistRequestHandler(BaseHTTPRequestHandler):
    server_version = "memassist-gui/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = _query(parsed.query)
        try:
            if parsed.path == "/":
                self._send_html(_dashboard_html())
            elif parsed.path == "/api/summary":
                self._send_json(_api_summary(query))
            elif parsed.path == "/api/projects":
                self._send_json(_api_projects())
            elif parsed.path == "/api/memories":
                self._send_json(_api_memories(query))
            elif parsed.path == "/api/traces":
                self._send_json(_api_traces(query))
            elif parsed.path == "/api/lifecycle":
                self._send_json(_api_lifecycle(query))
            elif parsed.path == "/api/verification-config":
                self._send_json(_api_verification_config(query))
            elif parsed.path == "/api/tools":
                self._send_json(_api_tools(query))
            else:
                self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        self._send_json({"error": "read-only endpoint"}, status=HTTPStatus.METHOD_NOT_ALLOWED)

    def do_PUT(self) -> None:
        self._send_json({"error": "read-only endpoint"}, status=HTTPStatus.METHOD_NOT_ALLOWED)

    def do_PATCH(self) -> None:
        self._send_json({"error": "read-only endpoint"}, status=HTTPStatus.METHOD_NOT_ALLOWED)

    def do_DELETE(self) -> None:
        self._send_json({"error": "read-only endpoint"}, status=HTTPStatus.METHOD_NOT_ALLOWED)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}", file=sys.stderr)

    def _send_html(self, body: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = _utf8(body)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, data: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = _utf8(json.dumps(data, ensure_ascii=False, indent=2))
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _api_summary(query: dict[str, str]) -> dict[str, Any]:
    project = detect_project()
    scope = _project_scope(query.get("project_id"), project)
    with _readonly_conn() as conn:
        project_filter = scope.project_id
        trace_where, trace_params = _event_scope_clause(project_filter)
        memories = _load_gui_memories(conn, project_id=project_filter, include_global=scope.include_global)

        memory_total = len(memories)
        trace_total = _count(conn, "trace_events", trace_where, trace_params)
        lifecycle_total = _count(conn, "lifecycle_events", trace_where, trace_params)
        session_total = _session_count(conn, project_filter)
        status_counts = _memory_group_counts(memories, "status")
        type_counts = _memory_group_counts(memories, "type")
        recent_memories = [_memory_dict(memory) for memory in sorted(memories, key=lambda item: item.updated_at or "", reverse=True)[:8]]
        recent_traces = [_trace_row(row) for row in _trace_rows(conn, project_filter, None, 8)]

    return {
        "db_path": str(db_path()),
        "scope": scope.as_dict(),
        "current_project": _project_as_dict(project),
        "counts": {
            "memories": memory_total,
            "traces": trace_total,
            "lifecycle": lifecycle_total,
            "sessions": session_total,
        },
        "memory_statuses": status_counts,
        "memory_types": type_counts,
        "recent_memories": recent_memories,
        "recent_traces": recent_traces,
    }


def _api_projects() -> dict[str, Any]:
    current = detect_project()
    with _readonly_conn() as conn:
        memories = _load_gui_memories(conn, project_id=None, include_global=False)
        rows = conn.execute(
            """
            SELECT p.*,
              (SELECT COUNT(*) FROM trace_events t WHERE t.project_id = p.id) AS trace_count,
              (SELECT COUNT(DISTINCT session_id) FROM trace_events t WHERE t.project_id = p.id) AS session_count
            FROM projects p
            ORDER BY p.last_seen_at DESC, p.root_path ASC
            """
        ).fetchall()
    projects = []
    seen_current = False
    for row in rows:
        item = dict(row)
        item["is_current"] = row["id"] == current.id
        item["memory_count"] = sum(1 for memory in memories if memory.project_id == row["id"])
        seen_current = seen_current or item["is_current"]
        projects.append(item)
    if not seen_current:
        detected = _project_as_dict(current)
        detected.update(
            {
                "created_at": None,
                "last_seen_at": None,
                "memory_count": 0,
                "trace_count": 0,
                "session_count": 0,
                "is_current": True,
                "detected_only": True,
            }
        )
        projects.insert(0, detected)
    return {
        "current_project": _project_as_dict(current),
        "projects": projects,
        "all_project_id": ALL_PROJECTS,
    }


def _api_memories(query: dict[str, str]) -> dict[str, Any]:
    project = detect_project()
    scope = _project_scope(query.get("project_id"), project)
    status = _none_if_blank(query.get("status"))
    memory_type = _none_if_blank(query.get("type"))
    search = _none_if_blank(query.get("q"))
    limit = _limit(query.get("limit"), default=MAX_LIMIT)
    with _readonly_conn() as conn:
        records = _load_gui_memories(
            conn,
            project_id=scope.project_id,
            include_global=scope.include_global,
            status=status,
            memory_type=memory_type,
            search=search,
        )
    memories = [_memory_dict(memory, search=search) for memory in records]
    metric_key = "relevance" if search else "priority"
    memories.sort(
        key=lambda memory: (
            memory["metrics"].get(metric_key) or 0,
            memory["metrics"].get("priority") or 0,
            memory.get("updated_at") or "",
        ),
        reverse=True,
    )
    return {
        "scope": scope.as_dict(),
        "filters": {"status": status, "type": memory_type, "q": search, "limit": limit},
        "metric": metric_key,
        "memories": memories[:limit],
    }


def _api_traces(query: dict[str, str]) -> dict[str, Any]:
    project = detect_project()
    scope = _project_scope(query.get("project_id"), project)
    session_id = _none_if_blank(query.get("session_id"))
    limit = _limit(query.get("limit"), default=DEFAULT_LIMIT)
    with _readonly_conn() as conn:
        rows = _trace_rows(conn, scope.project_id, session_id, limit)
    return {
        "scope": scope.as_dict(),
        "filters": {"session_id": session_id, "limit": limit},
        "traces": [_trace_row(row) for row in rows],
    }


def _api_lifecycle(query: dict[str, str]) -> dict[str, Any]:
    project = detect_project()
    scope = _project_scope(query.get("project_id"), project)
    session_id = _none_if_blank(query.get("session_id"))
    limit = _limit(query.get("limit"), default=DEFAULT_LIMIT)
    clauses: list[str] = []
    params: list[Any] = []
    if scope.project_id:
        clauses.append("project_id = ?")
        params.append(scope.project_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _readonly_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM lifecycle_events {where} ORDER BY created_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return {
        "scope": scope.as_dict(),
        "filters": {"session_id": session_id, "limit": limit},
        "lifecycle": [_lifecycle_row(row) for row in rows],
    }


def _api_verification_config(query: dict[str, str]) -> dict[str, Any]:
    project = _resolve_project(query.get("project_id"))
    config = load_verification_config(project.root)
    path = project.root / ".memassist" / "verification.yaml"
    return {
        "project": _project_as_dict(project),
        "path": str(path),
        "exists": path.exists(),
        "verification_config": {
            "verification_commands": config.verification_commands,
        },
        "raw": _redact_value(path.read_text(encoding="utf-8")) if path.exists() else "",
    }


def _api_tools(query: dict[str, str]) -> dict[str, Any]:
    project = _resolve_project(query.get("project_id"))
    statuses = status_tools(project, tools=list(SUPPORTED_TOOLS), scope="project")
    return {
        "project": _project_as_dict(project),
        "tools": [status.as_dict() for status in statuses],
    }


class _Scope:
    def __init__(self, raw_project_id: str | None, project_id: str | None, include_global: bool) -> None:
        self.raw_project_id = raw_project_id
        self.project_id = project_id
        self.include_global = include_global

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_project_id": self.raw_project_id,
            "project_id": self.project_id,
            "all_projects": self.project_id is None,
            "include_global": self.include_global,
        }


def _project_scope(raw_project_id: str | None, current: Project) -> _Scope:
    project_id = _none_if_blank(raw_project_id)
    if project_id in {ALL_PROJECTS, "*"}:
        return _Scope(project_id, None, include_global=False)
    if project_id is None:
        return _Scope(raw_project_id, current.id, include_global=True)
    return _Scope(project_id, project_id, include_global=True)


def _resolve_project(raw_project_id: str | None) -> Project:
    current = detect_project()
    project_id = _none_if_blank(raw_project_id)
    if project_id in {None, ALL_PROJECTS, "*", current.id}:
        return current
    with _readonly_conn() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        return current
    return Project(
        id=str(row["id"]),
        root=Path(str(row["root_path"])),
        git_remote=row["git_remote"],
    )


def _utf8(text: str) -> bytes:
    """Encode response text as UTF-8, neutralizing lone surrogates from corrupt stored bytes."""
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace")


def _query(raw_query: str) -> dict[str, str]:
    parsed = parse_qs(raw_query, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _none_if_blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _limit(value: str | None, *, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, MAX_LIMIT))


def _project_as_dict(project: Project) -> dict[str, Any]:
    return {"id": project.id, "root_path": str(project.root), "git_remote": project.git_remote}


def _load_gui_memories(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    include_global: bool,
    status: str | None = None,
    memory_type: str | None = None,
    search: str | None = None,
) -> list[Memory]:
    memories: list[Memory] = []
    query = (search or "").strip().lower()
    for memory in load_memory_artifacts(db_path().parent):
        if not _memory_matches_scope(memory, project_id=project_id, include_global=include_global):
            continue
        if status and memory.status != status:
            continue
        if memory_type and memory.type != memory_type:
            continue
        if query and not _memory_matches_query(memory, query):
            continue
        memories.append(_with_gui_telemetry(conn, memory))
    return sorted(memories, key=lambda item: (item.importance, item.updated_at or ""), reverse=True)


def _memory_matches_scope(memory: Memory, *, project_id: str | None, include_global: bool) -> bool:
    if project_id and include_global:
        return memory.project_id == project_id or memory.scope_type == "global"
    if project_id:
        return memory.project_id == project_id
    return True


def _memory_matches_query(memory: Memory, query: str) -> bool:
    haystack = " ".join(
        [
            memory.id,
            memory.type,
            memory.status,
            memory.content,
            memory.reason or "",
            " ".join(memory.tags),
            " ".join(memory.paths),
        ]
    ).lower()
    return query in haystack


def _with_gui_telemetry(conn: sqlite3.Connection, memory: Memory) -> Memory:
    try:
        row = conn.execute(
            """
            SELECT retrieval_count, utility, strength, recurrence, last_used_at
            FROM memories
            WHERE id = ?
            """,
            (memory.id,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if not row:
        return memory
    return replace(
        memory,
        retrieval_count=_int(_row_value(row, "retrieval_count")),
        utility=_float(_row_value(row, "utility"), default=memory.utility),
        strength=_float(_row_value(row, "strength"), default=memory.strength),
        recurrence=_int(_row_value(row, "recurrence"), default=memory.recurrence),
        last_used_at=_row_value(row, "last_used_at"),
    )


def _memory_group_counts(memories: list[Memory], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for memory in memories:
        key = str(getattr(memory, field))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


@contextmanager
def _readonly_conn() -> Any:
    path = db_path()
    if not path.exists():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _init_empty_schema(conn)
        try:
            yield conn
        finally:
            conn.close()
        return
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


def _init_empty_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE projects (
          id TEXT PRIMARY KEY,
          root_path TEXT NOT NULL,
          git_remote TEXT,
          created_at TEXT,
          last_seen_at TEXT
        );
        CREATE TABLE memories (
          id TEXT PRIMARY KEY,
          scope_type TEXT,
          project_id TEXT,
          session_id TEXT,
          type TEXT,
          content TEXT,
          reason TEXT,
          tags_json TEXT,
          paths_json TEXT,
          status TEXT,
          importance REAL,
          confidence REAL,
          strength REAL,
          recurrence INTEGER,
          retrieval_count INTEGER,
          utility REAL,
          half_life_days REAL,
          caution_level TEXT,
          source_kind TEXT,
          source_ref TEXT,
          source_ids_json TEXT,
          source_quote TEXT,
          content_hash TEXT,
          artifact_path TEXT,
          indexed_at TEXT,
          created_at TEXT,
          updated_at TEXT,
          last_used_at TEXT,
          expires_at TEXT,
          superseded_by TEXT
        );
        CREATE TABLE trace_events (
          id TEXT PRIMARY KEY,
          session_id TEXT,
          project_id TEXT,
          event_type TEXT,
          tool_name TEXT,
          input_json TEXT,
          output_summary TEXT,
          tool_decision TEXT,
          files_json TEXT,
          created_at TEXT
        );
        CREATE TABLE lifecycle_events (
          id TEXT PRIMARY KEY,
          session_id TEXT,
          project_id TEXT,
          memory_id TEXT,
          candidate_json TEXT,
          decision TEXT,
          risk TEXT,
          reason TEXT,
          created_at TEXT
        );
        """
    )


def _event_scope_clause(project_id: str | None) -> tuple[str, list[Any]]:
    if not project_id:
        return "", []
    return "WHERE project_id = ?", [project_id]


def _count(conn: sqlite3.Connection, table: str, where: str, params: list[Any]) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table} {where}", params).fetchone()
    return int(row["count"] if row else 0)


def _session_count(conn: sqlite3.Connection, project_id: str | None) -> int:
    where, params = _event_scope_clause(project_id)
    row = conn.execute(
        f"SELECT COUNT(DISTINCT session_id) AS count FROM trace_events {where}",
        params,
    ).fetchone()
    return int(row["count"] if row else 0)


def _trace_rows(
    conn: sqlite3.Connection,
    project_id: str | None,
    session_id: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return conn.execute(
        f"SELECT * FROM trace_events {where} ORDER BY created_at DESC LIMIT ?",
        [*params, limit],
    ).fetchall()


def _row_to_memory(row: sqlite3.Row) -> Memory:
    return Memory(
        id=str(row["id"]),
        scope_type=str(row["scope_type"]),
        project_id=_row_value(row, "project_id"),
        session_id=_row_value(row, "session_id"),
        type=str(row["type"]),
        content=str(row["content"]),
        reason=_row_value(row, "reason"),
        tags=_json_list(_row_value(row, "tags_json")),
        paths=_json_list(_row_value(row, "paths_json")),
        status=str(row["status"]),
        importance=_float(_row_value(row, "importance"), default=0.5),
        confidence=_float(_row_value(row, "confidence"), default=0.8),
        strength=_float(_row_value(row, "strength"), default=0.5),
        recurrence=_int(_row_value(row, "recurrence"), default=1),
        retrieval_count=_int(_row_value(row, "retrieval_count")),
        utility=_float(_row_value(row, "utility")),
        half_life_days=float(_row_value(row, "half_life_days", 30.0) or 30.0),
        caution_level=str(_row_value(row, "caution_level", "none")),
        source_kind=str(_row_value(row, "source_kind", "manual")),
        source_ref=_row_value(row, "source_ref"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_used_at=_row_value(row, "last_used_at"),
        expires_at=_row_value(row, "expires_at"),
        superseded_by=_row_value(row, "superseded_by"),
        source_quote=_row_value(row, "source_quote"),
        source_ids=_json_list(_row_value(row, "source_ids_json")),
        content_hash=_row_value(row, "content_hash"),
    )


def _row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def _memory_dict(record: Memory | sqlite3.Row, *, search: str | None = None) -> dict[str, Any]:
    memory = (record if isinstance(record, Memory) else _row_to_memory(record)).as_dict()
    metrics = _memory_metrics(memory, search=search)
    for key in ("content", "reason", "source_ref"):
        memory[key] = _redact_value(memory.get(key))
    memory["tags"] = _redact_value(memory.get("tags"))
    memory["paths"] = _redact_value(memory.get("paths"))
    memory["metrics"] = metrics
    memory["priority"] = metrics["priority"]
    memory["relevance"] = metrics["relevance"]
    memory["caution_level"] = memory.get("caution_level") or "none"
    return memory


def _memory_metrics(memory: dict[str, Any], *, search: str | None = None) -> dict[str, Any]:
    priority = _priority_score(memory)
    relevance = _relevance_score(memory, search, priority) if search else None
    return {
        "priority": priority,
        "relevance": relevance,
        "evidence": {
            "confidence": round(_float(memory.get("confidence")), 2),
            "strength": round(_float(memory.get("strength")), 2),
            "utility": round(_float(memory.get("utility")), 2),
            "uses": _int(memory.get("retrieval_count")),
            "recurrence": _int(memory.get("recurrence")),
            "importance": round(_float(memory.get("importance")), 2),
        },
    }


def _priority_score(memory: dict[str, Any]) -> int:
    status_weight = GUI_STATUS_WEIGHTS.get(str(memory.get("status") or ""), 0.1)
    recurrence = min(_int(memory.get("recurrence")) / 5.0, 1.0)
    retrieval_count = _int(memory.get("retrieval_count"))
    uses = min(math.log1p(max(retrieval_count, 0)) / math.log1p(20), 1.0)
    score = (
        status_weight * 0.22
        + _float(memory.get("strength")) * 0.20
        + _float(memory.get("utility")) * 0.16
        + _float(memory.get("confidence")) * 0.16
        + _float(memory.get("importance")) * 0.14
        + recurrence * 0.07
        + uses * 0.05
    )
    return int(round(_clamp(score) * 100))


def _relevance_score(memory: dict[str, Any], search: str | None, priority: int) -> int:
    match = _query_match_score(memory, search)
    baseline = priority / 100.0
    return int(round(_clamp(match * 0.72 + baseline * 0.28) * 100))


def _query_match_score(memory: dict[str, Any], search: str | None) -> float:
    query = (search or "").strip().lower()
    if not query:
        return 0.0
    tokens = [token for token in re.findall(r"[A-Za-z0-9_가-힣]+", query) if token]
    if not tokens:
        return 0.0
    content = str(memory.get("content") or "").lower()
    tags = " ".join(str(tag) for tag in memory.get("tags") or []).lower()
    paths = " ".join(str(path) for path in memory.get("paths") or []).lower()
    metadata = " ".join(
        [
            str(memory.get("id") or ""),
            str(memory.get("type") or ""),
            str(memory.get("status") or ""),
            tags,
            paths,
            str(memory.get("reason") or ""),
        ]
    ).lower()
    haystack = f"{content} {metadata}"
    token_hits = sum(1 for token in tokens if token in haystack) / len(tokens)
    phrase_bonus = 0.18 if query in haystack else 0.0
    content_bonus = 0.12 if any(token in content for token in tokens) else 0.0
    metadata_bonus = 0.08 if any(token in metadata for token in tokens) else 0.0
    return _clamp(token_hits * 0.62 + phrase_bonus + content_bonus + metadata_bonus)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return _clamp(float(value))
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _trace_row(row: sqlite3.Row) -> dict[str, Any]:
    raw_input = _json_object(row["input_json"])
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "project_id": row["project_id"],
        "event_type": row["event_type"],
        "tool_name": row["tool_name"],
        "input": _redact_value(raw_input),
        "input_preview": _preview(raw_input),
        "output_summary": _redact_value(row["output_summary"]),
        "tool_decision": row["tool_decision"],
        "files": _redact_value(_json_list(row["files_json"])),
        "created_at": row["created_at"],
    }


def _lifecycle_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "project_id": row["project_id"],
        "memory_id": row["memory_id"],
        "candidate": _redact_value(_json_object(row["candidate_json"])),
        "decision": row["decision"],
        "risk": row["risk"],
        "reason": _redact_value(row["reason"]),
        "created_at": row["created_at"],
    }


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {"raw": str(value)}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE_RE.sub("[redacted]", value)
    return value


def _preview(value: Any, limit: int = 180) -> str:
    text = json.dumps(_redact_value(value), ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _dashboard_html() -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>memassist</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --panel-soft: #eef3f1;
      --text: #1f2328;
      --muted: #66707a;
      --line: #d7dde2;
      --accent: #1b6f68;
      --accent-soft: #dcefeb;
      --warn: #946200;
      --bad: #a33b3b;
      --code: #263141;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 13px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    header {{
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 14px 20px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
      position: sticky;
      top: 0;
      z-index: 2;
    }}
    h1 {{ font-size: 16px; margin: 0; font-weight: 650; }}
    select, input {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      min-height: 30px;
      padding: 5px 8px;
      border-radius: 4px;
      font: inherit;
    }}
    input {{ min-width: 240px; }}
    button {{
      border: 1px solid #b9c4ca;
      background: #fff;
      color: var(--text);
      min-height: 30px;
      padding: 5px 10px;
      border-radius: 4px;
      cursor: pointer;
      font: inherit;
    }}
    button.active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
    main {{ padding: 16px 20px 24px; }}
    .toolbar {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }}
    .tabs {{ display: flex; gap: 4px; margin-left: auto; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
    }}
    .metric {{ padding: 10px 12px; min-height: 68px; }}
    .metric .label {{ color: var(--muted); font-size: 12px; }}
    .metric .value {{ font-size: 24px; font-weight: 650; margin-top: 4px; }}
    .panel {{ margin-bottom: 14px; overflow: hidden; }}
    .panel h2 {{
      margin: 0;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
      background: #fbfcfd;
    }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 7px 8px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }}
    th {{ color: var(--muted); font-weight: 600; background: #fbfcfd; }}
    tr:last-child td {{ border-bottom: 0; }}
    .muted {{ color: var(--muted); }}
    .pill {{
      display: inline-block;
      border-radius: 999px;
      padding: 1px 7px;
      background: var(--panel-soft);
      color: #25443f;
      font-size: 12px;
      margin: 0 3px 3px 0;
      max-width: 100%;
    }}
    .pill.warn {{ color: var(--warn); background: #fff3cf; }}
    .pill.bad {{ color: var(--bad); background: #ffe3df; }}
    pre {{
      margin: 0;
      padding: 12px;
      max-height: 360px;
      overflow: auto;
      background: #f1f4f6;
      color: var(--code);
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre-wrap;
    }}
    .two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .hidden {{ display: none; }}
    .content {{ max-width: 980px; }}
    .status {{ margin-left: auto; color: var(--muted); }}
    .locale-label {{
      color: var(--muted);
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .th-label, .metric-line {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      flex-wrap: wrap;
    }}
    .help-wrap {{
      position: relative;
      display: inline-flex;
      align-items: center;
    }}
    .help {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 16px;
      height: 16px;
      border: 1px solid #a8b4bb;
      border-radius: 50%;
      background: #fff;
      color: var(--muted);
      font-size: 11px;
      line-height: 1;
      cursor: help;
    }}
    .tooltip {{
      position: absolute;
      top: calc(100% + 6px);
      right: 0;
      width: min(360px, 72vw);
      padding: 10px 11px;
      border: 1px solid #b9c4ca;
      border-radius: 6px;
      background: #ffffff;
      color: var(--text);
      box-shadow: 0 8px 24px rgba(31, 35, 40, 0.14);
      font-size: 12px;
      font-weight: 400;
      line-height: 1.45;
      text-align: left;
      white-space: normal;
      z-index: 5;
      opacity: 0;
      pointer-events: none;
      transform: translateY(-2px);
      transition: opacity 120ms ease, transform 120ms ease;
    }}
    .tooltip div + div {{
      margin-top: 5px;
    }}
    .help-wrap:hover .tooltip,
    .help-wrap:focus-within .tooltip {{
      opacity: 1;
      transform: translateY(0);
      pointer-events: auto;
    }}
    .metric-primary {{
      font-size: 22px;
      line-height: 1.05;
      font-weight: 650;
      color: var(--accent);
    }}
    .evidence {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 2px;
      margin-top: 5px;
      color: var(--muted);
      font-size: 11px;
    }}
    @media (max-width: 900px) {{
      header, .toolbar {{ align-items: stretch; }}
      header {{ flex-direction: column; }}
      .tabs {{ margin-left: 0; }}
      .grid, .two {{ grid-template-columns: 1fr; }}
      input, select, button {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>memassist</h1>
    <select id="project"></select>
    <label class="locale-label"><span id="languageLabel">Language</span><select id="locale"><option value="en">English</option><option value="ko">한국어</option></select></label>
    <span id="root" class="muted"></span>
    <span id="status" class="status">Loading</span>
  </header>
  <main>
    <div class="toolbar">
      <input id="search" type="search" placeholder="Search memories">
      <select id="statusFilter"><option value="">All statuses</option></select>
      <select id="typeFilter"><option value="">All types</option></select>
      <input id="session" type="search" placeholder="Session id">
      <button id="refresh">Refresh</button>
      <div class="tabs">
        <button data-view="memories" class="active">Memories</button>
        <button data-view="traces">Traces</button>
        <button data-view="lifecycle">Lifecycle</button>
        <button data-view="verification">Verification</button>
        <button data-view="tools">Tools</button>
      </div>
    </div>
    <section class="grid" id="metrics"></section>
    <section class="panel" id="memoriesView"><h2 id="memoriesTitle">Memories</h2><div id="memories"></div></section>
    <section class="panel hidden" id="tracesView"><h2 id="tracesTitle">Traces</h2><div id="traces"></div></section>
    <section class="panel hidden" id="lifecycleView"><h2 id="lifecycleTitle">Lifecycle</h2><div id="lifecycle"></div></section>
    <section class="two hidden" id="verificationView">
      <div class="panel"><h2 id="verificationTitle">Verification</h2><div id="verification"></div></div>
      <div class="panel"><h2 id="rawVerificationConfigTitle">Raw verification config</h2><pre id="rawVerificationConfig"></pre></div>
    </section>
    <section class="panel hidden" id="toolsView"><h2 id="toolsTitle">Tools</h2><div id="tools"></div></section>
  </main>
  <script>
    const ALL_PROJECTS = {json.dumps(ALL_PROJECTS)};
    const I18N = {json.dumps(GUI_I18N, ensure_ascii=False)};
    const LOCALE_STORAGE_KEY = "memassist.gui.locale";
    const state = {{ view: "memories", projects: [], projectId: "", summary: null, locale: initialLocale() }};
    const el = id => document.getElementById(id);
    const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[ch]));
    function initialLocale() {{
      const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
      if (stored && I18N[stored]) return stored;
      return (navigator.language || "").toLowerCase().startsWith("ko") ? "ko" : "en";
    }}
    function t(key) {{
      return (I18N[state.locale] && I18N[state.locale][key]) || I18N.en[key] || key;
    }}
    function setLocale(locale) {{
      state.locale = I18N[locale] ? locale : "en";
      localStorage.setItem(LOCALE_STORAGE_KEY, state.locale);
      document.documentElement.lang = state.locale;
      el("locale").value = state.locale;
      applyStaticTranslations();
      renderProjectOptions();
      if (state.summary) {{
        renderMetrics(state.summary);
        renderFilters(state.summary);
      }}
      refreshVisible();
    }}
    function help(key, detailKeys=[]) {{
      const lines = [key].concat(detailKeys).map(item => `<div>${{esc(t(item))}}</div>`).join("");
      const text = [key].concat(detailKeys).map(item => t(item)).join(" ");
      return `<span class="help-wrap"><span class="help" tabindex="0" role="button" aria-label="${{esc(t("metricHelp"))}}: ${{esc(text)}}">?</span><span class="tooltip" role="tooltip">${{lines}}</span></span>`;
    }}
    function th(labelKey, tooltipKey=null, detailKeys=[]) {{
      return `<span class="th-label">${{esc(t(labelKey))}}${{tooltipKey ? help(tooltipKey, detailKeys) : ""}}</span>`;
    }}
    const params = values => {{
      const q = new URLSearchParams();
      Object.entries(values).forEach(([key, value]) => {{
        if (value !== undefined && value !== null && String(value) !== "") q.set(key, value);
      }});
      return q.toString();
    }};
    async function getJson(path, query={{}}) {{
      const response = await fetch(path + "?" + params(query));
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }}
    function projectQuery() {{ return state.projectId || ""; }}
    async function init() {{
      document.documentElement.lang = state.locale;
      el("locale").value = state.locale;
      const data = await getJson("/api/projects");
      state.projects = data.projects || [];
      state.currentProject = data.current_project || {{}};
      renderProjectOptions();
      applyStaticTranslations();
      bind();
      await refreshAll();
    }}
    function renderProjectOptions() {{
      const options = [`<option value="">${{esc(t("currentProject"))}}</option>`, `<option value="${{ALL_PROJECTS}}">${{esc(t("allProjects"))}}</option>`]
        .concat((state.projects || []).map(p => `<option value="${{esc(p.id)}}">${{esc(p.id)}}${{p.is_current ? " (" + esc(t("currentSuffix")) + ")" : ""}}</option>`));
      el("project").innerHTML = options.join("");
      el("project").value = state.projectId;
      const current = state.currentProject || {{}};
      el("root").textContent = current.root_path || "";
    }}
    function applyStaticTranslations() {{
      el("languageLabel").textContent = t("language");
      el("search").placeholder = t("searchMemories");
      el("session").placeholder = t("sessionId");
      el("refresh").textContent = t("refresh");
      el("memoriesTitle").textContent = t("memories");
      el("tracesTitle").textContent = t("traces");
      el("lifecycleTitle").textContent = t("lifecycle");
      el("verificationTitle").textContent = t("verification");
      el("rawVerificationConfigTitle").textContent = t("rawVerificationConfig");
      el("toolsTitle").textContent = t("tools");
      document.querySelector('[data-view="memories"]').textContent = t("memories");
      document.querySelector('[data-view="traces"]').textContent = t("traces");
      document.querySelector('[data-view="lifecycle"]').textContent = t("lifecycle");
      document.querySelector('[data-view="verification"]').textContent = t("verification");
      document.querySelector('[data-view="tools"]').textContent = t("tools");
      if (el("status").textContent !== t("error")) el("status").textContent = t("ready");
    }}
    function bind() {{
      el("project").addEventListener("change", e => {{ state.projectId = e.target.value; refreshAll(); }});
      el("locale").addEventListener("change", e => setLocale(e.target.value));
      el("refresh").addEventListener("click", refreshAll);
      ["search", "session"].forEach(id => el(id).addEventListener("keydown", e => {{ if (e.key === "Enter") refreshAll(); }}));
      ["statusFilter", "typeFilter"].forEach(id => el(id).addEventListener("change", refreshAll));
      document.querySelectorAll("[data-view]").forEach(button => {{
        button.addEventListener("click", () => {{
          state.view = button.dataset.view;
          document.querySelectorAll("[data-view]").forEach(b => b.classList.toggle("active", b === button));
          showView();
          refreshAll();
        }});
      }});
    }}
    function refreshVisible() {{
      if (state.view === "memories") loadMemories();
      if (state.view === "traces") loadTraces();
      if (state.view === "lifecycle") loadLifecycle();
      if (state.view === "verification") loadVerificationConfig();
      if (state.view === "tools") loadTools();
    }}
    function showView() {{
      ["memories", "traces", "lifecycle", "verification", "tools"].forEach(name => {{
        el(name + "View").classList.toggle("hidden", state.view !== name);
      }});
    }}
    async function refreshAll() {{
      el("status").textContent = t("loading");
      try {{
        state.summary = await getJson("/api/summary", {{ project_id: projectQuery() }});
        renderMetrics(state.summary);
        renderFilters(state.summary);
        if (state.view === "memories") await loadMemories();
        if (state.view === "traces") await loadTraces();
        if (state.view === "lifecycle") await loadLifecycle();
        if (state.view === "verification") await loadVerificationConfig();
        if (state.view === "tools") await loadTools();
        el("status").textContent = t("ready");
      }} catch (error) {{
        el("status").textContent = t("error");
        console.error(error);
      }}
    }}
    function renderMetrics(summary) {{
      const counts = summary.counts || {{}};
      el("metrics").innerHTML = [
        [t("memories"), counts.memories],
        [t("sessions"), counts.sessions],
        [t("traceEvents"), counts.traces],
        [t("lifecycleEvents"), counts.lifecycle],
      ].map(([label, value]) => `<div class="metric"><div class="label">${{label}}</div><div class="value">${{value ?? 0}}</div></div>`).join("");
    }}
    function renderFilters(summary) {{
      const selectedStatus = el("statusFilter").value;
      const selectedType = el("typeFilter").value;
      const statuses = Object.keys(summary.memory_statuses || {{}}).sort();
      const types = Object.keys(summary.memory_types || {{}}).sort();
      el("statusFilter").innerHTML = `<option value="">${{esc(t("allStatuses"))}}</option>` + statuses.map(v => `<option value="${{esc(v)}}">${{esc(v)}} (${{summary.memory_statuses[v]}})</option>`).join("");
      el("typeFilter").innerHTML = `<option value="">${{esc(t("allTypes"))}}</option>` + types.map(v => `<option value="${{esc(v)}}">${{esc(v)}} (${{summary.memory_types[v]}})</option>`).join("");
      el("statusFilter").value = selectedStatus;
      el("typeFilter").value = selectedType;
    }}
    async function loadMemories() {{
      const data = await getJson("/api/memories", {{
        project_id: projectQuery(),
        status: el("statusFilter").value,
        type: el("typeFilter").value,
        q: el("search").value,
      }});
      const metricKey = data.metric === "relevance" ? "relevance" : "priority";
      const metricLabel = metricKey === "relevance" ? "relevance" : "priority";
      const metricTip = metricKey === "relevance" ? "relevanceTooltip" : "priorityTooltip";
      const metricDetails = ["confidenceTooltip", "strengthTooltip", "utilityTooltip", "usesTooltip", "recurrenceTooltip", "importanceTooltip"];
      const rows = (data.memories || []).map(m => `<tr>
        <td style="width: 12%">${{esc(m.type)}}<br><span class="muted">${{esc(m.status)}}</span></td>
        <td class="content">${{esc(m.content)}}<div>${{(m.tags || []).map(tag => `<span class="pill">${{esc(tag)}}</span>`).join("")}}</div></td>
        <td style="width: 18%">${{(m.paths || []).map(path => `<span class="pill">${{esc(path)}}</span>`).join("")}}</td>
        <td style="width: 14%">${{metricCell(m, metricKey)}}<br><span class="muted">${{esc(t("cautionLevel"))}}: ${{esc(m.caution_level || "none")}}</span></td>
        <td style="width: 16%"><span class="muted">${{esc(m.updated_at)}}</span><br>${{esc(m.id)}}</td>
      </tr>`).join("");
      el("memories").innerHTML = table([th("type"), th("content"), th("paths"), th(metricLabel, metricTip, metricDetails), th("updated")], rows);
    }}
    function metricCell(memory, metricKey) {{
      const metrics = memory.metrics || {{}};
      const evidence = metrics.evidence || {{}};
      const value = metricKey === "relevance" ? metrics.relevance : metrics.priority;
      return `<div class="metric-primary">${{Number(value ?? 0).toFixed(0)}}</div>
        <div class="evidence">
          <span class="metric-line">${{esc(t("confidence"))}} ${{Number(evidence.confidence ?? 0).toFixed(2)}}</span>
          <span class="metric-line">${{esc(t("strength"))}} ${{Number(evidence.strength ?? 0).toFixed(2)}}</span>
          <span class="metric-line">${{esc(t("utility"))}} ${{Number(evidence.utility ?? 0).toFixed(2)}}</span>
          <span class="metric-line">${{esc(t("uses"))}} ${{Number(evidence.uses ?? 0).toFixed(0)}}</span>
          <span class="metric-line">${{esc(t("recurrence"))}} ${{Number(evidence.recurrence ?? 0).toFixed(0)}}</span>
        </div>`;
    }}
    async function loadTraces() {{
      const data = await getJson("/api/traces", {{ project_id: projectQuery(), session_id: el("session").value, limit: 150 }});
      const rows = (data.traces || []).map(t => `<tr>
        <td style="width: 16%">${{esc(t.created_at)}}<br><span class="muted">${{esc(t.session_id)}}</span></td>
        <td style="width: 14%">${{esc(t.event_type)}}<br><span class="muted">${{esc(t.tool_name || "")}}</span></td>
        <td style="width: 12%">${{decision(t.tool_decision)}}</td>
        <td>${{(t.files || []).map(path => `<span class="pill">${{esc(path)}}</span>`).join("")}}</td>
        <td style="width: 28%"><pre>${{esc(JSON.stringify(t.input || {{}}, null, 2))}}</pre></td>
      </tr>`).join("");
      el("traces").innerHTML = table([th("time"), th("event"), th("toolDecision"), th("files"), th("input")], rows);
    }}
    async function loadLifecycle() {{
      const data = await getJson("/api/lifecycle", {{ project_id: projectQuery(), session_id: el("session").value, limit: 150 }});
      const rows = (data.lifecycle || []).map(l => `<tr>
        <td style="width: 16%">${{esc(l.created_at)}}<br><span class="muted">${{esc(l.session_id || "")}}</span></td>
        <td style="width: 16%">${{esc(l.decision)}}<br>${{risk(l.risk)}}</td>
        <td>${{esc(l.reason)}}<pre>${{esc(JSON.stringify(l.candidate || {{}}, null, 2))}}</pre></td>
        <td style="width: 16%">${{esc(l.memory_id || "")}}</td>
      </tr>`).join("");
      el("lifecycle").innerHTML = table([th("time"), th("decision"), th("reason"), th("memory")], rows);
    }}
    async function loadVerificationConfig() {{
      const data = await getJson("/api/verification-config", {{ project_id: projectQuery() }});
      const p = data.verification_config || {{}};
      el("verification").innerHTML = table([th("key"), th("values")], Object.entries(p).map(([key, values]) => `<tr><td style="width: 28%">${{esc(key)}}</td><td>${{(values || []).map(v => `<span class="pill">${{esc(v)}}</span>`).join("") || `<span class='muted'>${{esc(t("emptyValue"))}}</span>`}}</td></tr>`).join(""));
      el("rawVerificationConfig").textContent = data.raw || "";
    }}
    async function loadTools() {{
      const data = await getJson("/api/tools", {{ project_id: projectQuery() }});
      const rows = (data.tools || []).map(tool => `<tr>
        <td style="width: 12%">${{esc(tool.tool)}}</td>
        <td style="width: 12%">${{tool.installed ? `<span class='pill'>${{esc(t("installed"))}}</span>` : `<span class='pill warn'>${{esc(t("missing"))}}</span>`}}</td>
        <td>${{esc(tool.path)}}</td>
        <td style="width: 20%">${{(tool.events || []).map(v => `<span class="pill">${{esc(v)}}</span>`).join("")}}</td>
        <td style="width: 24%">${{esc(tool.detail)}}</td>
      </tr>`).join("");
      el("tools").innerHTML = table([th("tool"), th("state"), th("path"), th("events"), th("detail")], rows);
    }}
    function table(headers, rows) {{
      return `<table><thead><tr>${{headers.map(h => `<th>${{h}}</th>`).join("")}}</tr></thead><tbody>${{rows || `<tr><td colspan="${{headers.length}}" class="muted">${{esc(t("empty"))}}</td></tr>`}}</tbody></table>`;
    }}
    function decision(value) {{
      if (!value) return "";
      const klass = value === "deny" || value === "block" ? "bad" : value === "warn" ? "warn" : "";
      return `<span class="pill ${{klass}}">${{esc(value)}}</span>`;
    }}
    function risk(value) {{
      const klass = value === "high" ? "bad" : value === "medium" ? "warn" : "";
      return `<span class="pill ${{klass}}">${{esc(value || "")}}</span>`;
    }}
    init();
  </script>
</body>
</html>
"""


GuiRequestHandler = MemassistRequestHandler
RequestHandler = MemassistRequestHandler
_Handler = MemassistRequestHandler


__all__ = ["run_gui", "create_server", "MemassistRequestHandler", "READ_ONLY_API_ROUTES"]
