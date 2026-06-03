from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .hooks import codex_hooks_status, install_codex_hooks, uninstall_codex_hooks
from .paths import db_path, memassist_home
from .policy import PolicyEngine, default_policy_yaml, load_policy
from .project import detect_project
from .retrieval import build_memory_pack, render_prompt_context
from .storage import Store
from .trace import record_tool_event


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memassist")
    sub = parser.add_subparsers(required=True)

    init = sub.add_parser("init", help="initialize .memassist in this project")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="show project and storage status")
    status.set_defaults(func=cmd_status)

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
    mem_add.add_argument("--enforcement", choices=["none", "warn", "require_approval", "block"], default="none")
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

    policy = sub.add_parser("policy", help="policy utilities")
    policy_sub = policy.add_subparsers(required=True)
    check = policy_sub.add_parser("check", help="check a tool call")
    check.add_argument("--tool", required=True)
    check.add_argument("--command")
    check.add_argument("--path")
    check.set_defaults(func=cmd_policy_check)

    hooks = sub.add_parser("hooks", help="manage agent hooks")
    hooks_sub = hooks.add_subparsers(required=True)
    hook_install = hooks_sub.add_parser("install", help="install hooks")
    hook_install.add_argument("agent", choices=["codex"])
    hook_install.add_argument("--scope", choices=["project", "user"], default="project")
    hook_install.set_defaults(func=cmd_hooks_install)
    hook_uninstall = hooks_sub.add_parser("uninstall", help="uninstall hooks")
    hook_uninstall.add_argument("agent", choices=["codex"])
    hook_uninstall.add_argument("--scope", choices=["project", "user"], default="project")
    hook_uninstall.set_defaults(func=cmd_hooks_uninstall)
    hook_status = hooks_sub.add_parser("status", help="show hook status")
    hook_status.add_argument("agent", choices=["codex"])
    hook_status.add_argument("--scope", choices=["project", "user"], default="project")
    hook_status.set_defaults(func=cmd_hooks_status)

    hook = sub.add_parser("hook", help=argparse.SUPPRESS)
    hook_sub = hook.add_subparsers(required=True)
    for name in ("pre-tool-use", "post-tool-use", "user-prompt-submit", "stop"):
        hook_cmd = hook_sub.add_parser(name)
        hook_cmd.set_defaults(func=cmd_hook_event, hook_event=name)

    logs = sub.add_parser("logs", help="show recent trace events")
    logs.add_argument("--session")
    logs.add_argument("--json", action="store_true")
    logs.set_defaults(func=cmd_logs)

    return parser


def cmd_init(_args: argparse.Namespace) -> int:
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
        print(json.dumps([memory.as_dict() for memory in memories], indent=2))
    else:
        _print_memories(memories)
    return 0


def cmd_memory_search(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        memories = store.search_memories(args.query, project_id=project.id)
    if args.json:
        print(json.dumps([memory.as_dict() for memory in memories], indent=2))
    else:
        _print_memories(memories)
    return 0


def cmd_memory_pack(args: argparse.Namespace) -> int:
    project = detect_project()
    with _store() as store:
        pack = build_memory_pack(store, query=args.query, project_id=project.id)
    if args.json:
        print(json.dumps(pack.as_dict(), indent=2))
    else:
        print(render_prompt_context(pack))
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


def cmd_policy_check(args: argparse.Namespace) -> int:
    project = detect_project()
    engine = PolicyEngine(load_policy(project.root))
    payload: dict[str, Any] = {}
    if args.command:
        payload["command"] = args.command
    if args.path:
        payload["path"] = args.path
    decision = engine.check_pre_tool(tool=args.tool, args=payload)
    print(json.dumps(decision.as_dict(), indent=2))
    return 1 if decision.action == "deny" else 0


def cmd_hooks_install(args: argparse.Namespace) -> int:
    project = detect_project()
    if args.agent == "codex":
        path = install_codex_hooks(scope=args.scope, project_root=project.root)
        print(f"installed codex hooks ({args.scope}): {path}")
        if args.scope == "project":
            print("Note: Codex loads project hooks only for trusted project .codex layers.")
    return 0


def cmd_hooks_uninstall(args: argparse.Namespace) -> int:
    project = detect_project()
    if args.agent == "codex":
        path = uninstall_codex_hooks(scope=args.scope, project_root=project.root)
        print(f"uninstalled codex hooks ({args.scope}): {path}")
    return 0


def cmd_hooks_status(args: argparse.Namespace) -> int:
    project = detect_project()
    if args.agent == "codex":
        print(json.dumps(codex_hooks_status(scope=args.scope, project_root=project.root), indent=2))
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
            pack = build_memory_pack(store, query=query, project_id=project.id)
            context = render_prompt_context(pack)
            if context:
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "UserPromptSubmit",
                                "additionalContext": context,
                            }
                        }
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
                print(json.dumps(output))
            return 0
        record_tool_event(
            store,
            session_id=session_id,
            project_id=project.id,
            event_type=args.hook_event.replace("-", "_"),
            tool_name=tool_name or None,
            payload=payload,
        )
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    with _store() as store:
        events = store.trace_events(args.session)
    rows = [dict(event) for event in events]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for event in rows:
            print(f"{event['created_at']} {event['event_type']} {event.get('tool_name') or '-'} {event.get('policy_decision') or ''}")
    return 0


def _read_json_stdin() -> dict[str, Any]:
    text = sys.stdin.read().strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    return value if isinstance(value, dict) else {"value": value}


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
    if action == "require_approval":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{reason}; explicit user approval required before retrying.",
            }
        }
    if action == "warn":
        return {"systemMessage": f"memassist warning: {reason}"}
    return {}


def _print_memories(memories: list[Any]) -> None:
    for memory in memories:
        tags = ",".join(memory.tags)
        print(f"{memory.id} [{memory.scope_type}/{memory.type}/{memory.status}] {memory.content} ({tags})")


class _store:
    def __enter__(self) -> Store:
        self.store = Store()
        return self.store

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
