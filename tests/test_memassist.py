from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from memassist.cli import main
from memassist.extraction import extract_candidates
from memassist.hooks import codex_hooks_status, install_codex_hooks, uninstall_codex_hooks
from memassist.policy import PolicyEngine, default_policy_yaml, load_policy
from memassist.project import detect_project
from memassist.retrieval import analyze_query_intent, build_memory_pack
from memassist.storage import Store
from memassist.trace import extract_files


@contextmanager
def isolated_env():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "home"
        project = root / "project"
        project.mkdir()
        old_cwd = Path.cwd()
        old_home = os.environ.get("MEMASSIST_HOME")
        old_codex = os.environ.get("CODEX_HOME")
        os.environ["MEMASSIST_HOME"] = str(home)
        os.environ["CODEX_HOME"] = str(root / "codex")
        os.chdir(project)
        try:
            yield root, project, home
        finally:
            os.chdir(old_cwd)
            if old_home is None:
                os.environ.pop("MEMASSIST_HOME", None)
            else:
                os.environ["MEMASSIST_HOME"] = old_home
            if old_codex is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old_codex


class MemassistTest(unittest.TestCase):
    def test_init_creates_project_policy(self) -> None:
        with isolated_env() as (_root, project, _home):
            code = main(["init"])
            self.assertEqual(code, 0)
            self.assertTrue((project / ".memassist" / "policy.yaml").exists())
            self.assertTrue((project / ".memassist" / "ignore").exists())

    def test_init_can_install_hooks_and_doctor_reports_setup(self) -> None:
        with isolated_env() as (_root, project, _home):
            code = main(["init", "--hooks"])
            self.assertEqual(code, 0)
            self.assertTrue((project / ".codex" / "hooks.json").exists())

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["doctor", "--json"])
            self.assertEqual(code, 0)
            report = json.loads(out.getvalue())
            self.assertTrue(report["passed"])
            checks = {check["name"]: check for check in report["checks"]}
            self.assertEqual(checks["project_config"]["status"], "pass")
            self.assertEqual(checks["codex_project_hooks"]["status"], "pass")

    def test_memory_search_is_project_scoped(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            other_dir = project_dir.parent / "other"
            other_dir.mkdir()
            other_project = detect_project(other_dir)
            store = Store()
            try:
                store.upsert_project(project)
                store.upsert_project(other_project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="fact",
                    content="This project uses FastAPI.",
                    tags=["api"],
                )
                store.add_memory(
                    scope_type="project",
                    project_id=other_project.id,
                    type="fact",
                    content="Other project uses Rails.",
                    tags=["api"],
                )
                results = store.search_memories("project", project_id=project.id)
                contents = [memory.content for memory in results]
                self.assertIn("This project uses FastAPI.", contents)
                self.assertNotIn("Other project uses Rails.", contents)
            finally:
                store.close()

    def test_memory_search_uses_contextual_path_index(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                first_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Use the focused regression command.",
                    tags=[],
                    paths=[],
                )
                self.assertFalse(store.search_memories("auth session", project_id=project.id))

                store.update_paths(first_id, ["src/auth/session.py"])
                results = store.search_memories("auth session", project_id=project.id)
                self.assertTrue(any(memory.id == first_id for memory in results))

    def test_store_search_and_pack(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            store = Store()
            try:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="lesson",
                    content="Do not change refresh token policy for session timeout fixes.",
                    tags=["auth", "session"],
                    enforcement="require_approval",
                    importance=0.9,
                )
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Run npm test -- auth session after auth changes.",
                    tags=["verification", "test"],
                    importance=0.8,
                )
                results = store.search_memories("session", project_id=project.id)
                self.assertGreaterEqual(len(results), 1)
                pack = build_memory_pack(store, query="session timeout", project_id=project.id)
                self.assertTrue(pack.context)
                self.assertTrue(pack.policy)
                self.assertTrue(pack.verifier)
            finally:
                store.close()

    def test_query_intent_analyzes_codex_task_deterministically(self) -> None:
        intent = analyze_query_intent(
            "Implement auth session fix in src/auth/session.py and run pytest verification"
        )
        self.assertEqual(intent.task_type, "feature")
        self.assertIn("auth", intent.domains)
        self.assertIn("tests", intent.domains)
        self.assertIn("src/auth/session.py", intent.likely_paths)
        self.assertEqual(intent.risk_level, "high")
        self.assertTrue(intent.needs_policy)
        self.assertTrue(intent.needs_verifier)
        self.assertIn("auth", " ".join(intent.retrieval_queries))

    def test_memory_pack_uses_intent_channels_for_sections(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            store = Store()
            try:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Retrieval pack construction lives in the memassist retrieval module.",
                    tags=["retrieval"],
                    paths=["src/memassist/retrieval.py"],
                    importance=0.8,
                )
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="rule",
                    content="Require approval before changing active retrieval policy behavior.",
                    tags=["policy", "retrieval"],
                    enforcement="require_approval",
                    importance=0.9,
                )
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Run focused memassist retrieval unit tests after retrieval changes.",
                    tags=["verification", "retrieval"],
                    importance=0.7,
                )
                pack = build_memory_pack(
                    store,
                    query="Implement rank fusion policy in src/memassist/retrieval.py",
                    project_id=project.id,
                )
                self.assertEqual(pack.intent.task_type if pack.intent else None, "feature")
                self.assertTrue(any("retrieval module" in memory.content for memory in pack.context))
                self.assertTrue(any(memory.enforcement == "require_approval" for memory in pack.policy))
                self.assertTrue(any(memory.type == "workflow" for memory in pack.verifier))
            finally:
                store.close()

    def test_retrieval_eval_measures_expected_and_forbidden_memory(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Use npm test after session timeout changes.",
                    tags=["verification", "session", "timeout"],
                    status="auto_active",
                    importance=0.7,
                )
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="Do not touch refresh token policy without confirmation.",
                    tags=["auth", "token"],
                    status="pending_confirmation",
                    importance=0.9,
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(
                    [
                        "eval",
                        "retrieval",
                        "--query",
                        "session-timeout npm verification",
                        "--expect",
                        "npm test",
                        "--forbid",
                        "refresh token",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertTrue(result["passed"])
            self.assertEqual(result["recall_at_k"], 1.0)
            self.assertEqual(result["forbidden_recall_rate"], 0.0)

    def test_memory_quality_eval_measures_wrong_policy_and_stale_rates(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Use npm test after session timeout changes.",
                    tags=["verification", "session", "timeout"],
                    status="long_term",
                    importance=0.8,
                    confidence=0.9,
                )
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="Do not touch refresh token policy without confirmation.",
                    tags=["auth", "token"],
                    status="pending_confirmation",
                    importance=0.9,
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(
                    [
                        "eval",
                        "memory",
                        "--query",
                        "session-timeout npm verification",
                        "--expect",
                        "npm test",
                        "--forbid",
                        "refresh token",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertTrue(result["passed"])
            self.assertEqual(result["memory_recall"], 1.0)
            self.assertEqual(result["wrong_policy_rate"], 0.0)

    def test_rag_eval_measures_section_aware_memory_pack(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Use npm test after session timeout changes.",
                    tags=["verification", "session", "timeout"],
                    status="long_term",
                    importance=0.8,
                    confidence=0.9,
                )
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="rule",
                    content="Refresh token policy requires explicit approval before edits.",
                    tags=["refresh", "token", "policy"],
                    status="policy_active",
                    enforcement="require_approval",
                    importance=0.9,
                    confidence=0.9,
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(
                    [
                        "eval",
                        "rag",
                        "--query",
                        "change session timeout and verify",
                        "--expect",
                        "npm test",
                        "--forbid",
                        "temporary branch",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertTrue(result["passed"])
            self.assertEqual(result["section_accuracy"], 1.0)
            self.assertIn("score", result)

    def test_rag_eval_case_file_can_seed_temporary_memories(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            case_file = project_dir / "rag_seed.json"
            case_file.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "query": "change session timeout and verify",
                                "seed": [
                                    {
                                        "section": "context",
                                        "content": "Session timeout fixes should update timeout behavior only.",
                                        "tags": ["auth", "session", "timeout"],
                                    },
                                    {
                                        "section": "verifier",
                                        "content": "Run npm test -- auth session after timeout changes.",
                                        "tags": ["verification", "test", "auth"],
                                    },
                                ],
                                "expect": [
                                    {"term": "session timeout", "section": "context"},
                                    {"term": "npm test", "section": "verifier"},
                                ],
                                "forbid": [{"term": "refresh token", "section": "*"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["eval", "rag", "--case-file", str(case_file), "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertTrue(result["passed"])
            self.assertGreaterEqual(result["score"], 4.5)
            with Store() as store:
                memories = store.list_memories(project_id=detect_project().id, include_global=True)
            self.assertFalse(any(memory.source_kind == "rag_eval_seed" for memory in memories))

    def test_user_prompt_submit_records_injected_memory_trace(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Use npm test after session timeout changes.",
                    tags=["verification", "session", "timeout"],
                    status="long_term",
                )
            payload = {
                "session_id": "sess_inject",
                "cwd": str(Path.cwd()),
                "prompt": "change session timeout and verify",
            }
            stdin = StringIO(json.dumps(payload))
            with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                code = main(["hook", "user-prompt-submit"])
            self.assertEqual(code, 0)
            with Store() as store:
                events = store.trace_events("sess_inject")
            self.assertTrue(any(event["event_type"] == "memory_injected" for event in events))

    def test_memory_links_related_memories_by_tags_and_content(self) -> None:
        with isolated_env():
            main(["init"])
            first = StringIO()
            with patch("sys.stdout", first):
                main(
                    [
                        "memory",
                        "add",
                        "--type",
                        "workflow",
                        "--content",
                        "Run npm test after auth changes.",
                        "--tag",
                        "auth",
                        "--tag",
                        "verification",
                    ]
                )
            first_id = first.getvalue().strip()
            second = StringIO()
            with patch("sys.stdout", second):
                main(
                    [
                        "memory",
                        "add",
                        "--type",
                        "lesson",
                        "--content",
                        "Auth changes require npm test verification.",
                        "--tag",
                        "auth",
                    ]
                )
            second_id = second.getvalue().strip()

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["memory", "links", second_id, "--json"])
            self.assertEqual(code, 0)
            links = json.loads(out.getvalue())
            self.assertTrue(any(link["target_id"] == first_id for link in links))

    def test_cli_memory_add_and_search(self) -> None:
        with isolated_env():
            main(["init"])
            out = StringIO()
            with patch("sys.stdout", out):
                code = main(
                    [
                        "memory",
                        "add",
                        "--type",
                        "decision",
                        "--content",
                        "Use pnpm for this repository.",
                        "--tag",
                        "tooling",
                    ]
                )
            self.assertEqual(code, 0)
            memory_id = out.getvalue().strip()
            self.assertTrue(memory_id.startswith("mem_"))

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["memory", "search", "pnpm"])
            self.assertEqual(code, 0)
            self.assertIn("Use pnpm", out.getvalue())

    def test_policy_blocks_dangerous_command_and_warns_sensitive_path(self) -> None:
        with isolated_env() as (_root, project, _home):
            (project / ".memassist").mkdir()
            (project / ".memassist" / "policy.yaml").write_text(default_policy_yaml(), encoding="utf-8")
            engine = PolicyEngine(load_policy(project))
            deny = engine.check_pre_tool(tool="shell", args={"command": "rm -rf dist"})
            self.assertEqual(deny.action, "deny")
            deny_recursive = engine.check_pre_tool(tool="shell", args={"command": "rm -r dist"})
            self.assertEqual(deny_recursive.action, "deny")
            warn = engine.check_pre_tool(tool="file_write", args={"path": ".env"})
            self.assertEqual(warn.action, "warn")

    def test_policy_detects_protected_path_inside_apply_patch(self) -> None:
        with isolated_env() as (_root, project, _home):
            (project / ".memassist").mkdir()
            (project / ".memassist" / "policy.yaml").write_text(
                default_policy_yaml()
                + '\nprotected_paths:\n  - "src/auth/refresh-token-policy.ts"\n',
                encoding="utf-8",
            )
            engine = PolicyEngine(load_policy(project))
            decision = engine.check_pre_tool(
                tool="apply_patch",
                args={
                    "command": "*** Begin Patch\n*** Update File: src/auth/refresh-token-policy.ts\n@@\n-true\n+false\n*** End Patch\n"
                },
            )
            self.assertEqual(decision.action, "require_approval")

    def test_codex_hook_install_status_uninstall(self) -> None:
        with isolated_env() as (_root, project, _home):
            path = install_codex_hooks(project_root=project)
            self.assertTrue(path.exists())
            self.assertEqual(path, project / ".codex" / "hooks.json")
            status = codex_hooks_status(project_root=project)
            self.assertTrue(status["installed"])
            self.assertEqual(status["scope"], "project")
            self.assertIn("PreToolUse", status["events"])
            hooks = json.loads(path.read_text(encoding="utf-8"))
            command = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertIn("PYTHONPATH=", command)
            self.assertIn("MEMASSIST_HOME=", command)
            self.assertIn("python3 -m memassist hook pre-tool-use", command)
            uninstall_codex_hooks(project_root=project)
            status = codex_hooks_status(project_root=project)
            self.assertFalse(status["installed"])

    def test_hook_pre_tool_use_records_trace(self) -> None:
        with isolated_env():
            main(["init"])
            payload = {
                "sessionId": "sess_1",
                "cwd": str(Path.cwd()),
                "toolName": "shell",
                "toolArgs": json.dumps({"command": "echo ok"}),
            }
            stdin = StringIO(json.dumps(payload))
            stdout = StringIO()
            with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
                code = main(["hook", "pre-tool-use"])
            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(), "")

            out = StringIO()
            with patch("sys.stdout", out):
                main(["logs", "--json"])
            events = json.loads(out.getvalue())
            self.assertEqual(events[0]["session_id"], "sess_1")
            self.assertEqual(events[0]["event_type"], "pre_tool_use")

    def test_hook_pre_tool_use_denies_with_codex_schema(self) -> None:
        with isolated_env():
            main(["init"])
            payload = {
                "session_id": "sess_2",
                "cwd": str(Path.cwd()),
                "tool_name": "shell",
                "tool_input": {"command": "rm -rf dist"},
            }
            stdin = StringIO(json.dumps(payload))
            stdout = StringIO()
            with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
                code = main(["hook", "pre-tool-use"])
            self.assertEqual(code, 0)
            hook_output = json.loads(stdout.getvalue())
            specific = hook_output["hookSpecificOutput"]
            self.assertEqual(specific["hookEventName"], "PreToolUse")
            self.assertEqual(specific["permissionDecision"], "deny")
            self.assertIn("permissionDecisionReason", specific)

    def test_hook_pre_tool_use_maps_require_approval_to_deny(self) -> None:
        with isolated_env() as (_root, project, _home):
            main(["init"])
            with open(project / ".memassist" / "policy.yaml", "a", encoding="utf-8") as file:
                file.write('\nprotected_paths:\n  - "src/auth/refresh-token-policy.ts"\n')
            payload = {
                "session_id": "sess_approval",
                "cwd": str(project),
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "*** Begin Patch\n*** Update File: src/auth/refresh-token-policy.ts\n@@\n-true\n+false\n*** End Patch\n"
                },
            }
            stdin = StringIO(json.dumps(payload))
            stdout = StringIO()
            with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
                code = main(["hook", "pre-tool-use"])
            self.assertEqual(code, 0)
            specific = json.loads(stdout.getvalue())["hookSpecificOutput"]
            self.assertEqual(specific["hookEventName"], "PreToolUse")
            self.assertEqual(specific["permissionDecision"], "deny")
            self.assertIn("explicit user approval required", specific["permissionDecisionReason"])

    def test_hook_user_prompt_submit_outputs_additional_context_schema(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            store = Store()
            try:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="lesson",
                    content="Session timeout fixes must not change refresh token policy.",
                    tags=["session", "timeout", "auth"],
                    importance=0.9,
                )
            finally:
                store.close()
            payload = {
                "session_id": "sess_3",
                "cwd": str(Path.cwd()),
                "prompt": "session timeout",
            }
            stdin = StringIO(json.dumps(payload))
            stdout = StringIO()
            with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
                code = main(["hook", "user-prompt-submit"])
            self.assertEqual(code, 0)
            hook_output = json.loads(stdout.getvalue())
            specific = hook_output["hookSpecificOutput"]
            self.assertEqual(specific["hookEventName"], "UserPromptSubmit")
            self.assertIn("Session timeout", specific["additionalContext"])

    def test_trace_extracts_apply_patch_files(self) -> None:
        files = extract_files(
            {
                "command": "*** Begin Patch\n*** Update File: src/auth/refresh-token-policy.ts\n@@\n-true\n+false\n*** End Patch\n"
            }
        )
        self.assertEqual(files, ["src/auth/refresh-token-policy.ts"])

    def test_session_summary_and_verify(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            store = Store()
            try:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Run npm test after auth/session changes.",
                    tags=["verification", "test"],
                )
                store.add_trace_event(
                    session_id="sess_verify",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="apply_patch",
                    input_json={
                        "command": "*** Begin Patch\n*** Update File: src/session/session-config.ts\n*** End Patch\n"
                    },
                    files=["src/session/session-config.ts"],
                )
                out = StringIO()
                with patch("sys.stdout", out):
                    code = main(["verify", "--session", "sess_verify"])
                self.assertEqual(code, 1)
                self.assertIn("no test command", out.getvalue())

                store.add_trace_event(
                    session_id="sess_verify",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="Bash",
                    input_json={"command": "npm test"},
                )
                out = StringIO()
                with patch("sys.stdout", out):
                    code = main(["verify", "--session", "sess_verify"])
                self.assertEqual(code, 0)
                self.assertIn("PASS", out.getvalue())
            finally:
                store.close()

    def test_verify_treats_require_approval_as_policy_block(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            with open(project_dir / ".memassist" / "policy.yaml", "a", encoding="utf-8") as file:
                file.write('\nprotected_paths:\n  - "src/auth/refresh-token-policy.ts"\n')
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Use npm test after code edits.",
                    tags=["verification", "test"],
                )
                store.add_trace_event(
                    session_id="sess_require_approval",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="apply_patch",
                    input_json={"command": "*** Update File: src/auth/refresh-token-policy.ts"},
                    files=["src/auth/refresh-token-policy.ts"],
                    policy_decision="require_approval",
                )
                store.add_trace_event(
                    session_id="sess_require_approval",
                    project_id=project.id,
                    event_type="stop",
                    input_json={"last_assistant_message": "Edit did not succeed."},
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["verify", "--session", "sess_require_approval", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertTrue(result["passed"])
            self.assertEqual(result["summary"]["denied_events"], 1)
            self.assertTrue(any("denied by policy" in warning for warning in result["warnings"]))

    def test_session_json_outputs_unicode_without_ascii_escape(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_trace_event(
                    session_id="sess_unicode",
                    project_id=project.id,
                    event_type="stop",
                    input_json={"last_assistant_message": "확인 완료했습니다."},
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["session", "latest", "--json"])
            self.assertEqual(code, 0)
            self.assertIn("확인 완료했습니다.", out.getvalue())
            self.assertNotIn("\\ud655", out.getvalue())

    def test_stop_hook_runs_lifecycle_and_auto_activates_low_risk_memory(self) -> None:
        with isolated_env():
            main(["init"])
            payload = {
                "session_id": "sess_stop",
                "cwd": str(Path.cwd()),
                "hook_event_name": "Stop",
                "last_assistant_message": "npm test passed. 앞으로 이 프로젝트는 npm test로 검증하세요.",
            }
            stdin = StringIO(json.dumps(payload))
            with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                code = main(["hook", "stop"])
            self.assertEqual(code, 0)
            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["memory", "list", "--all", "--json"])
            self.assertEqual(code, 0)
            memories = json.loads(out.getvalue())
            self.assertTrue(any(memory["status"] == "auto_active" for memory in memories))

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["memory", "search", "npm", "--json"])
            self.assertEqual(code, 0)
            results = json.loads(out.getvalue())
            self.assertTrue(any("npm test" in memory["content"] for memory in results))

    def test_lifecycle_keeps_risky_lessons_pending_confirmation(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_trace_event(
                    session_id="sess_risky",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="apply_patch",
                    input_json={"command": "*** Update File: src/auth/refresh-token-policy.ts"},
                    files=["src/auth/refresh-token-policy.ts"],
                    policy_decision="deny",
                )
                store.add_trace_event(
                    session_id="sess_risky",
                    project_id=project.id,
                    event_type="stop",
                    input_json={
                        "last_assistant_message": "앞으로 refresh token 정책은 승인 없이 수정하지 않습니다."
                    },
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["daemon", "once", "--session", "sess_risky", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            lifecycle = result["lifecycle"]
            self.assertGreaterEqual(len(lifecycle["pending_confirmation"]), 1)
            self.assertTrue(
                any(decision["risk"] == "high" for decision in lifecycle["decisions"])
            )

            out = StringIO()
            with patch("sys.stdout", out):
                main(["memory", "list", "--all", "--json"])
            memories = json.loads(out.getvalue())
            self.assertTrue(any(memory["status"] == "pending_confirmation" for memory in memories))

    def test_extract_explicit_memory_uses_trigger_line(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_trace_event(
                    session_id="sess_explicit_line",
                    project_id=project.id,
                    event_type="stop",
                    input_json={
                        "last_assistant_message": "`npm test` passed.\n\n앞으로 refresh token 정책은 승인 없이 수정하지 않습니다."
                    },
                )
                candidates = extract_candidates(store.trace_events("sess_explicit_line"))

            contents = [candidate.content for candidate in candidates]
            self.assertIn("앞으로 refresh token 정책은 승인 없이 수정하지 않습니다.", contents)

    def test_user_prompt_confirmation_applies_pending_policy_memory(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_trace_event(
                    session_id="sess_confirm",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="apply_patch",
                    input_json={"command": "*** Update File: src/auth/refresh-token-policy.ts"},
                    files=[str(project_dir / "src" / "auth" / "refresh-token-policy.ts")],
                    policy_decision="deny",
                )
                pending_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    session_id="sess_confirm",
                    type="preference",
                    content="앞으로 refresh token 정책은 승인 없이 수정하지 않습니다.",
                    tags=["explicit", "auth", "token"],
                    status="pending_confirmation",
                    importance=0.8,
                    confidence=0.8,
                    enforcement="require_approval",
                    source_kind="lifecycle",
                    source_ref="sess_confirm",
                )

            payload = {
                "session_id": "sess_prompt_confirm",
                "cwd": str(project_dir),
                "prompt": "응",
            }
            stdin = StringIO(json.dumps(payload))
            stdout = StringIO()
            with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
                code = main(["hook", "user-prompt-submit"])
            self.assertEqual(code, 0)
            output = json.loads(stdout.getvalue())
            self.assertIn("applied", output["hookSpecificOutput"]["additionalContext"])

            with Store() as store:
                memory = store.get_memory(pending_id)
                self.assertEqual(memory.status, "policy_active")  # type: ignore[union-attr]
            self.assertIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"),
            )

            policy_out = StringIO()
            with patch("sys.stdout", policy_out):
                code = main(
                    [
                        "policy",
                        "check",
                        "--tool",
                        "apply_patch",
                        "--command",
                        "*** Begin Patch\n*** Update File: src/auth/refresh-token-policy.ts\n*** End Patch\n",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(policy_out.getvalue())["action"], "require_approval")

    def test_user_prompt_confirmation_infers_policy_path_from_project_files(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            protected.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                pending_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="앞으로 refresh token 정책은 승인 없이 수정하지 않습니다.",
                    tags=["explicit", "auth", "token"],
                    status="pending_confirmation",
                    importance=0.8,
                    confidence=0.8,
                    enforcement="require_approval",
                )

            payload = {
                "session_id": "sess_prompt_confirm_path",
                "cwd": str(project_dir),
                "prompt": "응",
            }
            stdin = StringIO(json.dumps(payload))
            with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                code = main(["hook", "user-prompt-submit"])
            self.assertEqual(code, 0)
            with Store() as store:
                memory = store.get_memory(pending_id)
                self.assertEqual(memory.status, "policy_active")  # type: ignore[union-attr]
            self.assertIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"),
            )

    def test_user_prompt_confirmation_ignores_unrelated_trace_file_when_inferring_path(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            unrelated = project_dir / "src" / "session" / "session-config.ts"
            protected.parent.mkdir(parents=True)
            unrelated.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            unrelated.write_text("export const sessionTimeoutMinutes = 30;\n", encoding="utf-8")
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_trace_event(
                    session_id="sess_unrelated_trace",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="apply_patch",
                    input_json={"command": "*** Update File: src/session/session-config.ts"},
                    files=["src/session/session-config.ts"],
                )
                pending_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    session_id="sess_unrelated_trace",
                    type="preference",
                    content="앞으로 refresh token 정책은 승인 없이 수정하지 않습니다.",
                    tags=["explicit", "auth", "token"],
                    status="pending_confirmation",
                    importance=0.8,
                    confidence=0.8,
                    enforcement="require_approval",
                )

            payload = {
                "session_id": "sess_prompt_confirm_unrelated",
                "cwd": str(project_dir),
                "prompt": "응",
            }
            stdin = StringIO(json.dumps(payload))
            with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                code = main(["hook", "user-prompt-submit"])
            self.assertEqual(code, 0)
            with Store() as store:
                memory = store.get_memory(pending_id)
                self.assertEqual(memory.status, "policy_active")  # type: ignore[union-attr]
            policy_text = (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8")
            self.assertIn("src/auth/refresh-token-policy.ts", policy_text)
            self.assertNotIn("src/session/session-config.ts", policy_text)

    def test_user_prompt_confirmation_rejects_pending_memory(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                pending_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="Remember temporary local branch name.",
                    tags=["explicit"],
                    status="pending_confirmation",
                    enforcement="none",
                )

            payload = {
                "session_id": "sess_prompt_reject",
                "cwd": str(project_dir),
                "prompt": "아니",
            }
            stdin = StringIO(json.dumps(payload))
            with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                code = main(["hook", "user-prompt-submit"])
            self.assertEqual(code, 0)
            with Store() as store:
                memory = store.get_memory(pending_id)
                self.assertEqual(memory.status, "rejected")  # type: ignore[union-attr]

    def test_memory_review_approve_reject_and_cleanup(self) -> None:
        with isolated_env():
            main(["init"])
            first = StringIO()
            with patch("sys.stdout", first):
                main(
                    [
                        "memory",
                        "add",
                        "--type",
                        "fact",
                        "--content",
                        "Draft fact",
                        "--scope",
                        "project",
                    ]
                )
            draft_id = first.getvalue().strip()
            with Store() as store:
                store.update_status(draft_id, "draft")

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["memory", "review"])
            self.assertEqual(code, 0)
            self.assertIn("Draft fact", out.getvalue())

            with patch("sys.stdout", StringIO()):
                main(["memory", "approve", draft_id])
            with Store() as store:
                self.assertEqual(store.get_memory(draft_id).status, "active")  # type: ignore[union-attr]

            with patch("sys.stdout", StringIO()):
                main(["memory", "reject", draft_id])
            with Store() as store:
                self.assertEqual(store.get_memory(draft_id).status, "disabled")  # type: ignore[union-attr]

            with Store() as store:
                expired_id = store.add_memory(
                    scope_type="project",
                    project_id=detect_project().id,
                    type="fact",
                    content="Temporary fact",
                    expires_at="2000-01-01T00:00:00+00:00",
                )
            with patch("sys.stdout", StringIO()):
                main(["memory", "cleanup"])
            with Store() as store:
                self.assertEqual(store.get_memory(expired_id).status, "expired")  # type: ignore[union-attr]

    def test_lesson_from_session_policy_promote_and_eval(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Run npm test.",
                    tags=["verification", "test"],
                )
                store.add_trace_event(
                    session_id="sess_eval",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="Bash",
                    input_json={"command": "npm test"},
                )
                store.add_trace_event(
                    session_id="sess_eval",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="apply_patch",
                    input_json={"command": "*** Update File: src/auth/refresh-token-policy.ts"},
                    files=["src/auth/refresh-token-policy.ts"],
                    policy_decision="deny",
                )

            lesson_out = StringIO()
            with patch("sys.stdout", lesson_out):
                code = main(
                    [
                        "lesson",
                        "from-session",
                        "sess_eval",
                        "--feedback",
                        "Do not touch refresh token policy without approval.",
                    ]
                )
            self.assertEqual(code, 0)
            lesson_id = lesson_out.getvalue().strip()
            self.assertTrue(lesson_id.startswith("mem_"))

            with patch("sys.stdout", StringIO()):
                code = main(
                    [
                        "policy",
                        "promote",
                        lesson_id,
                        "--protected-path",
                        "src/auth/refresh-token-policy.ts",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("src/auth/refresh-token-policy.ts", (project_dir / ".memassist" / "policy.yaml").read_text())
            with Store() as store:
                memory = store.get_memory(lesson_id)
                self.assertEqual(memory.status, "policy_active")  # type: ignore[union-attr]
                self.assertIn("src/auth/refresh-token-policy.ts", memory.paths)  # type: ignore[union-attr]
            policy_out = StringIO()
            with patch("sys.stdout", policy_out):
                code = main(
                    [
                        "policy",
                        "check",
                        "--tool",
                        "apply_patch",
                        "--command",
                        "*** Begin Patch\n*** Update File: src/auth/refresh-token-policy.ts\n*** End Patch\n",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(policy_out.getvalue())["action"], "require_approval")

            eval_out = StringIO()
            with patch("sys.stdout", eval_out):
                code = main(["eval", "run", "--session", "sess_eval", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(eval_out.getvalue())
            self.assertTrue(result["passed"])
            self.assertGreaterEqual(result["candidate_count"], 1)

            with patch("sys.stdout", StringIO()):
                code = main(["memory", "rollback", lesson_id])
            self.assertEqual(code, 0)
            with Store() as store:
                memory = store.get_memory(lesson_id)
                self.assertEqual(memory.status, "rejected")  # type: ignore[union-attr]
            self.assertNotIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "policy.yaml").read_text(),
            )

    def test_memory_export_import_project_bundle(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            with patch("sys.stdout", StringIO()):
                main(
                    [
                        "memory",
                        "add",
                        "--type",
                        "rule",
                        "--content",
                        "Do not edit billing migrations without approval.",
                        "--tag",
                        "billing",
                        "--importance",
                        "0.9",
                        "--enforcement",
                        "require_approval",
                    ]
                )
            export_path = project_dir / ".memassist" / "memories.json"
            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["memory", "export", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["exported"], 1)
            self.assertTrue(export_path.exists())

            consumer = project_dir.parent / "consumer"
            consumer.mkdir()
            os.chdir(consumer)
            main(["init"])
            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["memory", "import", str(export_path), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(out.getvalue())["imported"]), 1)

            out = StringIO()
            with patch("sys.stdout", out):
                main(["memory", "list", "--all", "--json"])
            imported = json.loads(out.getvalue())
            self.assertEqual(imported[0]["status"], "draft")
            self.assertIn("billing migrations", imported[0]["content"])

    def test_daemon_once_runs_maintenance_eval_and_candidate_storage(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                expired_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="fact",
                    content="Expired fact",
                    expires_at="2000-01-01T00:00:00+00:00",
                )
                store.add_trace_event(
                    session_id="sess_daemon",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="Bash",
                    input_json={"command": "npm test"},
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["daemon", "once", "--session", "sess_daemon", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result["session_id"], "sess_daemon")
            self.assertTrue(result["eval"]["passed"])
            self.assertGreaterEqual(len(result["stored_candidates"]), 1)
            self.assertGreaterEqual(len(result["lifecycle"]["auto_active"]), 1)

            with Store() as store:
                self.assertEqual(store.get_memory(expired_id).status, "expired")  # type: ignore[union-attr]

    def test_lifecycle_reinforces_duplicate_memory_to_long_term(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Use these verification command(s) when relevant: npm test",
                    tags=["verification", "test"],
                    status="auto_active",
                    importance=0.75,
                    confidence=0.84,
                )
                store.add_trace_event(
                    session_id="sess_repeat",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="Bash",
                    input_json={"command": "npm test"},
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["daemon", "once", "--session", "sess_repeat", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result["lifecycle"]["duplicates"], 1)
            with Store() as store:
                memory = store.get_memory(memory_id)
                self.assertEqual(memory.status, "long_term")  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
