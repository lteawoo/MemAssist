from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .directives import handle_direct_user_instruction
from .doctor import run_doctor
from .eval_runner import run_eval
from .extraction import extract_candidates, store_candidates
from .integrations import install_tools, normalize_tools, repair_tools, status_tools, uninstall_tools
from .lesson import lesson_from_session
from .lifecycle import cleanup_memories, process_session_lifecycle
from .memory_eval import (
    ACTIVE_STATUSES as MEMORY_EVAL_ACTIVE_STATUSES,
    MemoryQualityCase,
    evaluate_memory_quality,
    load_memory_quality_cases,
)
from .paths import db_path, memassist_home
from .policy import (
    PolicyEngine,
    append_protected_path,
    default_policy_yaml,
    load_policy,
    remove_protected_path,
    remove_sensitive_path,
)
from .project import detect_project
from .rag_eval import RagCase, RagExpectation, evaluate_rag, load_rag_cases
from .retrieval import build_memory_pack, render_prompt_context
from .retrieval_eval import RetrievalCase, evaluate_retrieval, load_cases
from .session import summarize_session
from .storage import Store
from .sync import export_memories, import_memories
from .trace import record_tool_event
from .verifier import verify_session


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memassist")
    sub = parser.add_subparsers(required=True)

    init = sub.add_parser("init", help="initialize .memassist in this project")
    init.add_argument("--tools", help="also install tool integrations: codex, claude, opencode, or all")
    init.add_argument("--mode", choices=["full", "context", "trace", "guard"], default="full")
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
    mem_add.add_argument("--enforcement", choices=["none", "warn", "block"], default="none")
    mem_add.set_defaults(func=cmd_memory_add)

    mem_list = memory_sub.add_parser("list", help="list memories")
    mem_list.add_argument("--all", action="store_true")
    mem_list.add_argument("--json", action="store_true")
    mem_list.set_defaults(func=cmd_memory_list)

    mem_search = memory_sub.add_parser("search", help="search memories")
    mem_search.add_argument("query")
    mem_search.add_argument("--json", action="store_true")
    mem_search.set_defaults(func=cmd_memory_search)

    mem_pack = memory_sub.add_parser("pack", help="build a memory pack")
    mem_pack.add_argument("query")
    mem_pack.add_argument("--json", action="store_true")
    mem_pack.set_defaults(func=cmd_memory_pack)

    mem_candidates = memory_sub.add_parser("candidates", help="show or store draft memory candidates")
    mem_candidates.add_argument("--session", default="latest")
    mem_candidates.add_argument("--store", action="store_true")
    mem_candidates.add_argument("--json", action="store_true")
    mem_candidates.set_defaults(func=cmd_memory_candidates)

    mem_disable = memory_sub.add_parser("disable", help="disable a memory")
    mem_disable.add_argument("id")
    mem_disable.set_defaults(func=cmd_memory_disable)

    mem_pin = memory_sub.add_parser("pin", help="pin a memory")
    mem_pin.add_argument("id")
    mem_pin.set_defaults(func=cmd_memory_pin)

    mem_supersede = memory_sub.add_parser("supersede", help="mark one memory superseded by another")
    mem_supersede.add_argument("old_id")
    mem_supersede.add_argument("new_id")
    mem_supersede.set_defaults(func=cmd_memory_supersede)
    mem_rollback = memory_sub.add_parser("rollback", help="reject a memory and remove its policy path if present")
    mem_rollback.add_argument("id")
    mem_rollback.add_argument("--protected-path")
    mem_rollback.set_defaults(func=cmd_memory_rollback)
    mem_links = memory_sub.add_parser("links", help="show related memories")
    mem_links.add_argument("id")
    mem_links.add_argument("--json", action="store_true")
    mem_links.set_defaults(func=cmd_memory_links)

    mem_drafts = memory_sub.add_parser("drafts", help="list draft memories")
    mem_drafts.add_argument("--json", action="store_true")
    mem_drafts.set_defaults(func=cmd_memory_drafts)

    mem_activate = memory_sub.add_parser("activate", help="activate a memory")
    mem_activate.add_argument("id")
    mem_activate.set_defaults(func=cmd_memory_activate)

    mem_deactivate = memory_sub.add_parser("deactivate", help="disable a memory")
    mem_deactivate.add_argument("id")
    mem_deactivate.set_defaults(func=cmd_memory_deactivate)

    mem_cleanup = memory_sub.add_parser("cleanup", help="expire stale memories")
    mem_cleanup.add_argument("--json", action="store_true")
    mem_cleanup.set_defaults(func=cmd_memory_cleanup)

    mem_export = memory_sub.add_parser("export", help="export project memories")
    mem_export.add_argument("path", nargs="?")
    mem_export.add_argument("--all", action="store_true")
    mem_export.add_argument("--json", action="store_true")
    mem_export.set_defaults(func=cmd_memory_export)

    mem_import = memory_sub.add_parser("import", help="import project memories as drafts")
    mem_import.add_argument("path", nargs="?")
    mem_import.add_argument("--activate", action="store_true")
    mem_import.add_argument("--json", action="store_true")
    mem_import.set_defaults(func=cmd_memory_import)

    policy = sub.add_parser("policy", help="policy utilities")
    policy_sub = policy.add_subparsers(required=True)
    check = policy_sub.add_parser("check", help="check a tool call")
    check.add_argument("--tool", required=True)
    check.add_argument("--command")
    check.add_argument("--path")
    check.set_defaults(func=cmd_policy_check)
    promote = policy_sub.add_parser("promote", help="promote a memory into project policy")
    promote.add_argument("memory_id")
    promote.add_argument("--protected-path", required=True)
    promote.set_defaults(func=cmd_policy_promote)

    lesson = sub.add_parser("lesson", help="create lessons from traced sessions")
    lesson_sub = lesson.add_subparsers(required=True)
    lesson_from = lesson_sub.add_parser("from-session", help="create a draft lesson from a session")
    lesson_from.add_argument("session", nargs="?", default="latest")
    lesson_from.add_argument("--feedback")
    lesson_from.set_defaults(func=cmd_lesson_from_session)

    tools = sub.add_parser("tools", help="manage agent tool integrations")
    tools_sub = tools.add_subparsers(required=True)
    tools_install = tools_sub.add_parser("install", help="install tool integrations")
    tools_install.add_argument("tools", help="comma-separated tools: codex, claude, opencode, or all")
    tools_install.add_argument("--mode", choices=["full", "context", "trace", "guard"], default="full")
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
    tools_repair.add_argument("--mode", choices=["full", "context", "trace", "guard"], default="full")
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

    verify = sub.add_parser("verify", help="verify a traced session against memory and policy")
    verify.add_argument("--session", default="latest")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify)

    eval_parser = sub.add_parser("eval", help="run memassist session evaluation")
    eval_sub = eval_parser.add_subparsers(required=True)
    eval_run = eval_sub.add_parser("run", help="run verification and extraction checks")
    eval_run.add_argument("--session", default="latest")
    eval_run.add_argument("--json", action="store_true")
    eval_run.set_defaults(func=cmd_eval_run)
    eval_retrieval = eval_sub.add_parser("retrieval", help="evaluate memory retrieval cases")
    eval_retrieval.add_argument("--case-file")
    eval_retrieval.add_argument("--query")
    eval_retrieval.add_argument("--expect", action="append", default=[])
    eval_retrieval.add_argument("--forbid", action="append", default=[])
    eval_retrieval.add_argument("--limit", type=int, default=5)
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
    eval_rag.add_argument("--json", action="store_true")
    eval_rag.set_defaults(func=cmd_eval_rag)

    daemon = sub.add_parser("daemon", help="run maintenance tasks")
    daemon_sub = daemon.add_subparsers(required=True)
    daemon_once = daemon_sub.add_parser("once", help="process latest session once")
    daemon_once.add_argument("--session", default="latest")
    daemon_once.add_argument("--json", action="store_true")
    daemon_once.set_defaults(func=cmd_daemon_once)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    project = detect_project()
    mem_dir = project.root / ".memassist"
    mem_dir.mkdir(exist_ok=True)
    policy_path = mem_dir / "policy.yaml"
    if not policy_path.exists():
        policy_path.write_text(default_policy_yaml(), encoding="utf-8")
    ignore_path = mem_dir / "ignore"
    if not ignore_path.exists():
        ignore_path.write_text("# Add paths memassist should not record.\n", encoding="utf-8")
    with _store() as store:
        store.upsert_project(project)
    print(f"Initialized memassist for {project.id}")
    print(f"Project config: {policy_path}")
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
            enforcement=args.enforcement,
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


def cmd_memory_pack(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        pack = build_memory_pack(store, query=args.query, project_id=project.id)
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


def cmd_memory_disable(args: argparse.Namespace) -> int:
    with _store() as store:
        store.update_status(args.id, "disabled")
    print(f"disabled {args.id}")
    return 0


def cmd_memory_pin(args: argparse.Namespace) -> int:
    with _store() as store:
        store.update_status(args.id, "pinned")
    print(f"pinned {args.id}")
    return 0


def cmd_memory_supersede(args: argparse.Namespace) -> int:
    with _store() as store:
        store.supersede(args.old_id, args.new_id)
    print(f"superseded {args.old_id} by {args.new_id}")
    return 0


def cmd_memory_rollback(args: argparse.Namespace) -> int:
    project = detect_project()
    removed = False
    with _store() as store:
        memory = store.get_memory(args.id)
        if not memory:
            print(f"Memory not found: {args.id}")
            return 1
        path = args.protected_path or (memory.paths[0] if memory.paths else None)
        if path:
            if memory.enforcement == "warn" or memory.status == "warn_policy":
                removed = remove_sensitive_path(project.root, path)
            else:
                removed = remove_protected_path(project.root, path)
        store.update_status(args.id, "rejected")
    print(f"rolled back {args.id}")
    if args.protected_path or removed:
        print(("removed" if removed else "not present") + f" policy path: {args.protected_path or path}")
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


def cmd_memory_drafts(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        memories = store.list_memories(project_id=project.id, include_global=True, status="draft")
    if args.json:
        _print_json([memory.as_dict() for memory in memories])
    else:
        _print_memories(memories)
    return 0


def cmd_memory_activate(args: argparse.Namespace) -> int:
    with _store() as store:
        store.update_status(args.id, "active")
    print(f"activated {args.id}")
    return 0


def cmd_memory_deactivate(args: argparse.Namespace) -> int:
    with _store() as store:
        store.update_status(args.id, "disabled")
    print(f"deactivated {args.id}")
    return 0


def cmd_memory_cleanup(args: argparse.Namespace) -> int:
    with _store() as store:
        result = cleanup_memories(store)
    if args.json:
        _print_json(result.as_dict())
    else:
        for memory_id in result.expired:
            print(f"expired {memory_id}")
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


def cmd_policy_check(args: argparse.Namespace) -> int:
    project = detect_project()
    engine = PolicyEngine(load_policy(project.root))
    payload: dict[str, Any] = {}
    if args.command:
        payload["command"] = args.command
    if args.path:
        payload["path"] = args.path
    decision = engine.check_pre_tool(tool=args.tool, args=payload)
    _print_json(decision.as_dict())
    return 1 if decision.action in {"deny", "block"} else 0


def cmd_policy_promote(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        memory = store.get_memory(args.memory_id)
        if not memory:
            print(f"Memory not found: {args.memory_id}")
            return 1
        changed = append_protected_path(project.root, args.protected_path)
        paths = list(dict.fromkeys([*memory.paths, args.protected_path]))
        store.update_paths(args.memory_id, paths)
        store.update_enforcement(args.memory_id, "block")
        store.update_status(args.memory_id, "block_policy")
    print(("added" if changed else "already present") + f" protected path: {args.protected_path}")
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
            status="draft",
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
    payload = _read_json_stdin()
    project = detect_project(Path(payload.get("cwd", os.getcwd())))
    session_id = str(payload.get("sessionId") or payload.get("session_id") or "unknown")
    tool_name = str(payload.get("toolName") or payload.get("tool_name") or payload.get("tool") or "")
    tool_args = _coerce_tool_args(payload)
    with _store() as store:
        store.upsert_project(project)
        if args.hook_event == "user-prompt-submit":
            query = str(payload.get("prompt") or payload.get("message") or payload.get("content") or "")
            direct_instruction = handle_direct_user_instruction(
                store,
                project_root=project.root,
                project_id=project.id,
                session_id=session_id,
                prompt=query,
            )
            pack = build_memory_pack(store, query=query, project_id=project.id)
            _record_memory_injection(store, session_id=session_id, project_id=project.id, query=query, pack=pack)
            context = render_prompt_context(pack)
            context_parts = [
                part
                for part in [direct_instruction.message, context]
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
            decision = PolicyEngine(load_policy(project.root)).check_pre_tool(
                tool=tool_name,
                args=tool_args,
            )
            record_tool_event(
                store,
                session_id=session_id,
                project_id=project.id,
                event_type="pre_tool_use",
                tool_name=tool_name,
                payload=tool_args,
                policy_decision=decision.action,
            )
            output = _codex_pre_tool_use_output(decision.action, decision.reason)
            if output:
                print(_json_dumps(output, indent=None))
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
            process_session_lifecycle(store, session_id=session_id, project_id=project.id)
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    with _store() as store:
        events = store.trace_events(args.session)
    rows = [dict(event) for event in events]
    if args.json:
        _print_json(rows)
    else:
        for event in rows:
            print(f"{event['created_at']} {event['event_type']} {event.get('tool_name') or '-'} {event.get('policy_decision') or ''}")
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
        print("Tests: " + (", ".join(summary.test_commands) if summary.test_commands else "-"))
        print(f"Denied events: {summary.denied_events}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        session_id = _resolve_session_id(store, args.session, project.id)
        if not session_id:
            print("No traced session found.")
            return 1
        memories = store.list_memories(project_id=project.id, include_global=True)
        result = verify_session(
            store.trace_events(session_id),
            memories=memories,
            policy=load_policy(project.root),
        )
    if args.json:
        _print_json(result.as_dict())
    else:
        print("PASS" if result.passed else "FAIL")
        for issue in result.issues:
            print(f"issue: {issue}")
        for warning in result.warnings:
            print(f"warning: {warning}")
    return 0 if result.passed else 1


def cmd_eval_run(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        session_id = _resolve_session_id(store, args.session, project.id)
        if not session_id:
            print("No traced session found.")
            return 1
        result = run_eval(
            store.trace_events(session_id),
            memories=store.list_memories(project_id=project.id, include_global=True),
            policy=load_policy(project.root),
        )
    if args.json:
        _print_json(result.as_dict())
    else:
        print("PASS" if result.passed else "FAIL")
        print(f"candidate_count: {result.candidate_count}")
    return 0 if result.passed else 1


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
        result = evaluate_retrieval(store, project_id=project.id, cases=cases)
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
        print(f"wrong_policy_rate: {result.wrong_policy_rate:.3f}")
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
        result = evaluate_rag(store, project_id=project.id, cases=cases)
    if args.json:
        _print_json(result.as_dict())
    else:
        print("PASS" if result.passed else "FAIL")
        print(f"section_accuracy: {result.section_accuracy:.3f}")
        print(f"context_relevance: {result.context_relevance:.3f}")
        print(f"policy_leak_rate: {result.policy_leak_rate:.3f}")
        print(f"verifier_recall: {result.verifier_recall:.3f}")
        print(f"pass_rate: {result.pass_rate:.3f}")
        print(f"score: {result.score:.3f}")
    return 0 if result.passed else 1


def cmd_daemon_once(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        store.upsert_project(project)
        session_id = _resolve_session_id(store, args.session, project.id)
        stored: list[str] = []
        eval_result = None
        lifecycle_result = None
        if session_id:
            events = store.trace_events(session_id)
            lifecycle_result = process_session_lifecycle(store, session_id=session_id, project_id=project.id)
            stored = lifecycle_result.stored
            eval_result = run_eval(
                events,
                memories=store.list_memories(project_id=project.id, include_global=True),
                policy=load_policy(project.root),
            )
        cleanup_result = lifecycle_result.cleanup if lifecycle_result else cleanup_memories(store)
    payload = {
        "session_id": session_id,
        "stored_candidates": stored,
        "lifecycle": lifecycle_result.as_dict() if lifecycle_result else None,
        "cleanup": cleanup_result.as_dict(),
        "eval": eval_result.as_dict() if eval_result else None,
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"session: {session_id or '-'}")
        print(f"stored_candidates: {len(stored)}")
        print(f"expired: {len(cleanup_result.expired)}")
        if eval_result:
            print("eval: " + ("PASS" if eval_result.passed else "FAIL"))
    return 0 if eval_result is None or eval_result.passed else 1


def _read_json_stdin() -> dict[str, Any]:
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
        "policy": [memory.id for memory in pack.policy],
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


def _codex_pre_tool_use_output(action: str, reason: str) -> dict[str, Any]:
    if action == "deny":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    if action == "block":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{reason}; blocked by autonomous memory policy.",
            }
        }
    if action == "warn":
        return {"systemMessage": f"memassist warning: {reason}"}
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


class _store:
    def __enter__(self) -> Store:
        self.store = Store()
        return self.store

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
