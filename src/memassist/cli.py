from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .async_ingestion import (
    async_batch_limit,
    async_ingestion_enabled_for_hook,
    hook_mode,
    pending_source_count,
    run_async_ingestion_once,
    spawn_async_ingestion_worker,
    stop_ingest_mode,
    sync_ingestion_enabled_for_hook,
)
from .doctor import run_doctor
from .embedding_profiles import (
    EmbeddingProfileError,
    activate_embedding_profile,
    ensure_embedding_profiles,
    get_embedding_profile,
    load_embedding_profile_config,
)
from .embeddings import build_memory_embeddings, install_embedding_model
from .extraction import extract_candidates, store_candidates
from .integrations import install_tools, normalize_tools, repair_tools, status_tools, uninstall_tools
from .lesson import lesson_from_session
from .lifecycle import cleanup_memories, process_session_lifecycle
from .memory_judge import (
    INTERPRETER_ACTIVE_ENV,
    observe_turn_end_memory_source,
    process_pending_memory_intents,
)
from .memory_eval import (
    ACTIVE_STATUSES as MEMORY_EVAL_ACTIVE_STATUSES,
    MemoryQualityCase,
    evaluate_memory_quality,
    load_memory_quality_cases,
)
from .memory_artifacts import ensure_memory_artifact_dirs
from .paths import db_path, memassist_home, project_memassist_home
from .project import detect_project, detect_project_for_init
from .rag_eval import RagCase, RagExpectation, evaluate_rag, load_rag_cases
from .retrieval import build_memory_pack, render_prompt_context
from .retrieval_eval import RetrievalCase, evaluate_retrieval, load_cases
from .session import summarize_session
from .source_ledger import ensure_source_ledger
from .storage import Store
from .sync import export_memories, import_memories
from .trace import record_tool_event


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memassist")
    sub = parser.add_subparsers(required=True)

    init = sub.add_parser("init", help="initialize .memassist in this project")
    init.add_argument("--tools", help="also install tool integrations: codex, claude, opencode, or all")
    init.add_argument("--mode", choices=["full", "context", "trace"], default="full")
    init.add_argument("--skip-embedding-install", action="store_true", help="do not prepare the active embedding model")
    init.add_argument("--force-embedding-install", action="store_true", help="re-download or refresh the active embedding model")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="show project and storage status")
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="check memassist project setup and tool integration readiness")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    gui = sub.add_parser("gui", help="open the read-only local memory GUI")
    gui.add_argument("--host", default="127.0.0.1", help="localhost address to bind")
    gui.add_argument("--port", type=int, default=8765)
    gui.add_argument("--no-open", action="store_true", help="do not open a browser automatically")
    gui.set_defaults(func=cmd_gui)

    memory = sub.add_parser("memory", help="manage memories")
    memory_sub = memory.add_subparsers(required=True)
    mem_add = memory_sub.add_parser("add", help="add a memory")
    mem_add.add_argument("--type", required=True)
    mem_add.add_argument("--content", required=True)
    mem_add.add_argument("--scope", choices=["global", "project", "session"], default="project")
    mem_add.add_argument("--reason")
    mem_add.add_argument("--tag", action="append", default=[])
    mem_add.add_argument("--path", action="append", default=[])
    mem_add.add_argument("--importance", type=float, default=0.5)
    mem_add.add_argument("--confidence", type=float, default=0.8)
    mem_add.set_defaults(func=cmd_memory_add)

    mem_list = memory_sub.add_parser("list", help="list memories")
    mem_list.add_argument("--all", action="store_true")
    mem_list.add_argument("--json", action="store_true")
    mem_list.set_defaults(func=cmd_memory_list)

    mem_search = memory_sub.add_parser("search", help="search memories")
    mem_search.add_argument("query")
    mem_search.add_argument("--json", action="store_true")
    mem_search.set_defaults(func=cmd_memory_search)

    mem_rebuild = memory_sub.add_parser("rebuild-index", help="rebuild SQLite search index from Markdown memories")
    mem_rebuild.add_argument("--json", action="store_true")
    mem_rebuild.set_defaults(func=cmd_memory_rebuild_index)

    mem_pack = memory_sub.add_parser("pack", help="build a memory pack")
    mem_pack.add_argument("query")
    mem_pack.add_argument("--profile")
    mem_pack.add_argument("--json", action="store_true")
    mem_pack.set_defaults(func=cmd_memory_pack)

    mem_candidates = memory_sub.add_parser("candidates", help="show or store memory candidates")
    mem_candidates.add_argument("--session", default="latest")
    mem_candidates.add_argument("--store", action="store_true")
    mem_candidates.add_argument("--json", action="store_true")
    mem_candidates.set_defaults(func=cmd_memory_candidates)

    mem_links = memory_sub.add_parser("links", help="show related memories")
    mem_links.add_argument("id")
    mem_links.add_argument("--json", action="store_true")
    mem_links.set_defaults(func=cmd_memory_links)

    mem_pending = memory_sub.add_parser("pending", help="list candidate memories")
    mem_pending.add_argument("--json", action="store_true")
    mem_pending.set_defaults(func=cmd_memory_pending)

    mem_activate = memory_sub.add_parser("activate", help="activate a memory")
    mem_activate.add_argument("id")
    mem_activate.set_defaults(func=cmd_memory_activate)

    mem_deactivate = memory_sub.add_parser("deactivate", help="archive a memory")
    mem_deactivate.add_argument("id")
    mem_deactivate.set_defaults(func=cmd_memory_deactivate)

    mem_cleanup = memory_sub.add_parser("cleanup", help="archive due or duplicate memories")
    mem_cleanup.add_argument("--json", action="store_true")
    mem_cleanup.set_defaults(func=cmd_memory_cleanup)

    mem_export = memory_sub.add_parser("export", help="export project memories")
    mem_export.add_argument("path", nargs="?")
    mem_export.add_argument("--all", action="store_true")
    mem_export.add_argument("--json", action="store_true")
    mem_export.set_defaults(func=cmd_memory_export)

    mem_import = memory_sub.add_parser("import", help="import project memories as candidates")
    mem_import.add_argument("path", nargs="?")
    mem_import.add_argument("--activate", action="store_true")
    mem_import.add_argument("--json", action="store_true")
    mem_import.set_defaults(func=cmd_memory_import)

    lesson = sub.add_parser("lesson", help="create lessons from traced sessions")
    lesson_sub = lesson.add_subparsers(required=True)
    lesson_from = lesson_sub.add_parser("from-session", help="create a candidate lesson from a session")
    lesson_from.add_argument("session", nargs="?", default="latest")
    lesson_from.add_argument("--feedback")
    lesson_from.set_defaults(func=cmd_lesson_from_session)

    tools = sub.add_parser("tools", help="manage agent tool integrations")
    tools_sub = tools.add_subparsers(required=True)
    tools_install = tools_sub.add_parser("install", help="install tool integrations")
    tools_install.add_argument("tools", help="comma-separated tools: codex, claude, opencode, or all")
    tools_install.add_argument("--mode", choices=["full", "context", "trace"], default="full")
    tools_install.add_argument("--scope", choices=["project", "user"], default="project")
    tools_install.add_argument("--json", action="store_true")
    tools_install.set_defaults(func=cmd_tools_install)
    tools_uninstall = tools_sub.add_parser("uninstall", help="uninstall tool integrations")
    tools_uninstall.add_argument("tools", help="comma-separated tools: codex, claude, opencode, or all")
    tools_uninstall.add_argument("--scope", choices=["project", "user"], default="project")
    tools_uninstall.add_argument("--json", action="store_true")
    tools_uninstall.set_defaults(func=cmd_tools_uninstall)
    tools_repair = tools_sub.add_parser("repair", help="reinstall tool integrations")
    tools_repair.add_argument("tools", help="comma-separated tools: codex, claude, opencode, or all")
    tools_repair.add_argument("--mode", choices=["full", "context", "trace"], default="full")
    tools_repair.add_argument("--scope", choices=["project", "user"], default="project")
    tools_repair.add_argument("--json", action="store_true")
    tools_repair.set_defaults(func=cmd_tools_repair)
    tools_status = tools_sub.add_parser("status", help="show tool integration status")
    tools_status.add_argument("tools", nargs="?", help="comma-separated tools; defaults to all")
    tools_status.add_argument("--scope", choices=["project", "user"], default="project")
    tools_status.add_argument("--json", action="store_true")
    tools_status.set_defaults(func=cmd_tools_status)

    hook = sub.add_parser("hook", help=argparse.SUPPRESS)
    hook_sub = hook.add_subparsers(required=True)
    for name in ("pre-tool-use", "post-tool-use", "user-prompt-submit", "stop"):
        hook_cmd = hook_sub.add_parser(name)
        hook_cmd.set_defaults(func=cmd_hook_event, hook_event=name)

    logs = sub.add_parser("logs", help="show recent trace events")
    logs.add_argument("--session")
    logs.add_argument("--json", action="store_true")
    logs.set_defaults(func=cmd_logs)

    session = sub.add_parser("session", help="summarize a traced session")
    session.add_argument("id", nargs="?", default="latest")
    session.add_argument("--json", action="store_true")
    session.set_defaults(func=cmd_session)

    eval_parser = sub.add_parser("eval", help="run memassist retrieval and memory evaluations")
    eval_sub = eval_parser.add_subparsers(required=True)
    eval_retrieval = eval_sub.add_parser("retrieval", help="evaluate memory retrieval cases")
    eval_retrieval.add_argument("--case-file")
    eval_retrieval.add_argument("--query")
    eval_retrieval.add_argument("--expect", action="append", default=[])
    eval_retrieval.add_argument("--forbid", action="append", default=[])
    eval_retrieval.add_argument("--limit", type=int, default=5)
    eval_retrieval.add_argument("--profile")
    eval_retrieval.add_argument("--json", action="store_true")
    eval_retrieval.set_defaults(func=cmd_eval_retrieval)
    eval_memory = eval_sub.add_parser("memory", help="evaluate memory quality cases")
    eval_memory.add_argument("--case-file")
    eval_memory.add_argument("--query")
    eval_memory.add_argument("--expect", action="append", default=[])
    eval_memory.add_argument("--forbid", action="append", default=[])
    eval_memory.add_argument("--limit", type=int, default=10)
    eval_memory.add_argument("--json", action="store_true")
    eval_memory.set_defaults(func=cmd_eval_memory)
    eval_rag = eval_sub.add_parser("rag", help="evaluate section-aware RAG memory packs")
    eval_rag.add_argument("--case-file")
    eval_rag.add_argument("--query")
    eval_rag.add_argument("--expect", action="append", default=[])
    eval_rag.add_argument("--forbid", action="append", default=[])
    eval_rag.add_argument("--profile")
    eval_rag.add_argument("--json", action="store_true")
    eval_rag.set_defaults(func=cmd_eval_rag)
    eval_compare = eval_sub.add_parser("compare", help="compare retrieval eval results across embedding profiles")
    eval_compare.add_argument("--case-file", required=True)
    eval_compare.add_argument("--profiles", required=True, help="comma-separated embedding profile ids")
    eval_compare.add_argument("--json", action="store_true")
    eval_compare.set_defaults(func=cmd_eval_compare)

    embedding = sub.add_parser("embedding", help="manage local embedding profiles and caches")
    embedding_sub = embedding.add_subparsers(required=True)
    emb_profiles = embedding_sub.add_parser("profiles", help="list embedding profiles")
    emb_profiles.add_argument("--json", action="store_true")
    emb_profiles.set_defaults(func=cmd_embedding_profiles)
    emb_activate = embedding_sub.add_parser("activate", help="activate an embedding profile")
    emb_activate.add_argument("profile")
    emb_activate.add_argument("--json", action="store_true")
    emb_activate.set_defaults(func=cmd_embedding_activate)
    emb_build = embedding_sub.add_parser("build", help="build embedding cache for a profile")
    emb_build.add_argument("--profile")
    emb_build.add_argument("--json", action="store_true")
    emb_build.set_defaults(func=cmd_embedding_build)
    emb_install = embedding_sub.add_parser("install", help="install the selected embedding model locally")
    emb_install.add_argument("--profile")
    emb_install.add_argument("--force", action="store_true")
    emb_install.add_argument("--json", action="store_true")
    emb_install.set_defaults(func=cmd_embedding_install)
    emb_cleanup = embedding_sub.add_parser("cleanup", help="remove derived embedding cache rows")
    emb_cleanup.add_argument("--profile")
    emb_cleanup.add_argument("--json", action="store_true")
    emb_cleanup.set_defaults(func=cmd_embedding_cleanup)

    daemon = sub.add_parser("daemon", help="run maintenance tasks")
    daemon_sub = daemon.add_subparsers(required=True)
    daemon_once = daemon_sub.add_parser("once", help="process latest session once")
    daemon_once.add_argument("--session", default="latest")
    daemon_once.add_argument("--batch-limit", type=int)
    daemon_once.add_argument("--quiet", action="store_true")
    daemon_once.add_argument("--json", action="store_true")
    daemon_once.set_defaults(func=cmd_daemon_once)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    project = detect_project_for_init()
    mem_dir = project_memassist_home(project.root)
    mem_dir.mkdir(exist_ok=True)
    ensure_memory_artifact_dirs(mem_dir)
    ensure_source_ledger(mem_dir)
    ensure_embedding_profiles(mem_dir)
    ignore_path = mem_dir / "ignore"
    if not ignore_path.exists():
        ignore_path.write_text("# Add paths memassist should not record.\n", encoding="utf-8")
    with Store(mem_dir / "memassist.db") as store:
        store.upsert_project(project)
    print(f"Initialized memassist for {project.id}")
    if not args.skip_embedding_install and not _env_truthy("MEMASSIST_INIT_SKIP_EMBEDDING_INSTALL"):
        if _install_active_embedding_for_init(mem_dir, force=args.force_embedding_install) != 0:
            return 1
    tools = _tools_or_error(args.tools)
    if tools is None:
        return 2
    if tools:
        results = install_tools(project, tools=tools, mode=args.mode, scope="project")
        _print_tool_results(results)
        print("Review each tool's trust or permission prompt before relying on trace capture.")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        store.upsert_project(project)
        memories = store.list_memories(project_id=project.id, include_global=True)
    print(f"Project: {project.id}")
    print(f"Root: {project.root}")
    print(f"Home: {memassist_home()}")
    print(f"Database: {db_path()}")
    print(f"Memories visible here: {len(memories)}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    project = detect_project()
    report = run_doctor(project)
    if args.json:
        _print_json(report.as_dict())
    else:
        print("PASS" if report.passed else "FAIL")
        print(f"Project: {report.project_id}")
        print(f"Root: {report.project_root}")
        print(f"Home: {report.memassist_home}")
        print(f"Database: {report.database}")
        for check in report.checks:
            print(f"{check.status.upper()} {check.name}: {check.detail}")
    return 0 if report.passed else 1


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import run_gui

    return run_gui(host=args.host, port=args.port, open_browser=not args.no_open)


def cmd_memory_add(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        store.upsert_project(project)
        project_id = project.id if args.scope in {"project", "session"} else None
        memory_id = store.add_memory(
            scope_type=args.scope,
            project_id=project_id,
            type=args.type,
            content=args.content,
            reason=args.reason,
            tags=args.tag,
            paths=args.path,
            importance=args.importance,
            confidence=args.confidence,
        )
        store.link_related_memories(memory_id, project_id=project_id)
    print(memory_id)
    return 0


def cmd_memory_list(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        memories = store.list_memories(
            project_id=project.id,
            include_global=True,
            status=None if args.all else "active",
        )
    if args.json:
        _print_json([memory.as_dict() for memory in memories])
    else:
        _print_memories(memories)
    return 0


def cmd_memory_search(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        memories = store.search_memories(args.query, project_id=project.id)
    if args.json:
        _print_json([memory.as_dict() for memory in memories])
    else:
        _print_memories(memories)
    return 0


def cmd_memory_rebuild_index(args: argparse.Namespace) -> int:
    with _store() as store:
        indexed = store.rebuild_memory_index_from_artifacts()
    if args.json:
        _print_json({"indexed": indexed, "count": len(indexed)})
    else:
        print(f"Indexed {len(indexed)} memories from Markdown.")
    return 0


def cmd_memory_pack(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        pack = build_memory_pack(store, query=args.query, project_id=project.id, embedding_profile_id=args.profile)
    if args.json:
        _print_json(pack.as_dict())
    else:
        print(render_prompt_context(pack))
    return 0


def cmd_memory_candidates(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        session_id = _resolve_session_id(store, args.session, project.id)
        if not session_id:
            print("No traced session found.")
            return 1
        events = store.trace_events(session_id)
        if args.store:
            ids = store_candidates(events, store.add_memory, project_id=project.id)
            if args.json:
                _print_json({"session_id": session_id, "stored": ids})
            else:
                for memory_id in ids:
                    print(memory_id)
            return 0
        candidates = extract_candidates(events)
    if args.json:
        _print_json([candidate.as_dict() for candidate in candidates])
    else:
        for candidate in candidates:
            print(f"[{candidate.type}] {candidate.content}")
    return 0


def cmd_memory_links(args: argparse.Namespace) -> int:
    with _store() as store:
        links = store.memory_links(args.id)
    if args.json:
        _print_json(links)
    else:
        for link in links:
            print(
                f"{link['relation']} {link['target_id']} "
                f"strength={link['strength']:.2f}: {link['target_content']}"
            )
    return 0


def cmd_memory_pending(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        memories = store.list_memories(project_id=project.id, include_global=True, status="candidate")
    if args.json:
        _print_json([memory.as_dict() for memory in memories])
    else:
        _print_memories(memories)
    return 0


def cmd_memory_activate(args: argparse.Namespace) -> int:
    with _store() as store:
        memory = store.get_memory(args.id)
        if not memory:
            print(f"Memory not found: {args.id}")
            return 1
        store.update_status(args.id, "active")
    print(f"activated {args.id}")
    return 0


def cmd_memory_deactivate(args: argparse.Namespace) -> int:
    with _store() as store:
        store.update_status(args.id, "archived")
    print(f"archived {args.id}")
    return 0


def cmd_memory_cleanup(args: argparse.Namespace) -> int:
    with _store() as store:
        result = cleanup_memories(store)
    if args.json:
        _print_json(result.as_dict())
    else:
        for memory_id in result.archived:
            print(f"archived {memory_id}")
        for memory_id in result.duplicates:
            print(f"duplicate archived {memory_id}")
    return 0


def cmd_memory_export(args: argparse.Namespace) -> int:
    project = detect_project()
    output_path = Path(args.path) if args.path else project.root / ".memassist" / "memories.json"
    with _store() as store:
        count = export_memories(store, project_id=project.id, path=output_path, include_all=args.all)
    if args.json:
        _print_json({"path": str(output_path), "exported": count})
    else:
        print(f"exported {count} memories: {output_path}")
    return 0


def cmd_memory_import(args: argparse.Namespace) -> int:
    project = detect_project()
    input_path = Path(args.path) if args.path else project.root / ".memassist" / "memories.json"
    if not input_path.exists():
        print(f"Memory export not found: {input_path}")
        return 1
    with _store() as store:
        result = import_memories(store, project_id=project.id, path=input_path, activate=args.activate)
    if args.json:
        _print_json(result.as_dict())
    else:
        print(f"imported {len(result.imported)} memories, skipped {result.skipped}")
        for memory_id in result.imported:
            print(memory_id)
    return 0


def cmd_lesson_from_session(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        session_id = _resolve_session_id(store, args.session, project.id)
        if not session_id:
            print("No traced session found.")
            return 1
        events = store.trace_events(session_id)
        content = lesson_from_session(events, args.feedback)
        memory_id = store.add_memory(
            scope_type="project",
            project_id=project.id,
            session_id=session_id,
            type="lesson",
            content=content,
            reason="Created from traced session feedback.",
            tags=["lesson", "session"],
            status="candidate",
            importance=0.8,
            confidence=0.6,
            source_kind="lesson_from_session",
            source_ref=session_id,
        )
    print(memory_id)
    return 0


def cmd_tools_install(args: argparse.Namespace) -> int:
    project = detect_project()
    tools = _tools_or_error(args.tools)
    if tools is None:
        return 2
    results = install_tools(project, tools=tools, mode=args.mode, scope=args.scope)
    _print_tool_results(results, as_json=args.json)
    return 0


def cmd_tools_uninstall(args: argparse.Namespace) -> int:
    project = detect_project()
    tools = _tools_or_error(args.tools)
    if tools is None:
        return 2
    results = uninstall_tools(project, tools=tools, scope=args.scope)
    _print_tool_results(results, as_json=args.json)
    return 0


def cmd_tools_repair(args: argparse.Namespace) -> int:
    project = detect_project()
    tools = _tools_or_error(args.tools)
    if tools is None:
        return 2
    results = repair_tools(project, tools=tools, mode=args.mode, scope=args.scope)
    _print_tool_results(results, as_json=args.json)
    return 0


def cmd_tools_status(args: argparse.Namespace) -> int:
    project = detect_project()
    tools = _tools_or_error(args.tools)
    if tools is None:
        return 2
    statuses = status_tools(project, tools=tools, scope=args.scope)
    if args.json:
        _print_json([status.as_dict() for status in statuses])
    else:
        for status in statuses:
            events = ",".join(status.events) if status.events else "-"
            state = "installed" if status.installed else "missing"
            print(f"{status.tool}: {state} {status.path} events={events}")
    return 0


def cmd_hook_event(args: argparse.Namespace) -> int:
    if os.environ.get(INTERPRETER_ACTIVE_ENV) == "1":
        return 0
    payload = _read_json_stdin()
    project = detect_project(Path(payload.get("cwd", os.getcwd())))
    session_id = str(payload.get("sessionId") or payload.get("session_id") or "unknown")
    tool_name = str(payload.get("toolName") or payload.get("tool_name") or payload.get("tool") or "")
    tool_args = _coerce_tool_args(payload)
    with Store(project_memassist_home(project.root) / "memassist.db") as store:
        store.upsert_project(project)
        if args.hook_event == "user-prompt-submit":
            query = str(payload.get("prompt") or payload.get("message") or payload.get("content") or "")
            pack = build_memory_pack(store, query=query, project_id=project.id)
            _record_memory_injection(store, session_id=session_id, project_id=project.id, query=query, pack=pack)
            context = render_prompt_context(pack)
            context_parts = [
                part
                for part in [context]
                if part
            ]
            context = "\n\n".join(context_parts)
            if context:
                print(
                    _json_dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "UserPromptSubmit",
                                "additionalContext": context,
                            }
                        },
                        indent=None,
                    )
                )
            return 0
        if args.hook_event == "pre-tool-use":
            record_tool_event(
                store,
                session_id=session_id,
                project_id=project.id,
                event_type="pre_tool_use",
                tool_name=tool_name,
                payload=tool_args,
                tool_decision=None,
            )
            return 0
        record_tool_event(
            store,
            session_id=session_id,
            project_id=project.id,
            event_type=args.hook_event.replace("-", "_"),
            tool_name=tool_name or None,
            payload=payload,
        )
        if args.hook_event == "stop":
            if hook_mode() != "full":
                return 0
            source_event = observe_turn_end_memory_source(
                store,
                session_id=session_id,
                project_id=project.id,
                payload=payload,
            )
            if sync_ingestion_enabled_for_hook():
                # Debug/test mode: preserve the historical synchronous turn-end
                # ingestion path for deterministic local runs.
                process_pending_memory_intents(store, project=project, session_id=session_id)
                process_session_lifecycle(store, session_id=session_id, project_id=project.id)
            elif async_ingestion_enabled_for_hook() and source_event and pending_source_count(store, project_id=project.id):
                spawned = spawn_async_ingestion_worker(project, session_id=session_id)
                store.add_trace_event(
                    session_id=session_id,
                    project_id=project.id,
                    event_type="async_ingestion_requested",
                    tool_name="memassist",
                    input_json={
                        "mode": stop_ingest_mode(),
                        "spawned": spawned.as_dict(),
                    },
                )
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    with _store() as store:
        events = store.trace_events(args.session)
    rows = [dict(event) for event in events]
    if args.json:
        _print_json(rows)
    else:
        for event in rows:
            print(f"{event['created_at']} {event['event_type']} {event.get('tool_name') or '-'} {event.get('tool_decision') or ''}")
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        session_id = _resolve_session_id(store, args.id, project.id)
        if not session_id:
            print("No traced session found.")
            return 1
        summary = summarize_session(store.trace_events(session_id))
    if args.json:
        _print_json(summary.as_dict())
    else:
        print(f"Session: {summary.session_id}")
        print(f"Events: {summary.event_count}")
        print("Tools: " + (", ".join(summary.tools) if summary.tools else "-"))
        print("Files: " + (", ".join(summary.files) if summary.files else "-"))
        print("Commands: " + (", ".join(summary.commands) if summary.commands else "-"))
        print(f"Denied events: {summary.denied_events}")
    return 0


def cmd_eval_retrieval(args: argparse.Namespace) -> int:
    project = detect_project()
    if args.case_file:
        cases = load_cases(Path(args.case_file))
    elif args.query and args.expect:
        cases = [
            RetrievalCase(
                query=args.query,
                expect=args.expect,
                forbid=args.forbid,
                seed=[],
                limit=args.limit,
            )
        ]
    else:
        print("Provide --case-file or --query with at least one --expect.")
        return 1
    with _store() as store:
        result = evaluate_retrieval(store, project_id=project.id, cases=cases, profile_id=args.profile)
    if args.json:
        _print_json(result.as_dict())
    else:
        print("PASS" if result.passed else "FAIL")
        print(f"recall_at_k: {result.recall_at_k:.3f}")
        print(f"precision_at_k: {result.precision_at_k:.3f}")
        print(f"mrr: {result.mrr:.3f}")
        print(f"forbidden_recall_rate: {result.forbidden_recall_rate:.3f}")
    return 0 if result.passed else 1


def cmd_eval_memory(args: argparse.Namespace) -> int:
    project = detect_project()
    if args.case_file:
        cases = load_memory_quality_cases(Path(args.case_file))
    elif args.query:
        cases = [
            MemoryQualityCase(
                name=args.query,
                query=args.query,
                expect=args.expect,
                forbid=args.forbid,
                expect_statuses=sorted(MEMORY_EVAL_ACTIVE_STATUSES),
                forbid_statuses=sorted(MEMORY_EVAL_ACTIVE_STATUSES),
                seed=[],
                limit=args.limit,
            )
        ]
    else:
        print("Provide --case-file or --query.")
        return 1
    with _store() as store:
        result = evaluate_memory_quality(store, project_id=project.id, cases=cases)
    if args.json:
        _print_json(result.as_dict())
    else:
        print("PASS" if result.passed else "FAIL")
        print(f"memory_recall: {result.memory_recall:.3f}")
        print(f"memory_precision: {result.memory_precision:.3f}")
        print(f"wrong_promotion_rate: {result.wrong_promotion_rate:.3f}")
        print(f"wrong_context_promotion_rate: {result.wrong_policy_rate:.3f}")
        print(f"stale_memory_rate: {result.stale_memory_rate:.3f}")
    return 0 if result.passed else 1


def cmd_eval_rag(args: argparse.Namespace) -> int:
    project = detect_project()
    if args.case_file:
        cases = load_rag_cases(Path(args.case_file))
    elif args.query:
        cases = [
            RagCase(
                query=args.query,
                expect=[RagExpectation(term=term, section="*") for term in args.expect],
                forbid=[RagExpectation(term=term, section="*") for term in args.forbid],
                seed=[],
            )
        ]
    else:
        print("Provide --case-file or --query.")
        return 1
    with _store() as store:
        result = evaluate_rag(store, project_id=project.id, cases=cases, profile_id=args.profile)
    if args.json:
        _print_json(result.as_dict())
    else:
        print("PASS" if result.passed else "FAIL")
        print(f"section_accuracy: {result.section_accuracy:.3f}")
        print(f"context_relevance: {result.context_relevance:.3f}")
        print(f"context_gate_leak_rate: {result.policy_leak_rate:.3f}")
        print(f"verifier_recall: {result.verifier_recall:.3f}")
        print(f"pass_rate: {result.pass_rate:.3f}")
        print(f"score: {result.score:.3f}")
    return 0 if result.passed else 1


def cmd_eval_compare(args: argparse.Namespace) -> int:
    project = detect_project()
    cases = load_cases(Path(args.case_file))
    profile_ids = [profile.strip() for profile in args.profiles.split(",") if profile.strip()]
    if not profile_ids:
        print("Provide at least one profile id.")
        return 1
    rows: list[dict[str, Any]] = []
    with _store() as store:
        for profile_id in profile_ids:
            try:
                profile = get_embedding_profile(profile_id, mem_dir=store.path.parent)
            except EmbeddingProfileError as exc:
                rows.append({"profile_id": profile_id, "passed": False, "error": str(exc)})
                continue
            started = time.perf_counter()
            result = evaluate_retrieval(store, project_id=project.id, cases=cases, profile_id=profile_id)
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            cache = store.embedding_cache_summary(profile_id=profile_id)
            rows.append(
                {
                    "profile_id": profile_id,
                    "provider": profile.provider,
                    "model": profile.model,
                    "quantization": profile.quantization,
                    "dimension": profile.dimension,
                    "fingerprint": profile.fingerprint,
                    "passed": result.passed,
                    "recall_at_k": result.recall_at_k,
                    "precision_at_k": result.precision_at_k,
                    "mrr": result.mrr,
                    "forbidden_recall_rate": result.forbidden_recall_rate,
                    "elapsed_ms": elapsed_ms,
                    "cache": cache,
                    "cases": result.cases,
                }
            )
    payload = {"profiles": rows, "case_count": len(cases)}
    if args.json:
        _print_json(payload)
    else:
        for row in rows:
            status = "PASS" if row.get("passed") else "FAIL"
            print(
                f"{status} {row['profile_id']} "
                f"recall={float(row.get('recall_at_k', 0.0)):.3f} "
                f"mrr={float(row.get('mrr', 0.0)):.3f} "
                f"forbidden={float(row.get('forbidden_recall_rate', 0.0)):.3f}"
            )
    return 0 if all(row.get("passed") for row in rows) else 1


def cmd_embedding_profiles(args: argparse.Namespace) -> int:
    project = detect_project()
    mem_dir = project_memassist_home(project.root)
    try:
        config = load_embedding_profile_config(mem_dir)
    except EmbeddingProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    payload = config.as_dict()
    if args.json:
        _print_json(payload)
    else:
        print(f"active: {config.active}")
        for profile_id, profile in config.profiles.items():
            active = "*" if profile_id == config.active else " "
            print(f"{active} {profile_id}: {profile.provider} {profile.model} quantization={profile.quantization} dimension={profile.dimension}")
    return 0


def cmd_embedding_activate(args: argparse.Namespace) -> int:
    project = detect_project()
    mem_dir = project_memassist_home(project.root)
    try:
        config = activate_embedding_profile(args.profile, mem_dir=mem_dir)
    except EmbeddingProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _print_json({"active": config.active})
    else:
        print(f"active embedding profile: {config.active}")
    return 0


def cmd_embedding_build(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        try:
            profile = get_embedding_profile(args.profile, mem_dir=store.path.parent)
        except EmbeddingProfileError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        previous_allow_download = os.environ.get("MEMASSIST_EMBEDDING_ALLOW_DOWNLOAD")
        os.environ["MEMASSIST_EMBEDDING_ALLOW_DOWNLOAD"] = "1"
        try:
            result = build_memory_embeddings(store, project_id=project.id, profile=profile)
        finally:
            if previous_allow_download is None:
                os.environ.pop("MEMASSIST_EMBEDDING_ALLOW_DOWNLOAD", None)
            else:
                os.environ["MEMASSIST_EMBEDDING_ALLOW_DOWNLOAD"] = previous_allow_download
    if args.json:
        _print_json(result)
    else:
        print(f"profile: {result['profile_id']}")
        print(f"status: {result['status']}")
        print(f"built: {len(result['built'])}")
        print(f"skipped: {len(result['skipped'])}")
        print(f"chunk built: {len(result.get('chunk_built', []))}")
        print(f"chunk skipped: {len(result.get('chunk_skipped', []))}")
    return 0 if result.get("status") in {"ok", "partial"} else 1


def cmd_embedding_install(args: argparse.Namespace) -> int:
    project = detect_project()
    mem_dir = project_memassist_home(project.root)
    try:
        profile = get_embedding_profile(args.profile, mem_dir=mem_dir)
    except EmbeddingProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    result = install_embedding_model(profile, mem_dir=mem_dir, force=args.force)
    if args.json:
        _print_json(result.as_dict())
    else:
        print(f"profile: {result.profile_id}")
        print(f"status: {result.status}")
        if result.path:
            print(f"path: {result.path}")
        if result.detail:
            print(f"detail: {result.detail}")
    return 0 if result.status in {"ok", "disabled"} else 1


def cmd_embedding_cleanup(args: argparse.Namespace) -> int:
    with _store() as store:
        profile_id = args.profile
        removed = store.cleanup_embedding_cache(profile_id=profile_id)
    payload = {"profile_id": profile_id, "removed": removed}
    if args.json:
        _print_json(payload)
    else:
        target = profile_id or "all profiles"
        print(f"removed {removed} embedding cache rows for {target}")
    return 0


def _install_active_embedding_for_init(mem_dir: Path, *, force: bool) -> int:
    try:
        profile = get_embedding_profile(None, mem_dir=mem_dir)
    except EmbeddingProfileError as exc:
        print(f"Embedding model: skipped ({exc})")
        return 1
    result = install_embedding_model(profile, mem_dir=mem_dir, force=force)
    if result.status == "ok":
        location = f" at {result.path}" if result.path else ""
        print(f"Embedding model: installed{location}")
        return 0
    if result.status == "disabled":
        print("Embedding model: disabled")
        return 0
    print(f"Embedding model: {result.status}" + (f" ({result.detail})" if result.detail else ""))
    return 1


def _env_truthy(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.lower() in {"1", "true", "yes", "on"}


def cmd_daemon_once(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        store.upsert_project(project)
        process_all_sessions = str(args.session).lower() in {"all", "*", "project"}
        session_id = None if process_all_sessions else _resolve_session_id(store, args.session, project.id)
        stored: list[str] = []
        lifecycle_result = None
        ingestion_result = None
        if session_id or process_all_sessions:
            ingestion_result = run_async_ingestion_once(
                store,
                project=project,
                session_id=session_id,
                batch_limit=args.batch_limit or async_batch_limit(),
            )
            lifecycle_result = ingestion_result.lifecycle
            stored = [
                str(decision["memory_id"])
                for decision in ingestion_result.decisions
                if decision.get("memory_id")
            ]
        cleanup_result = lifecycle_result.cleanup if lifecycle_result else cleanup_memories(store)
    output_session_id = "all" if process_all_sessions else session_id
    payload = {
        "session_id": output_session_id,
        "stored_candidates": stored,
        "ingestion": ingestion_result.as_dict() if ingestion_result else None,
        "lifecycle": lifecycle_result.as_dict() if lifecycle_result else None,
        "cleanup": cleanup_result.as_dict(),
    }
    if args.json:
        _print_json(payload)
    elif not args.quiet:
        print(f"session: {session_id or '-'}")
        print(f"stored_candidates: {len(stored)}")
        if ingestion_result:
            print(f"ingestion: {ingestion_result.status} processed={ingestion_result.processed}")
        print(f"archived: {len(cleanup_result.archived)}")
    if ingestion_result and ingestion_result.status == "failed":
        return 1
    return 0


def _read_json_stdin() -> dict[str, Any]:
    # Hook payloads from Claude Code / Codex are always UTF-8 JSON. Read raw bytes and
    # decode as UTF-8 ourselves so a Windows locale codec (e.g. cp949) does not corrupt
    # non-ASCII prompts into lone surrogates. Fall back to text mode when stdin has no
    # binary buffer (e.g. a StringIO stand-in in tests).
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        text = buffer.read().decode("utf-8", errors="replace").strip()
    else:
        text = sys.stdin.read().strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    return value if isinstance(value, dict) else {"value": value}


def _record_memory_injection(
    store: Store,
    *,
    session_id: str,
    project_id: str | None,
    query: str,
    pack: Any,
) -> None:
    sections = {
        "context": [memory.id for memory in pack.context],
        "verifier": [memory.id for memory in pack.verifier],
    }
    memory_ids = list(dict.fromkeys(memory_id for ids in sections.values() for memory_id in ids))
    if not memory_ids:
        return
    store.add_trace_event(
        session_id=session_id,
        project_id=project_id,
        event_type="memory_injected",
        tool_name="memassist",
        input_json={"query": query, "memory_ids": memory_ids, "sections": sections},
        files=[],
    )


def _resolve_session_id(store: Store, requested: str, project_id: str | None) -> str | None:
    if requested == "latest":
        return store.latest_session_id(project_id=project_id)
    return requested


def _coerce_tool_args(payload: dict[str, Any]) -> dict[str, Any]:
    raw_args = (
        payload.get("toolArgs")
        or payload.get("tool_args")
        or payload.get("toolInput")
        or payload.get("tool_input")
        or payload.get("args")
        or payload.get("arguments")
        or {}
    )
    if isinstance(raw_args, str):
        try:
            decoded = json.loads(raw_args)
            return decoded if isinstance(decoded, dict) else {"value": decoded}
        except json.JSONDecodeError:
            return {"command": raw_args}
    if isinstance(raw_args, dict):
        return raw_args
    return {}


def _print_json(value: Any) -> None:
    print(_json_dumps(value))


def _json_dumps(value: Any, *, indent: int | None = 2) -> str:
    return json.dumps(value, indent=indent, ensure_ascii=False)


def _print_memories(memories: list[Any]) -> None:
    for memory in memories:
        tags = ",".join(memory.tags)
        print(f"{memory.id} [{memory.scope_type}/{memory.type}/{memory.status}] {memory.content} ({tags})")


def _tools_or_error(raw: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    try:
        return normalize_tools(raw)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def _print_tool_results(results: list[Any], *, as_json: bool = False) -> None:
    if as_json:
        _print_json([result.as_dict() for result in results])
        return
    for result in results:
        state = "installed" if result.installed else "removed"
        print(f"{result.tool}: {state} {result.path}")


def _run_lifecycle_cleanup(store: Store, *, session_id: str | None, project_id: str | None) -> None:
    """Run non-heuristic lifecycle operations after judge ingestion.

    cleanup_memories handles expiry-based archival and exact duplicate cleanup.
    Heuristic candidate generation was removed; candidates come from the judge path.
    """
    cleanup_memories(store)


class _store:
    def __enter__(self) -> Store:
        self.store = Store()
        return self.store

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
