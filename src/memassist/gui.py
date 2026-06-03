from __future__ import annotations

import json
import re
import sqlite3
import sys
import threading
import webbrowser
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .integrations.registry import SUPPORTED_TOOLS, status_tools
from .models import Memory
from .paths import db_path
from .policy import load_policy
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
    "/api/policy",
    "/api/tools",
)
SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|password|passwd|credential|authorization|api[_-]?key|refresh[_-]?token)",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,})"
)


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
            elif parsed.path == "/api/policy":
                self._send_json(_api_policy(query))
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
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, data: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
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
        memory_where, memory_params = _memory_scope_clause(project_filter, include_global=scope.include_global)
        trace_where, trace_params = _event_scope_clause(project_filter)

        memory_total = _count(conn, "memories", memory_where, memory_params)
        trace_total = _count(conn, "trace_events", trace_where, trace_params)
        lifecycle_total = _count(conn, "lifecycle_events", trace_where, trace_params)
        session_total = _session_count(conn, project_filter)
        status_counts = _group_counts(
            conn,
            "memories",
            "status",
            memory_where,
            memory_params,
        )
        type_counts = _group_counts(
            conn,
            "memories",
            "type",
            memory_where,
            memory_params,
        )
        recent_memories = [
            _memory_dict(row)
            for row in conn.execute(
                f"SELECT * FROM memories {memory_where} ORDER BY updated_at DESC LIMIT 8",
                memory_params,
            ).fetchall()
        ]
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
        rows = conn.execute(
            """
            SELECT p.*,
              (SELECT COUNT(*) FROM memories m WHERE m.project_id = p.id) AS memory_count,
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
    clauses: list[str] = []
    params: list[Any] = []
    scope_clause, scope_params = _memory_scope_clause(
        scope.project_id,
        include_global=scope.include_global,
        include_where=False,
    )
    if scope_clause:
        clauses.append(scope_clause)
        params.extend(scope_params)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if memory_type:
        clauses.append("type = ?")
        params.append(memory_type)
    if search:
        like = f"%{search}%"
        clauses.append(
            "(id LIKE ? OR content LIKE ? OR reason LIKE ? OR tags_json LIKE ? OR paths_json LIKE ?)"
        )
        params.extend([like, like, like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _readonly_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM memories
            {where}
            ORDER BY
              CASE status WHEN 'pinned' THEN 1 ELSE 0 END DESC,
              CASE status WHEN 'policy_active' THEN 1 ELSE 0 END DESC,
              importance DESC,
              updated_at DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return {
        "scope": scope.as_dict(),
        "filters": {"status": status, "type": memory_type, "q": search, "limit": limit},
        "memories": [_memory_dict(row) for row in rows],
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


def _api_policy(query: dict[str, str]) -> dict[str, Any]:
    project = _resolve_project(query.get("project_id"))
    config = load_policy(project.root)
    path = project.root / ".memassist" / "policy.yaml"
    return {
        "project": _project_as_dict(project),
        "path": str(path),
        "exists": path.exists(),
        "policy": {
            "sensitive_paths": config.sensitive_paths,
            "protected_paths": config.protected_paths,
            "dangerous_commands": config.dangerous_commands,
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
          enforcement TEXT,
          source_kind TEXT,
          source_ref TEXT,
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
          policy_decision TEXT,
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


def _memory_scope_clause(
    project_id: str | None,
    *,
    include_global: bool,
    include_where: bool = True,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if project_id and include_global:
        clauses.append("(project_id = ? OR scope_type = 'global')")
        params.append(project_id)
    elif project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    where = f"WHERE {' AND '.join(clauses)}" if include_where and clauses else " AND ".join(clauses)
    return where, params


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


def _group_counts(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    where: str,
    params: list[Any],
) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT {column} AS key, COUNT(*) AS count FROM {table} {where} GROUP BY {column} ORDER BY count DESC",
        params,
    ).fetchall()
    return {str(row["key"]): int(row["count"]) for row in rows}


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
        project_id=row["project_id"],
        session_id=row["session_id"],
        type=str(row["type"]),
        content=str(row["content"]),
        reason=row["reason"],
        tags=_json_list(row["tags_json"]),
        paths=_json_list(row["paths_json"]),
        status=str(row["status"]),
        importance=float(row["importance"]),
        confidence=float(row["confidence"]),
        enforcement=str(row["enforcement"]),
        source_kind=str(row["source_kind"]),
        source_ref=row["source_ref"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_used_at=row["last_used_at"],
        expires_at=row["expires_at"],
        superseded_by=row["superseded_by"],
    )


def _memory_dict(row: sqlite3.Row) -> dict[str, Any]:
    memory = _row_to_memory(row).as_dict()
    for key in ("content", "reason", "source_ref"):
        memory[key] = _redact_value(memory.get(key))
    memory["tags"] = _redact_value(memory.get("tags"))
    memory["paths"] = _redact_value(memory.get("paths"))
    return memory


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
        "policy_decision": row["policy_decision"],
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
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if SENSITIVE_KEY_RE.search(str(key)):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        value = SECRET_VALUE_RE.sub("[redacted]", value)
        return re.sub(
            r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|AUTHORIZATION)[A-Z0-9_]*)\s*=\s*([^\s]+)",
            lambda match: f"{match.group(1)}=[redacted]",
            value,
        )
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
        <button data-view="policy">Policy</button>
        <button data-view="tools">Tools</button>
      </div>
    </div>
    <section class="grid" id="metrics"></section>
    <section class="panel" id="memoriesView"><h2>Memories</h2><div id="memories"></div></section>
    <section class="panel hidden" id="tracesView"><h2>Traces</h2><div id="traces"></div></section>
    <section class="panel hidden" id="lifecycleView"><h2>Lifecycle</h2><div id="lifecycle"></div></section>
    <section class="two hidden" id="policyView">
      <div class="panel"><h2>Policy</h2><div id="policy"></div></div>
      <div class="panel"><h2>Raw policy.yaml</h2><pre id="rawPolicy"></pre></div>
    </section>
    <section class="panel hidden" id="toolsView"><h2>Tools</h2><div id="tools"></div></section>
  </main>
  <script>
    const ALL_PROJECTS = {json.dumps(ALL_PROJECTS)};
    const state = {{ view: "memories", projects: [], projectId: "", summary: null }};
    const el = id => document.getElementById(id);
    const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[ch]));
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
      const data = await getJson("/api/projects");
      state.projects = data.projects || [];
      const current = data.current_project || {{}};
      const options = [`<option value="">Current project</option>`, `<option value="${{ALL_PROJECTS}}">All projects</option>`]
        .concat(state.projects.map(p => `<option value="${{esc(p.id)}}">${{esc(p.id)}}${{p.is_current ? " (current)" : ""}}</option>`));
      el("project").innerHTML = options.join("");
      el("root").textContent = current.root_path || "";
      bind();
      await refreshAll();
    }}
    function bind() {{
      el("project").addEventListener("change", e => {{ state.projectId = e.target.value; refreshAll(); }});
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
    function showView() {{
      ["memories", "traces", "lifecycle", "policy", "tools"].forEach(name => {{
        el(name + "View").classList.toggle("hidden", state.view !== name);
      }});
    }}
    async function refreshAll() {{
      el("status").textContent = "Loading";
      try {{
        state.summary = await getJson("/api/summary", {{ project_id: projectQuery() }});
        renderMetrics(state.summary);
        renderFilters(state.summary);
        if (state.view === "memories") await loadMemories();
        if (state.view === "traces") await loadTraces();
        if (state.view === "lifecycle") await loadLifecycle();
        if (state.view === "policy") await loadPolicy();
        if (state.view === "tools") await loadTools();
        el("status").textContent = "Ready";
      }} catch (error) {{
        el("status").textContent = "Error";
        console.error(error);
      }}
    }}
    function renderMetrics(summary) {{
      const counts = summary.counts || {{}};
      el("metrics").innerHTML = [
        ["Memories", counts.memories],
        ["Sessions", counts.sessions],
        ["Trace events", counts.traces],
        ["Lifecycle events", counts.lifecycle],
      ].map(([label, value]) => `<div class="metric"><div class="label">${{label}}</div><div class="value">${{value ?? 0}}</div></div>`).join("");
    }}
    function renderFilters(summary) {{
      const selectedStatus = el("statusFilter").value;
      const selectedType = el("typeFilter").value;
      const statuses = Object.keys(summary.memory_statuses || {{}}).sort();
      const types = Object.keys(summary.memory_types || {{}}).sort();
      el("statusFilter").innerHTML = `<option value="">All statuses</option>` + statuses.map(v => `<option value="${{esc(v)}}">${{esc(v)}} (${{summary.memory_statuses[v]}})</option>`).join("");
      el("typeFilter").innerHTML = `<option value="">All types</option>` + types.map(v => `<option value="${{esc(v)}}">${{esc(v)}} (${{summary.memory_types[v]}})</option>`).join("");
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
      const rows = (data.memories || []).map(m => `<tr>
        <td style="width: 12%">${{esc(m.type)}}<br><span class="muted">${{esc(m.status)}}</span></td>
        <td class="content">${{esc(m.content)}}<div>${{(m.tags || []).map(tag => `<span class="pill">${{esc(tag)}}</span>`).join("")}}</div></td>
        <td style="width: 18%">${{(m.paths || []).map(path => `<span class="pill">${{esc(path)}}</span>`).join("")}}</td>
        <td style="width: 12%">${{Number(m.importance).toFixed(2)}} / ${{Number(m.confidence).toFixed(2)}}<br><span class="muted">${{esc(m.enforcement)}}</span></td>
        <td style="width: 16%"><span class="muted">${{esc(m.updated_at)}}</span><br>${{esc(m.id)}}</td>
      </tr>`).join("");
      el("memories").innerHTML = table(["Type", "Content", "Paths", "Score", "Updated"], rows);
    }}
    async function loadTraces() {{
      const data = await getJson("/api/traces", {{ project_id: projectQuery(), session_id: el("session").value, limit: 150 }});
      const rows = (data.traces || []).map(t => `<tr>
        <td style="width: 16%">${{esc(t.created_at)}}<br><span class="muted">${{esc(t.session_id)}}</span></td>
        <td style="width: 14%">${{esc(t.event_type)}}<br><span class="muted">${{esc(t.tool_name || "")}}</span></td>
        <td style="width: 12%">${{decision(t.policy_decision)}}</td>
        <td>${{(t.files || []).map(path => `<span class="pill">${{esc(path)}}</span>`).join("")}}</td>
        <td style="width: 28%"><pre>${{esc(JSON.stringify(t.input || {{}}, null, 2))}}</pre></td>
      </tr>`).join("");
      el("traces").innerHTML = table(["Time", "Event", "Policy", "Files", "Input"], rows);
    }}
    async function loadLifecycle() {{
      const data = await getJson("/api/lifecycle", {{ project_id: projectQuery(), session_id: el("session").value, limit: 150 }});
      const rows = (data.lifecycle || []).map(l => `<tr>
        <td style="width: 16%">${{esc(l.created_at)}}<br><span class="muted">${{esc(l.session_id || "")}}</span></td>
        <td style="width: 16%">${{esc(l.decision)}}<br>${{risk(l.risk)}}</td>
        <td>${{esc(l.reason)}}<pre>${{esc(JSON.stringify(l.candidate || {{}}, null, 2))}}</pre></td>
        <td style="width: 16%">${{esc(l.memory_id || "")}}</td>
      </tr>`).join("");
      el("lifecycle").innerHTML = table(["Time", "Decision", "Reason", "Memory"], rows);
    }}
    async function loadPolicy() {{
      const data = await getJson("/api/policy", {{ project_id: projectQuery() }});
      const p = data.policy || {{}};
      el("policy").innerHTML = table(["Key", "Values"], Object.entries(p).map(([key, values]) => `<tr><td style="width: 28%">${{esc(key)}}</td><td>${{(values || []).map(v => `<span class="pill">${{esc(v)}}</span>`).join("") || "<span class='muted'>empty</span>"}}</td></tr>`).join(""));
      el("rawPolicy").textContent = data.raw || "";
    }}
    async function loadTools() {{
      const data = await getJson("/api/tools", {{ project_id: projectQuery() }});
      const rows = (data.tools || []).map(t => `<tr>
        <td style="width: 12%">${{esc(t.tool)}}</td>
        <td style="width: 12%">${{t.installed ? "<span class='pill'>installed</span>" : "<span class='pill warn'>missing</span>"}}</td>
        <td>${{esc(t.path)}}</td>
        <td style="width: 20%">${{(t.events || []).map(v => `<span class="pill">${{esc(v)}}</span>`).join("")}}</td>
        <td style="width: 24%">${{esc(t.detail)}}</td>
      </tr>`).join("");
      el("tools").innerHTML = table(["Tool", "State", "Path", "Events", "Detail"], rows);
    }}
    function table(headers, rows) {{
      return `<table><thead><tr>${{headers.map(h => `<th>${{esc(h)}}</th>`).join("")}}</tr></thead><tbody>${{rows || `<tr><td colspan="${{headers.length}}" class="muted">No records</td></tr>`}}</tbody></table>`;
    }}
    function decision(value) {{
      if (!value) return "";
      const klass = value === "deny" || value === "require_approval" ? "bad" : value === "warn" ? "warn" : "";
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
