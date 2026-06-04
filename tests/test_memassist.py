from __future__ import annotations

import json
import os
import shlex
import tempfile
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from memassist.cli import main
from memassist.extraction import extract_candidates
from memassist.hooks import codex_hooks_status, install_codex_hooks, uninstall_codex_hooks
from memassist.memory_judge import INTERPRETER_ACTIVE_ENV
from memassist.models import MEMORY_STATUSES, Memory
from memassist.policy import default_policy_yaml, load_policy
from memassist.project import detect_project, detect_project_for_init
from memassist.retrieval import analyze_query_intent, build_memory_pack
from memassist.storage import Store, now_iso
from memassist.trace import extract_files


@contextmanager
def isolated_env():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "home"
        project = root / "project"
        project.mkdir()
        home.mkdir()
        old_cwd = Path.cwd()
        old_home = os.environ.get("MEMASSIST_HOME")
        old_user_home = os.environ.get("HOME")
        old_codex = os.environ.get("CODEX_HOME")
        os.environ.pop("MEMASSIST_HOME", None)
        os.environ["HOME"] = str(home)
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
            if old_user_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_user_home
            if old_codex is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old_codex


def _fire_hook(event: str, payload: dict) -> int:
    with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
        return main(["hook", event])


def _stop_for(payload: dict) -> int:
    """Fire the Stop hook for the same session/cwd so turn-end judging runs.

    Durable memory is produced at turn end (Stop), not inline at UserPromptSubmit,
    so tests that assert judged memory must drive the full turn.
    """
    stop_payload = {
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
    }
    for key in ("prompt", "message", "content"):
        if payload.get(key):
            stop_payload[key] = payload.get(key)
            break
    return _fire_hook("stop", stop_payload)


def _collect_hook_commands(obj: object) -> list[str]:
    """Recursively collect every hook `command` string from a parsed hooks.json,
    regardless of the surrounding wrapper structure."""
    commands: list[str] = []
    if isinstance(obj, dict):
        command = obj.get("command")
        if isinstance(command, str):
            commands.append(command)
        for value in obj.values():
            commands.extend(_collect_hook_commands(value))
    elif isinstance(obj, list):
        for item in obj:
            commands.extend(_collect_hook_commands(item))
    return commands


class MemassistTest(unittest.TestCase):
    def test_init_creates_project_policy(self) -> None:
        with isolated_env() as (_root, project, _home):
            code = main(["init"])
            self.assertEqual(code, 0)
            policy_path = project / ".memassist" / "policy.yaml"
            self.assertTrue(policy_path.exists())
            policy_text = policy_path.read_text(encoding="utf-8")
            self.assertIn("verification_commands: []", policy_text)
            self.assertNotIn(".env", policy_text)
            self.assertNotIn("rm -rf", policy_text)
            self.assertTrue((project / ".memassist" / "ignore").exists())
            self.assertTrue((project / ".memassist" / "memories" / "active").exists())
            self.assertTrue((project / ".memassist" / "memories" / "candidates").exists())
            self.assertTrue((project / ".memassist" / "memories" / "archived").exists())

    def test_init_ignores_global_memassist_home_as_project_marker(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            project = fake_home / "projects" / "plain-project"
            project.mkdir(parents=True)
            global_memassist = fake_home / ".memassist"
            global_memassist.mkdir()
            old_cwd = Path.cwd()
            old_home = os.environ.get("MEMASSIST_HOME")
            old_codex = os.environ.get("CODEX_HOME")
            os.environ["MEMASSIST_HOME"] = str(global_memassist)
            os.environ["CODEX_HOME"] = str(fake_home / ".codex")
            os.chdir(project)
            try:
                detected = detect_project_for_init()
                self.assertEqual(detected.root, project.resolve())
                code = main(["init", "--tools", "codex"])
                self.assertEqual(code, 0)
                self.assertTrue((project / ".memassist" / "policy.yaml").exists())
                self.assertTrue((project / ".codex" / "hooks.json").exists())
                self.assertFalse((fake_home / ".memassist" / "policy.yaml").exists())
                self.assertFalse((fake_home / ".codex" / "hooks.json").exists())
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

    def test_init_uses_cwd_when_parent_memassist_exists_with_external_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "parent"
            project = parent / "plain-project"
            external_home = project / ".memassist-home"
            parent.mkdir()
            project.mkdir()
            (parent / ".memassist").mkdir()
            old_cwd = Path.cwd()
            old_home = os.environ.get("MEMASSIST_HOME")
            old_user_home = os.environ.get("HOME")
            old_codex = os.environ.get("CODEX_HOME")
            os.environ["MEMASSIST_HOME"] = str(external_home)
            os.environ["HOME"] = str(root / "user-home")
            os.environ["CODEX_HOME"] = str(root / "codex-home")
            os.chdir(project)
            try:
                detected = detect_project_for_init()
                self.assertEqual(detected.root, project.resolve())
                code = main(["init", "--tools", "codex"])
                self.assertEqual(code, 0)
                self.assertTrue((project / ".memassist" / "policy.yaml").exists())
                self.assertTrue((project / ".memassist" / "memassist.db").exists())
                self.assertTrue((project / ".codex" / "hooks.json").exists())
                self.assertFalse((parent / ".codex" / "hooks.json").exists())
                hooks_data = json.loads(
                    (project / ".codex" / "hooks.json").read_text(encoding="utf-8")
                )
                # The home value is shell-quoted in the hook command (shlex.quote),
                # which differs per-OS for paths containing backslashes. Parse the
                # JSON and compare against the same quoting so this holds on any OS.
                commands = _collect_hook_commands(hooks_data)
                expected_home = (
                    f"MEMASSIST_HOME={shlex.quote(str((project / '.memassist').resolve()))}"
                )
                self.assertTrue(
                    any(expected_home in command for command in commands),
                    f"{expected_home!r} not found in {commands!r}",
                )
            finally:
                os.chdir(old_cwd)
                if old_home is None:
                    os.environ.pop("MEMASSIST_HOME", None)
                else:
                    os.environ["MEMASSIST_HOME"] = old_home
                if old_user_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_user_home
                if old_codex is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = old_codex

    def test_project_hook_stores_judged_memory_in_project_local_db(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            parent = root / "parent"
            project = parent / "plain-project"
            external_home = project / ".memassist-home"
            parent.mkdir()
            project.mkdir()
            (parent / ".memassist").mkdir()
            old_cwd = Path.cwd()
            old_home = os.environ.get("MEMASSIST_HOME")
            old_user_home = os.environ.get("HOME")
            old_codex = os.environ.get("CODEX_HOME")
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_HOME"] = str(external_home)
            os.environ["HOME"] = str(root / "user-home")
            os.environ["CODEX_HOME"] = str(root / "codex-home")
            os.chdir(project)
            try:
                self.assertEqual(main(["init", "--tools", "codex"]), 0)
                local_home = project / ".memassist"
                os.environ["MEMASSIST_HOME"] = str(local_home)
                os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = json.dumps(
                    {
                        "should_store": True,
                        "memory_content": "Confirm before changing refresh token behavior.",
                        "source_quote": "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해",
                        "memory_type": "directive",
                        "enforcement": "warn",
                        "activation": "candidate",
                        "candidate_paths": ["src/auth/refresh-token-policy.ts"],
                        "meaning_preserved": True,
                        "contamination_risk": "low",
                        "reason": "User asked to confirm before future refresh token changes.",
                    }
                )
                payload = {
                    "session_id": "sess_project_local_home",
                    "cwd": str(project),
                    "prompt": "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해",
                }
                with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
                _stop_for(payload)
                self.assertTrue((local_home / "memassist.db").exists())
                with Store(local_home / "memassist.db") as store:
                    memories = store.list_memories(project_id=None, include_global=False, status=None)
                    events = store.trace_events("sess_project_local_home")
                self.assertTrue(any(memory.source_kind == "isolated_memory_judge" for memory in memories))
                judged_memory = next(memory for memory in memories if memory.source_kind == "isolated_memory_judge")
                artifact = local_home / "memories" / "candidates" / f"{judged_memory.id}.md"
                self.assertTrue(artifact.exists())
                artifact_text = artifact.read_text(encoding="utf-8")
                self.assertIn("Confirm before changing refresh token behavior.", artifact_text)
                self.assertIn("앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해", artifact_text)
                self.assertTrue(any(event["event_type"] == "memory_judged" for event in events))
                self.assertFalse((external_home / "memassist.db").exists())
            finally:
                os.chdir(old_cwd)
                if old_home is None:
                    os.environ.pop("MEMASSIST_HOME", None)
                else:
                    os.environ["MEMASSIST_HOME"] = old_home
                if old_user_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_user_home
                if old_codex is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = old_codex
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

    def test_init_can_install_codex_tool_and_doctor_reports_setup(self) -> None:
        with isolated_env() as (_root, project, _home):
            code = main(["init", "--tools", "codex"])
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
            self.assertEqual(checks["tool_integrations"]["status"], "pass")

    def test_doctor_reports_judge_backend_and_warns_when_executable_missing(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init", "--tools", "codex"]), 0)
            out = StringIO()
            with patch("memassist.memory_judge.shutil.which", return_value=None), patch("sys.stdout", out):
                code = main(["doctor", "--json"])
            self.assertEqual(code, 0)
            report = json.loads(out.getvalue())
            checks = {check["name"]: check for check in report["checks"]}
            # The directive interpreter and codex_cli checks were removed; doctor now
            # reports the live judge backend instead.
            self.assertNotIn("directive_interpreter", checks)
            self.assertNotIn("codex_cli", checks)
            self.assertEqual(checks["memory_judge"]["status"], "warn")
            self.assertIn("codex", checks["memory_judge"]["detail"])
            self.assertIn("executable not found", checks["memory_judge"]["detail"])

    def test_doctor_reports_claude_judge_backend_ready(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init", "--tools", "claude"]), 0)
            out = StringIO()
            with patch("memassist.memory_judge.shutil.which", return_value="/usr/bin/claude"), patch("sys.stdout", out):
                code = main(["doctor", "--json"])
            self.assertEqual(code, 0)
            checks = {check["name"]: check for check in json.loads(out.getvalue())["checks"]}
            self.assertEqual(checks["memory_judge"]["status"], "pass")
            self.assertIn("claude", checks["memory_judge"]["detail"])
            self.assertNotIn("directive_interpreter", checks)

    def test_init_tools_all_installs_supported_project_integrations(self) -> None:
        with isolated_env() as (_root, project, _home):
            code = main(["init", "--tools", "all"])
            self.assertEqual(code, 0)
            self.assertTrue((project / ".codex" / "hooks.json").exists())
            self.assertTrue((project / ".claude" / "settings.json").exists())
            self.assertTrue((project / ".opencode" / "plugins" / "memassist.js").exists())

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["tools", "status", "--json"])
            self.assertEqual(code, 0)
            statuses = {item["tool"]: item for item in json.loads(out.getvalue())}
            self.assertEqual(set(statuses), {"codex", "claude", "opencode"})
            self.assertTrue(all(item["installed"] for item in statuses.values()))
            self.assertTrue(statuses["codex"]["capabilities"]["prompt_memory_injection"])
            self.assertTrue(statuses["codex"]["capabilities"]["llm_directive_interpretation"])
            # Claude now has a judge adapter (claude -p), so it is judge-capable;
            # OpenCode has no adapter and stays non-judge-capable.
            self.assertTrue(statuses["claude"]["capabilities"]["llm_directive_interpretation"])
            self.assertFalse(statuses["opencode"]["capabilities"]["llm_directive_interpretation"])

    def test_tools_uninstall_removes_selected_integration(self) -> None:
        with isolated_env() as (_root, project, _home):
            self.assertEqual(main(["init", "--tools", "codex,claude"]), 0)
            self.assertTrue((project / ".codex" / "hooks.json").exists())
            self.assertTrue((project / ".claude" / "settings.json").exists())

            self.assertEqual(main(["tools", "uninstall", "claude"]), 0)
            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["tools", "status", "codex,claude", "--json"])
            self.assertEqual(code, 0)
            statuses = {item["tool"]: item for item in json.loads(out.getvalue())}
            self.assertTrue(statuses["codex"]["installed"])
            self.assertFalse(statuses["claude"]["installed"])

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
                    enforcement="block",
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
                self.assertEqual(pack.policy, [])
                self.assertTrue(any(memory.enforcement == "block" for memory in pack.context))
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
                    content="Block active retrieval policy behavior changes unless the directive changes.",
                    tags=["policy", "retrieval"],
                    enforcement="block",
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
                self.assertEqual(pack.policy, [])
                self.assertTrue(any(memory.enforcement == "block" for memory in pack.context))
                self.assertTrue(any(memory.type == "workflow" for memory in pack.verifier))
            finally:
                store.close()

    def test_memory_pack_retrieves_source_language_refresh_token_memory_across_variants(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content="앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해",
                    tags=["auth", "token", "refresh", "directive"],
                    paths=["src/auth/refresh-token-policy.ts"],
                    status="active",
                    enforcement="warn",
                    source_kind="isolated_memory_judge",
                )
                for query in [
                    "리프레시 토큰 15분으로 변경해줘",
                    "리프레쉬 토큰 TTL 바꿔줘",
                    "change refresh token TTL to 15 minutes",
                ]:
                    pack = build_memory_pack(store, query=query, project_id=project.id)
                    self.assertTrue(
                        any("리프레시 토큰 변경" in memory.content for memory in pack.context),
                        query,
                    )
                    self.assertEqual(pack.policy, [])

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
                    status="active",
                    importance=0.7,
                )
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="Keep refresh token policy changes as an internal blocked memory policy.",
                    tags=["auth", "token"],
                    status="candidate",
                    importance=0.9,
                )
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Use npm test after session timeout changes.",
                    tags=["verification", "session", "timeout"],
                    status="active",
                    importance=0.7,
                    source_kind="retrieval_eval_seed",
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
            self.assertEqual(result["precision_at_k"], 1.0)
            self.assertEqual(result["forbidden_recall_rate"], 0.0)
            with Store() as store:
                memories = store.list_memories(project_id=project.id, include_global=False)
                self.assertFalse(any(memory.source_kind == "retrieval_eval_seed" for memory in memories))

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
                    status="active",
                    importance=0.8,
                    confidence=0.9,
                )
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="Keep refresh token policy changes as an internal blocked memory policy.",
                    tags=["auth", "token"],
                    status="candidate",
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
                    status="active",
                    importance=0.8,
                    confidence=0.9,
                )
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="rule",
                    content="Refresh token policy edits are blocked by autonomous memory policy.",
                    tags=["refresh", "token", "policy"],
                    status="active",
                    enforcement="block",
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
                    status="active",
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

    def test_codex_hook_install_status_uninstall(self) -> None:
        with isolated_env() as (_root, project, _home):
            path = install_codex_hooks(project_root=project, memassist_home=project / ".memassist")
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

    def test_hook_pre_tool_use_allows_without_project_policy(self) -> None:
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
            self.assertEqual(stdout.getvalue(), "")

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

    def test_user_prompt_submit_does_not_parse_markdown_artifacts(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            artifact_dir = project_dir / ".memassist" / "memories" / "active"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "mem_fake.md").write_text(
                "# Directive\n\nThis markdown-only memory should not be injected.\n",
                encoding="utf-8",
            )
            payload = {
                "session_id": "sess_markdown_only",
                "cwd": str(project_dir),
                "prompt": "markdown-only memory",
            }
            stdout = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", stdout):
                self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
            self.assertNotIn("SQLite-only stale retrieval sentinel", stdout.getvalue())

    def test_markdown_memory_rebuild_updates_sqlite_search_index(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Use pnpm for this repository.",
                    tags=["tooling"],
                    status="active",
                )

            artifact = project_dir / ".memassist" / "memories" / "active" / f"{memory_id}.md"
            text = artifact.read_text(encoding="utf-8")
            artifact.write_text(
                text.replace("Use pnpm for this repository.", "Use uv for Python package management."),
                encoding="utf-8",
            )

            with Store() as store:
                self.assertFalse(
                    any("Use uv" in memory.content for memory in store.search_memories("uv", project_id=project.id))
                )
                indexed = store.rebuild_memory_index_from_artifacts()
                self.assertIn(memory_id, indexed)
                updated = store.get_memory(memory_id)
                results = store.search_memories("uv", project_id=project.id)

            self.assertIsNotNone(updated)
            self.assertEqual(updated.content, "Use uv for Python package management.")
            self.assertTrue(any(memory.id == memory_id for memory in results))

    def test_markdown_rebuild_prunes_deleted_memory_from_sqlite_index(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="fact",
                    content="This repository uses Hatch.",
                    tags=["tooling"],
                    status="active",
                )
                self.assertTrue(store.search_memories("Hatch", project_id=project.id))

            artifact = project_dir / ".memassist" / "memories" / "active" / f"{memory_id}.md"
            artifact.unlink()

            with Store() as store:
                indexed = store.rebuild_memory_index_from_artifacts()
                results = store.search_memories("Hatch", project_id=project.id)

            self.assertNotIn(memory_id, indexed)
            self.assertFalse(any(memory.id == memory_id for memory in results))

    def test_sqlite_deletion_and_rebuild_preserves_markdown_lifecycle_buckets(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                active_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content="Ask before changing refresh token behavior.",
                    tags=["auth", "token"],
                    paths=["src/auth/session.ts"],
                    status="active",
                    source_ref="turn://active",
                    source_quote="ask before refresh token changes",
                )
                candidate_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Consider running focused auth regression tests.",
                    tags=["auth", "test"],
                    status="candidate",
                    source_ref="turn://candidate",
                    source_quote="run focused auth tests",
                )
                archived_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="fact",
                    content="The old auth server used a legacy token table.",
                    tags=["auth", "legacy"],
                    status="archived",
                    source_ref="turn://archived",
                    source_quote="old auth server legacy table",
                )

            db = project_dir / ".memassist" / "memassist.db"
            db.unlink()
            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["memory", "rebuild-index", "--json"]), 0)
            rebuilt = json.loads(out.getvalue())
            self.assertEqual(set(rebuilt["indexed"]), {active_id, candidate_id, archived_id})

            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["memory", "list", "--all", "--json"]), 0)
            listed = {memory["id"]: memory for memory in json.loads(out.getvalue())}
            self.assertEqual(listed[active_id]["status"], "active")
            self.assertEqual(listed[candidate_id]["status"], "candidate")
            self.assertEqual(listed[archived_id]["status"], "archived")
            for memory_id, bucket, quote in (
                (active_id, "active", "ask before refresh token changes"),
                (candidate_id, "candidates", "run focused auth tests"),
                (archived_id, "archived", "old auth server legacy table"),
            ):
                artifact = project_dir / ".memassist" / "memories" / bucket / f"{memory_id}.md"
                self.assertTrue(artifact.exists())
                self.assertIn(quote, artifact.read_text(encoding="utf-8"))

            with Store() as store:
                results = store.search_memories("refresh token", project_id=project.id)
            self.assertTrue(any(memory.id == active_id for memory in results))

    def test_sqlite_only_row_ignored_by_list_export_update_and_retrieval_after_rebuild(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                markdown_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="fact",
                    content="Markdown-backed memory describes canonical artifact authority.",
                    tags=["source"],
                    status="active",
                )
                stale_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="fact",
                    content="SQLite-only stale retrieval sentinel.",
                    tags=["stale-sentinel"],
                    status="active",
                )
                (project_dir / ".memassist" / "memories" / "active" / f"{stale_id}.md").unlink()

            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["memory", "rebuild-index", "--json"]), 0)
            self.assertNotIn(stale_id, json.loads(out.getvalue())["indexed"])

            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["memory", "list", "--all", "--json"]), 0)
            listed = json.loads(out.getvalue())
            self.assertIn(markdown_id, {memory["id"] for memory in listed})
            self.assertNotIn(stale_id, {memory["id"] for memory in listed})

            export_path = project_dir / "memories-export.json"
            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["memory", "export", str(export_path), "--all", "--json"]), 0)
            exported = json.loads(export_path.read_text(encoding="utf-8"))["memories"]
            self.assertTrue(
                any(memory["content"] == "Markdown-backed memory describes canonical artifact authority." for memory in exported)
            )
            self.assertFalse(any(memory["content"] == "SQLite-only stale retrieval sentinel." for memory in exported))

            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["memory", "activate", stale_id]), 1)
            self.assertIn("Memory not found", out.getvalue())

            payload = {
                "session_id": "sess_stale_sqlite",
                "cwd": str(project_dir),
                "prompt": "SQLite-only stale retrieval sentinel",
            }
            stdout = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", stdout):
                self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
            self.assertEqual(stdout.getvalue(), "")

            with Store() as store:
                row = store.conn.execute("SELECT id FROM memories WHERE id = ?", (stale_id,)).fetchone()
            self.assertIsNone(row)

    def test_management_commands_work_when_sqlite_missing_or_empty(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                active_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Keep managed memories readable without SQLite.",
                    tags=["sqlite"],
                    status="active",
                )
                candidate_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Activate this candidate without relying on SQLite rows.",
                    tags=["sqlite"],
                    status="candidate",
                )

            db = project_dir / ".memassist" / "memassist.db"
            db.unlink()
            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["memory", "list", "--all", "--json"]), 0)
            self.assertEqual({active_id, candidate_id}, {memory["id"] for memory in json.loads(out.getvalue())})

            with patch("sys.stdout", StringIO()):
                self.assertEqual(main(["memory", "activate", candidate_id]), 0)
            self.assertTrue((project_dir / ".memassist" / "memories" / "active" / f"{candidate_id}.md").exists())

            with Store() as store:
                store.conn.execute("DELETE FROM memory_fts")
                store.conn.execute("DELETE FROM memories")
                store.conn.commit()

            with patch("sys.stdout", StringIO()):
                self.assertEqual(main(["memory", "deactivate", active_id]), 0)
            self.assertTrue((project_dir / ".memassist" / "memories" / "archived" / f"{active_id}.md").exists())

            export_path = project_dir / "empty-index-export.json"
            with patch("sys.stdout", StringIO()):
                self.assertEqual(main(["memory", "export", str(export_path), "--all"]), 0)
            exported_ids = {
                memory["content"]
                for memory in json.loads(export_path.read_text(encoding="utf-8"))["memories"]
            }
            self.assertIn("Keep managed memories readable without SQLite.", exported_ids)
            self.assertIn("Activate this candidate without relying on SQLite rows.", exported_ids)

    def test_markdown_write_failure_does_not_create_sqlite_only_memory(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                with patch("memassist.storage.sync_memory_artifact", side_effect=OSError("artifact write failed")):
                    with self.assertRaises(OSError):
                        store.add_memory(
                            scope_type="project",
                            project_id=project.id,
                            type="fact",
                            content="This should never become a SQLite-only managed memory.",
                            tags=["artifact-failure"],
                            status="active",
                        )
                row = store.conn.execute(
                    "SELECT id FROM memories WHERE content = ?",
                    ("This should never become a SQLite-only managed memory.",),
                ).fetchone()
                self.assertIsNone(row)

    def test_lifecycle_bucket_path_overrides_conflicting_metadata_status(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                active_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content="Active bucket wins over archived metadata.",
                    tags=["bucket"],
                    status="active",
                )
                candidate_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Candidate bucket wins over active metadata.",
                    tags=["bucket"],
                    status="candidate",
                )
                archived_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="fact",
                    content="Archived bucket wins over active metadata.",
                    tags=["bucket"],
                    status="archived",
                )

            _replace_artifact_metadata_status(
                project_dir / ".memassist" / "memories" / "active" / f"{active_id}.md",
                "archived",
            )
            _replace_artifact_metadata_status(
                project_dir / ".memassist" / "memories" / "candidates" / f"{candidate_id}.md",
                "active",
            )
            _replace_artifact_metadata_status(
                project_dir / ".memassist" / "memories" / "archived" / f"{archived_id}.md",
                "active",
            )

            with Store() as store:
                indexed = store.rebuild_memory_index_from_artifacts()
                listed = {memory.id: memory for memory in store.list_memories(project_id=project.id, include_global=False, status=None)}
                active_results = store.search_memories("bucket wins", project_id=project.id)

            self.assertEqual(set(indexed), {active_id, candidate_id, archived_id})
            self.assertEqual(listed[active_id].status, "active")
            self.assertEqual(listed[candidate_id].status, "candidate")
            self.assertEqual(listed[archived_id].status, "archived")
            self.assertTrue(any(memory.id == active_id for memory in active_results))
            self.assertFalse(any(memory.id in {candidate_id, archived_id} for memory in active_results))

    def test_retrieval_telemetry_can_reset_on_rebuild_without_losing_memory_or_evidence(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content="Review auth telemetry before changing refresh token TTL.",
                    tags=["auth", "telemetry", "refresh"],
                    paths=["src/auth/session.ts"],
                    status="active",
                    source_ref="turn://telemetry",
                    source_quote="review auth telemetry first",
                )
                self.assertTrue(store.search_memories("refresh token telemetry", project_id=project.id))
                used = store.get_memory(memory_id)
                self.assertIsNotNone(used)
                self.assertGreater(used.retrieval_count, 0)  # type: ignore[union-attr]

            db = project_dir / ".memassist" / "memassist.db"
            db.unlink()
            with patch("sys.stdout", StringIO()):
                self.assertEqual(main(["memory", "rebuild-index"]), 0)

            artifact = project_dir / ".memassist" / "memories" / "active" / f"{memory_id}.md"
            artifact_text = artifact.read_text(encoding="utf-8")
            self.assertIn("review auth telemetry first", artifact_text)
            self.assertIn("turn://telemetry", artifact_text)
            with Store() as store:
                rebuilt = store.get_memory(memory_id)
                results = store.search_memories("refresh token telemetry", project_id=project.id)
            self.assertIsNotNone(rebuilt)
            self.assertEqual(rebuilt.status, "active")  # type: ignore[union-attr]
            self.assertEqual(rebuilt.content, "Review auth telemetry before changing refresh token TTL.")  # type: ignore[union-attr]
            self.assertEqual(rebuilt.retrieval_count, 0)  # type: ignore[union-attr]
            self.assertTrue(any(memory.id == memory_id for memory in results))

    def test_legacy_statuses_do_not_create_retrieval_or_management_behavior(self) -> None:
        legacy_statuses = {"draft", "pinned", "disabled", "deleted", "expired", "superseded", "durable"}
        self.assertFalse(legacy_statuses & MEMORY_STATUSES)
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                legacy_ids = [
                    _insert_legacy_sqlite_only_memory(store, project_id=project.id, status=status)
                    for status in sorted(legacy_statuses)
                ]
                store.rebuild_memory_index_from_artifacts()
                for memory_id in legacy_ids:
                    self.assertIsNone(store.get_memory(memory_id))
                self.assertFalse(store.search_memories("legacy sqlite status sentinel", project_id=project.id))

            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["memory", "list", "--all", "--json"]), 0)
            self.assertFalse(
                any("legacy sqlite status sentinel" in memory["content"] for memory in json.loads(out.getvalue()))
            )

            payload = {
                "session_id": "sess_legacy_status",
                "cwd": str(project_dir),
                "prompt": "legacy sqlite status sentinel",
            }
            stdout = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", stdout):
                self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
            self.assertEqual(stdout.getvalue(), "")

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

    def test_stop_hook_does_not_auto_activate_assistant_echo_memory(self) -> None:
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
            self.assertFalse(any("앞으로 이 프로젝트는 npm test로 검증하세요." in memory["content"] for memory in memories))

    def test_lifecycle_keeps_risky_lessons_as_inactive_candidates(self) -> None:
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
                        "last_assistant_message": "앞으로 refresh token 정책은 묻지 않고 수정하지 않습니다."
                    },
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["daemon", "once", "--session", "sess_risky", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            lifecycle = result["lifecycle"]
            self.assertGreaterEqual(len(lifecycle["candidates"]), 1)
            self.assertTrue(any(decision["risk"] in {"medium", "high"} for decision in lifecycle["decisions"]))

            out = StringIO()
            with patch("sys.stdout", out):
                main(["memory", "list", "--all", "--json"])
            memories = json.loads(out.getvalue())
            self.assertTrue(any(memory["status"] == "candidate" for memory in memories))

    def test_extract_candidates_ignores_assistant_echo_memory_wording(self) -> None:
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
                        "last_assistant_message": "`npm test` passed.\n\n앞으로 refresh token 정책은 묻지 않고 수정하지 않습니다."
                    },
                )
                candidates = extract_candidates(store.trace_events("sess_explicit_line"))

            contents = [candidate.content for candidate in candidates]
            self.assertNotIn("앞으로 refresh token 정책은 묻지 않고 수정하지 않습니다.", contents)

    def test_user_prompt_short_reply_does_not_activate_candidate_memory(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            protected.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                candidate_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="앞으로 refresh token 정책은 묻지 않고 수정하지 않습니다.",
                    tags=["explicit", "auth", "token"],
                    status="candidate",
                    importance=0.8,
                    confidence=0.8,
                    enforcement="none",
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
                memory = store.get_memory(candidate_id)
                self.assertEqual(memory.status, "candidate")  # type: ignore[union-attr]
            self.assertNotIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"),
            )

    def test_stop_records_source_event_without_direct_memory_when_judge_unavailable(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            target = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            target.parent.mkdir(parents=True)
            target.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            payload = {
                "session_id": "sess_source_only",
                "cwd": str(project_dir),
                "prompt": "앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐",
            }
            with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
            with Store() as store:
                submit_event_types = [event["event_type"] for event in store.trace_events("sess_source_only")]
            self.assertNotIn("memory_source_observed", submit_event_types)
            self.assertNotIn("memory_intent_observed", submit_event_types)
            _stop_for(payload)

            project = detect_project()
            with Store() as store:
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                events = [dict(event) for event in store.trace_events("sess_source_only")]
            event_types = [event["event_type"] for event in events]
            self.assertIn("memory_source_observed", event_types)
            self.assertIn("memory_judged", event_types)
            self.assertFalse(any(memory.source_kind == "isolated_memory_judge" for memory in memories))
            self.assertNotIn("src/auth/refresh-token-policy.ts", (project_dir / ".memassist" / "policy.yaml").read_text())

    def test_stop_without_source_evidence_does_not_judge_memory(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            payload = {"session_id": "sess_no_source", "cwd": str(project_dir)}
            self.assertEqual(_fire_hook("stop", payload), 0)
            with Store() as store:
                event_types = [event["event_type"] for event in store.trace_events("sess_no_source")]
            self.assertIn("stop", event_types)
            self.assertNotIn("memory_source_observed", event_types)
            self.assertNotIn("memory_judged", event_types)

    def test_isolated_judge_payload_excludes_assistant_and_injected_context(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content="Injected memory must not be sent as judge source.",
                    tags=["refresh", "token"],
                    status="active",
                )
                store.add_trace_event(
                    session_id="sess_payload_isolation",
                    project_id=project.id,
                    event_type="stop",
                    input_json={"last_assistant_message": "앞으로 페이지 리프레시는 승인 요청하겠습니다."},
                )
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "refresh token 관련 변경 전 사용자에게 먼저 확인한다.",
                    "source_quote": "앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐",
                    "memory_type": "directive",
                    "enforcement": "warn",
                    "activation": "candidate",
                    "candidate_paths": [],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "Payload isolation test.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                payload = {
                    "session_id": "sess_payload_isolation",
                    "cwd": str(project_dir),
                    "prompt": "앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐",
                }
                with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
                _stop_for(payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            with Store() as store:
                judged = [
                    dict(event)
                    for event in store.trace_events("sess_payload_isolation")
                    if event["event_type"] == "memory_judged"
                ]
            self.assertEqual(len(judged), 1)
            payload_json = json.loads(judged[0]["input_json"])["payload"]
            serialized = json.dumps(payload_json, ensure_ascii=False)
            self.assertIn("앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐", serialized)
            self.assertNotIn("Injected memory must not be sent as judge source.", serialized)
            self.assertNotIn("앞으로 페이지 리프레시는 승인 요청하겠습니다.", serialized)
            self.assertIn("assistant_responses", serialized)

    def test_low_risk_judge_preference_auto_activates(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "사용자는 한국어로 간결한 답변을 선호한다.",
                    "source_quote": "앞으로 답변은 한국어로 짧게 해줘",
                    "memory_type": "preference",
                    "enforcement": "none",
                    "activation": "active",
                    "candidate_paths": [],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "Low-risk user preference.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                payload = {
                    "session_id": "sess_low_risk_active",
                    "cwd": str(project_dir),
                    "prompt": "앞으로 답변은 한국어로 짧게 해줘",
                }
                with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
                _stop_for(payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            project = detect_project()
            with Store() as store:
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
            self.assertTrue(
                any(
                    memory.source_kind == "isolated_memory_judge"
                    and memory.status == "active"
                    and memory.enforcement == "none"
                    for memory in memories
                )
            )

    def test_direct_policy_instruction_does_not_upgrade_existing_candidate_in_user_prompt_submit(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            protected.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            project = detect_project()
            content = "리프레시 토큰 관련 변경은 변경전에 나에게 확인해야해"
            with Store() as store:
                store.upsert_project(project)
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content=content,
                    tags=["explicit", "auth", "token"],
                    status="candidate",
                    enforcement="none",
                )

            payload = {
                "session_id": "sess_direct_policy_upgrade_candidate",
                "cwd": str(project_dir),
                "prompt": content,
            }
            stdin = StringIO(json.dumps(payload))
            with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                code = main(["hook", "user-prompt-submit"])
            self.assertEqual(code, 0)

            with Store() as store:
                memory = store.get_memory(memory_id)
                traces = store.trace_events("sess_direct_policy_upgrade_candidate")
                self.assertEqual(memory.status, "candidate")  # type: ignore[union-attr]
                self.assertEqual(memory.enforcement, "none")  # type: ignore[union-attr]
            self.assertFalse(any(event["event_type"] == "memory_intent_observed" for event in traces))
            self.assertNotIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"),
            )

    def test_user_prompt_direct_policy_instruction_is_retrieved_without_policy_activation(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            protected.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "리프레시 토큰 관련 변경은 사용자 확인 전 수정하지 않는다.",
                    "source_quote": "리프레시 토큰 관련 변경은 절대 묻지 않고 수정하지마",
                    "memory_type": "directive",
                    "enforcement": "block",
                    "activation": "active",
                    "candidate_paths": ["src/auth/refresh-token-policy.ts"],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "User explicitly set a future edit gate for refresh token changes.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture

            try:
                payload = {
                    "session_id": "sess_direct_policy",
                    "cwd": str(project_dir),
                    "prompt": "리프레시 토큰 관련 변경은 절대 묻지 않고 수정하지마",
                }
                stdin = StringIO(json.dumps(payload))
                stdout = StringIO()
                with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
                    code = main(["hook", "user-prompt-submit"])
                self.assertEqual(code, 0)
                # WRITE happens at turn end; the just-submitted prompt cannot inject
                # memory it has not created yet. Injection is verified on the later
                # rag_payload prompt below.
                _stop_for(payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            project = detect_project()
            with Store() as store:
                memories = store.list_memories(project_id=project.id, include_global=True)
                traces = store.trace_events("sess_direct_policy")
            direct_memories = [
                memory
                for memory in memories
                if memory.source_kind == "isolated_memory_judge"
                and "리프레시 토큰" in memory.content
            ]
            self.assertEqual(len(direct_memories), 1)
            self.assertEqual(direct_memories[0].status, "active")
            self.assertEqual(direct_memories[0].enforcement, "block")
            self.assertTrue(any(event["event_type"] == "memory_source_observed" for event in traces))
            self.assertTrue(any(event["event_type"] == "memory_judged" for event in traces))
            self.assertNotIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"),
            )

            with patch("sys.stdout", StringIO()):
                self.assertEqual(main(["memory", "activate", direct_memories[0].id]), 0)
            self.assertNotIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"),
            )

            rag_payload = {
                "session_id": "sess_direct_policy_rag",
                "cwd": str(project_dir),
                "prompt": "리프레시 토큰 15분으로 변경해줘",
            }
            rag_out = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(rag_payload))), patch("sys.stdout", rag_out):
                self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
            injected = json.loads(rag_out.getvalue())
            context = injected["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Relevant memassist memory:", context)
            self.assertIn("리프레시 토큰 관련 변경은 사용자 확인 전 수정하지 않는다.", context)
            self.assertNotIn("Policy reminders", context)

    def test_user_prompt_direct_policy_instruction_does_not_activate_unrelated_candidate_memory(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            protected.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                candidate_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="Remember temporary branch cleanup note.",
                    tags=["explicit"],
                    status="candidate",
                    enforcement="none",
                )

            payload = {
                "session_id": "sess_direct_policy_with_candidate",
                "cwd": str(project_dir),
                "prompt": "리프레시 토큰 관련 변경은 변경전에 나에게 확인해야해",
            }
            stdin = StringIO(json.dumps(payload))
            with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                code = main(["hook", "user-prompt-submit"])
            self.assertEqual(code, 0)
            with Store() as store:
                candidate = store.get_memory(candidate_id)
            self.assertEqual(candidate.status, "candidate")  # type: ignore[union-attr]

    def test_non_keyword_directive_is_captured_at_stop_not_inline(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "리프레시 토큰 변경 시 사용자 확인을 받는다.",
                    "source_quote": "리프레시토큰 변경에 승인확인하라",
                    "memory_type": "directive",
                    "enforcement": "warn",
                    "activation": "active",
                    "candidate_paths": [],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "Directive without any trigger keyword.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                # No keyword like 앞으로/먼저/기억 — the old gate would have dropped this.
                payload = {"session_id": "sess_nokw", "cwd": str(project_dir), "prompt": "리프레시토큰 변경에 승인확인하라"}
                with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
                project = detect_project()
                with Store() as store:
                    events_before = [event["event_type"] for event in store.trace_events("sess_nokw")]
                    mem_before = [
                        memory
                        for memory in store.list_memories(project_id=project.id, include_global=False, status=None)
                        if memory.source_kind == "isolated_memory_judge"
                    ]
                # Not recorded or judged inline.
                self.assertNotIn("memory_intent_observed", events_before)
                self.assertNotIn("memory_source_observed", events_before)
                self.assertNotIn("memory_judged", events_before)
                self.assertEqual(mem_before, [])
                # Stop runs the judge and stores it.
                self.assertEqual(_stop_for(payload), 0)
                with Store() as store:
                    events_after = [event["event_type"] for event in store.trace_events("sess_nokw")]
                    mem_after = [
                        memory
                        for memory in store.list_memories(project_id=project.id, include_global=False, status=None)
                        if memory.source_kind == "isolated_memory_judge"
                    ]
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture
            self.assertIn("memory_judged", events_after)
            self.assertIn("memory_source_observed", events_after)
            self.assertTrue(any("리프레시 토큰" in memory.content for memory in mem_after))

    def test_trivial_task_prompt_stores_no_durable_memory(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            fixture = json.dumps(
                {
                    "should_store": False,
                    "memory_content": "n/a",
                    "source_quote": "이 함수 typo 좀 고쳐줘",
                    "memory_type": "fact",
                    "enforcement": "none",
                    "activation": "rejected",
                    "candidate_paths": [],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "One-off task; nothing durable to remember.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                payload = {"session_id": "sess_trivial", "cwd": str(project_dir), "prompt": "이 함수 typo 좀 고쳐줘"}
                with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
                self.assertEqual(_stop_for(payload), 0)
                project = detect_project()
                with Store() as store:
                    events = [event["event_type"] for event in store.trace_events("sess_trivial")]
                    mem = [
                        memory
                        for memory in store.list_memories(project_id=project.id, include_global=False, status=None)
                        if memory.source_kind == "isolated_memory_judge"
                    ]
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture
            self.assertIn("memory_source_observed", events)  # recorded at Stop
            self.assertIn("memory_judged", events)  # judged at Stop
            self.assertEqual(mem, [])  # but nothing durable stored (should_store=false)

    def test_typo_directive_is_judged_to_active_source_language_memory(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            protected.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "리프레시 토큰 관련 변경 전 사용자에게 먼저 확인한다.",
                    "source_quote": "리프레쉬 토큰 쪽은 담부터 고치기 전에 꼭 나한테 먼저 말해줘",
                    "memory_type": "directive",
                    "enforcement": "warn",
                    "activation": "active",
                    "candidate_paths": ["src/auth/refresh-token-policy.ts"],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "User asked for confirmation before refresh token changes.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture

            try:
                payload = {
                    "session_id": "sess_typo_warn_policy",
                    "cwd": str(project_dir),
                    "prompt": "리프레쉬 토큰 쪽은 담부터 고치기 전에 꼭 나한테 먼저 말해줘",
                }
                stdin = StringIO(json.dumps(payload))
                stdout = StringIO()
                with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
                    code = main(["hook", "user-prompt-submit"])
                self.assertEqual(code, 0)
                _stop_for(payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            project = detect_project()
            with Store() as store:
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                traces = store.trace_events("sess_typo_warn_policy")
            policy_text = (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8")
            self.assertNotIn("src/auth/refresh-token-policy.ts", policy_text)
            self.assertTrue(
                any(
                    memory.status == "active"
                    and memory.enforcement == "warn"
                    and memory.content == "리프레시 토큰 관련 변경 전 사용자에게 먼저 확인한다."
                    for memory in memories
                )
            )
            self.assertTrue(any(event["event_type"] == "memory_judged" for event in traces))

    def test_mixed_prompt_stores_only_durable_memory_content(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            protected.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정한다.",
                    "source_quote": "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.",
                    "memory_type": "directive",
                    "enforcement": "warn",
                    "activation": "active",
                    "candidate_paths": ["src/auth/refresh-token-policy.ts"],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "Durable refresh token edit gate separated from the current response-format instruction.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                prompt_payload = {
                    "session_id": "sess_mixed_prompt",
                    "cwd": str(project_dir),
                    "prompt": "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.",
                }
                with patch("sys.stdin", StringIO(json.dumps(prompt_payload))), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
                _stop_for(prompt_payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            project = detect_project()
            with Store() as store:
                memories = [
                    memory
                    for memory in store.list_memories(project_id=project.id, include_global=False, status=None)
                    if memory.source_kind == "isolated_memory_judge"
                ]
                lifecycle_events = [dict(event) for event in store.lifecycle_events("sess_mixed_prompt")]
            self.assertEqual(len(memories), 1)
            self.assertEqual(memories[0].content, "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정한다.")
            self.assertNotIn("응답은 OK만 해", memories[0].content)
            self.assertNotIn("응답은 OK만 해", memories[0].reason)
            self.assertTrue(
                any(
                    "응답은 OK만 해" in json.loads(event["candidate_json"])["source_quote"]
                    for event in lifecycle_events
                )
            )
            with Store() as store:
                transient_results = store.search_memories("응답은 OK만 해", project_id=project.id)
                broad_transient_results = store.search_memories("응답", project_id=project.id)
            self.assertFalse(any(memory.id == memories[0].id for memory in transient_results))
            self.assertFalse(any(memory.id == memories[0].id for memory in broad_transient_results))

            retrieval_payload = {
                "session_id": "sess_mixed_prompt_retrieval",
                "cwd": str(project_dir),
                "prompt": "리프레시 토큰 15분으로 변경해줘",
            }
            retrieval_out = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(retrieval_payload))), patch("sys.stdout", retrieval_out):
                self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
            context = json.loads(retrieval_out.getvalue())["hookSpecificOutput"]["additionalContext"]
            self.assertIn("앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정한다.", context)
            self.assertNotIn("응답은 OK만 해", context)

    def test_english_mixed_prompt_excludes_transient_response_text_from_search(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "Ask before changing refresh token settings.",
                    "source_quote": "Always ask before changing refresh token settings. Reply only OK.",
                    "memory_type": "directive",
                    "enforcement": "warn",
                    "activation": "active",
                    "candidate_paths": ["src/auth/refresh-token-policy.ts"],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "Durable edit gate separated from a current-turn response format.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                payload = {
                    "session_id": "sess_english_mixed_prompt",
                    "cwd": str(project_dir),
                    "prompt": "Always ask before changing refresh token settings. Reply only OK.",
                }
                with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
                _stop_for(payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            project = detect_project()
            with Store() as store:
                memories = [
                    memory
                    for memory in store.list_memories(project_id=project.id, include_global=False, status=None)
                    if memory.source_kind == "isolated_memory_judge"
                ]
                transient_results = store.search_memories("Reply only OK", project_id=project.id)
            self.assertEqual(len(memories), 1)
            self.assertEqual(memories[0].content, "Ask before changing refresh token settings.")
            self.assertFalse(any(memory.id == memories[0].id for memory in transient_results))

    def test_initialized_codex_fixture_judge_stores_source_language_memory(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            protected.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "Do not change refresh token policy without asking first.",
                    "source_quote": "refresh token policy 변경은 묻지 않고 하지마",
                    "memory_type": "directive",
                    "enforcement": "block",
                    "activation": "active",
                    "candidate_paths": ["src/auth/refresh-token-policy.ts"],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "Explicit future change gate.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                payload = {
                    "session_id": "sess_codex_fixture",
                    "cwd": str(project_dir),
                    "prompt": "refresh token policy 변경은 묻지 않고 하지마",
                }
                stdin = StringIO(json.dumps(payload))
                with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                    code = main(["hook", "user-prompt-submit"])
                self.assertEqual(code, 0)
                _stop_for(payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            project = detect_project()
            with Store() as store:
                traces = store.trace_events("sess_codex_fixture")
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
            self.assertTrue(
                any(
                    memory.status == "active"
                    and memory.enforcement == "block"
                    and memory.content == "Do not change refresh token policy without asking first."
                    for memory in memories
                )
            )
            judged = [event for event in traces if event["event_type"] == "memory_judged"]
            self.assertEqual(len(judged), 1)
            self.assertIn('"adapter_name": "fixture"', judged[0]["input_json"])

    def test_medium_risk_judge_candidate_does_not_mutate_policy(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "Maybe mention refresh token changes later.",
                    "source_quote": "앞으로 refresh token changes tell me later",
                    "memory_type": "directive",
                    "enforcement": "warn",
                    "activation": "candidate",
                    "candidate_paths": ["src/auth/refresh-token-policy.ts"],
                    "meaning_preserved": True,
                    "contamination_risk": "medium",
                    "reason": "Ambiguous but potentially useful.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                payload = {"session_id": "sess_low_conf", "cwd": str(project_dir), "prompt": "앞으로 refresh token changes tell me later"}
                stdin = StringIO(json.dumps(payload))
                with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                    code = main(["hook", "user-prompt-submit"])
                self.assertEqual(code, 0)
                _stop_for(payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture
            project = detect_project()
            with Store() as store:
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
            self.assertTrue(any(memory.status == "candidate" for memory in memories))
            self.assertNotIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"),
            )

    def test_external_judge_candidate_paths_do_not_compile_policy(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "Do not change external path.",
                    "source_quote": "외부 경로 건드리지마",
                    "memory_type": "directive",
                    "enforcement": "block",
                    "activation": "active",
                    "candidate_paths": ["../outside.txt", "/etc/passwd"],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "External paths must not be normalized into project policy.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                payload = {"session_id": "sess_external_path", "cwd": str(project_dir), "prompt": "외부 경로 건드리지마"}
                stdin = StringIO(json.dumps(payload))
                with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                    code = main(["hook", "user-prompt-submit"])
                self.assertEqual(code, 0)
                _stop_for(payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            project = detect_project()
            with Store() as store:
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
            self.assertTrue(any(memory.status == "active" and not memory.paths for memory in memories))
            self.assertNotIn("outside.txt", (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"))
            self.assertNotIn("/etc/passwd", (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"))

    def test_weak_single_term_path_match_does_not_compile_policy(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            target = project_dir / "src" / "auth" / "session.py"
            target.parent.mkdir(parents=True)
            target.write_text("SESSION = True\n", encoding="utf-8")
            fixture = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "Tell me before auth changes.",
                    "source_quote": "auth 변경 전에 알려줘",
                    "memory_type": "directive",
                    "enforcement": "warn",
                    "activation": "candidate",
                    "candidate_paths": [],
                    "meaning_preserved": True,
                    "contamination_risk": "medium",
                    "reason": "No reliable path was provided.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                payload = {"session_id": "sess_weak_path", "cwd": str(project_dir), "prompt": "auth 변경 전에 알려줘"}
                stdin = StringIO(json.dumps(payload))
                with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                    code = main(["hook", "user-prompt-submit"])
                self.assertEqual(code, 0)
                _stop_for(payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            self.assertNotIn(
                "src/auth/session.py",
                (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"),
            )

    def test_invalid_judge_output_does_not_mutate_policy(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            fixture = json.dumps(
                {
                    "is_directive": "false",
                    "intent": "none",
                    "subject": "refresh token policy",
                    "enforcement": "block",
                    "scope_terms": ["refresh", "token"],
                    "candidate_paths": ["src/auth/refresh-token-policy.ts"],
                    "confidence": 0.99,
                    "rationale": "malformed boolean should be rejected",
                    "normalized_prompt": "앞으로 refresh token policy 관련 내용은 기억해줘",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                payload = {
                    "session_id": "sess_invalid_interpreter",
                    "cwd": str(project_dir),
                    "prompt": "앞으로 refresh token policy 관련 내용은 기억해줘",
                }
                stdin = StringIO(json.dumps(payload))
                with patch("sys.stdin", stdin), patch("sys.stdout", StringIO()):
                    code = main(["hook", "user-prompt-submit"])
                self.assertEqual(code, 0)
                # Invalid fixture output triggers retry logic; drive enough stops to reach
                # MAX_JUDGE_ATTEMPTS so the give-up terminal memory_judged event is recorded.
                from memassist.memory_judge import MAX_JUDGE_ATTEMPTS
                for _ in range(MAX_JUDGE_ATTEMPTS):
                    _stop_for(payload)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            project = detect_project()
            with Store() as store:
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                traces = store.trace_events("sess_invalid_interpreter")
            self.assertFalse(any(memory.source_kind == "isolated_memory_judge" for memory in memories))
            self.assertNotIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "policy.yaml").read_text(encoding="utf-8"),
            )
            judged = [event for event in traces if event["event_type"] == "memory_judged"]
            self.assertEqual(len(judged), 1)
            self.assertIn("invalid judge output", judged[0]["input_json"])

    def test_interpreter_recursion_guard_noops_hook(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            old_active = os.environ.get(INTERPRETER_ACTIVE_ENV)
            os.environ[INTERPRETER_ACTIVE_ENV] = "1"
            try:
                payload = {"session_id": "sess_guard", "cwd": str(project_dir), "prompt": "remember this"}
                stdin = StringIO(json.dumps(payload))
                stdout = StringIO()
                with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
                    code = main(["hook", "user-prompt-submit"])
            finally:
                if old_active is None:
                    os.environ.pop(INTERPRETER_ACTIVE_ENV, None)
                else:
                    os.environ[INTERPRETER_ACTIVE_ENV] = old_active
            self.assertEqual(code, 0)
            self.assertEqual(stdout.getvalue(), "")

    def test_memory_drafts_activate_deactivate_and_cleanup(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
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
                store.update_status(draft_id, "candidate")
            self.assertTrue((project_dir / ".memassist" / "memories" / "candidates" / f"{draft_id}.md").exists())

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["memory", "pending"])
            self.assertEqual(code, 0)
            self.assertIn("Draft fact", out.getvalue())

            with patch("sys.stdout", StringIO()):
                main(["memory", "activate", draft_id])
            with Store() as store:
                self.assertEqual(store.get_memory(draft_id).status, "active")  # type: ignore[union-attr]
            self.assertTrue((project_dir / ".memassist" / "memories" / "active" / f"{draft_id}.md").exists())
            self.assertFalse((project_dir / ".memassist" / "memories" / "candidates" / f"{draft_id}.md").exists())

            with patch("sys.stdout", StringIO()):
                main(["memory", "deactivate", draft_id])
            with Store() as store:
                self.assertEqual(store.get_memory(draft_id).status, "archived")  # type: ignore[union-attr]
            self.assertTrue((project_dir / ".memassist" / "memories" / "archived" / f"{draft_id}.md").exists())

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
                self.assertEqual(store.get_memory(expired_id).status, "archived")  # type: ignore[union-attr]

    def test_lesson_from_session_does_not_promote_memory_to_policy(self) -> None:
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
                        "Block refresh token policy edits by autonomous memory policy.",
                    ]
                )
            self.assertEqual(code, 0)
            lesson_id = lesson_out.getvalue().strip()
            self.assertTrue(lesson_id.startswith("mem_"))

            with Store() as store:
                memory = store.get_memory(lesson_id)
                self.assertEqual(memory.status, "candidate")  # type: ignore[union-attr]
            self.assertNotIn("src/auth/refresh-token-policy.ts", (project_dir / ".memassist" / "policy.yaml").read_text())

            eval_out = StringIO()
            with patch("sys.stdout", eval_out):
                code = main(["eval", "run", "--session", "sess_eval", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(eval_out.getvalue())
            self.assertTrue(result["passed"])
            self.assertGreaterEqual(result["candidate_count"], 1)

            with patch("sys.stdout", StringIO()):
                code = main(["memory", "deactivate", lesson_id])
            self.assertEqual(code, 0)
            with Store() as store:
                memory = store.get_memory(lesson_id)
                self.assertEqual(memory.status, "archived")  # type: ignore[union-attr]
            self.assertNotIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "policy.yaml").read_text(),
            )

    def test_memory_deactivate_does_not_remove_manual_sensitive_path(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            policy_path = project_dir / ".memassist" / "policy.yaml"
            policy_path.write_text(
                '# memassist project policy\nsensitive_paths:\n  - "src/auth/session.py"\nprotected_paths: []\ndangerous_commands: []\nverification_commands: []\n',
                encoding="utf-8",
            )
            with Store() as store:
                store.upsert_project(project)
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content="Warn before changing src/auth/session.py.",
                    paths=["src/auth/session.py"],
                    status="active",
                    enforcement="warn",
                    source_kind="test",
                )

            with patch("sys.stdout", StringIO()):
                code = main(["memory", "deactivate", memory_id])
            self.assertEqual(code, 0)
            policy_text = (project_dir / ".memassist" / "policy.yaml").read_text()
            self.assertIn("src/auth/session.py", policy_text)
            with Store() as store:
                memory = store.get_memory(memory_id)
                self.assertEqual(memory.status, "archived")  # type: ignore[union-attr]

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
                        "Block billing migration edits by autonomous memory policy.",
                        "--tag",
                        "billing",
                        "--importance",
                        "0.9",
                        "--enforcement",
                        "block",
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
            self.assertEqual(imported[0]["status"], "candidate")
            self.assertIn("billing migration", imported[0]["content"])

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
            self.assertGreaterEqual(len(result["lifecycle"]["active"]), 1)

            with Store() as store:
                self.assertEqual(store.get_memory(expired_id).status, "archived")  # type: ignore[union-attr]

    def test_lifecycle_reinforces_duplicate_active_memory_without_status_promotion(self) -> None:
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
                    status="active",
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
                self.assertEqual(memory.status, "active")  # type: ignore[union-attr]

    def test_lifecycle_suppresses_directive_paraphrase_candidate(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="앞으로 리프레시토큰 변경은 묻지 않고 하지마",
                    tags=["explicit", "user_prompt", "auth", "token", "refresh", "policy"],
                    status="active",
                    importance=0.9,
                    confidence=0.95,
                    enforcement="block",
                    source_kind="user_prompt_directive",
                )
                store.add_trace_event(
                    session_id="sess_paraphrase",
                    project_id=project.id,
                    event_type="stop",
                    tool_name=None,
                    input_json={
                        "last_assistant_message": "앞으로 리프레시 토큰 관련 변경은 먼저 확인하고 진행하겠습니다."
                    },
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["daemon", "once", "--session", "sess_paraphrase", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result["lifecycle"]["duplicates"], 0)
            self.assertEqual(result["lifecycle"]["stored"], [])
            with Store() as store:
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                self.assertEqual([memory.id for memory in memories if memory.status == "candidate"], [])
                self.assertEqual(store.get_memory(memory_id).status, "active")  # type: ignore[union-attr]

    def test_judge_failure_is_retried_not_dropped(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = "{}"
            try:
                payload = {
                    "session_id": "sess_retry_not_dropped",
                    "cwd": str(project_dir),
                    "prompt": "isolated_memory_judge retry test",
                }
                with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
                _stop_for(payload)
                project = detect_project()
                with Store() as store:
                    events = [dict(event) for event in store.trace_events("sess_retry_not_dropped")]
                    memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                event_types = [event["event_type"] for event in events]
                self.assertIn("memory_judge_failed", event_types)
                self.assertNotIn("memory_judged", event_types)
                self.assertFalse(any(memory.source_kind == "isolated_memory_judge" for memory in memories))
                # Second stop retries the failed source, not skips it.
                _stop_for(payload)
                with Store() as store:
                    events2 = [dict(event) for event in store.trace_events("sess_retry_not_dropped")]
                failed_events = [e for e in events2 if e["event_type"] == "memory_judge_failed"]
                self.assertEqual(len(failed_events), 2)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

    def test_judge_failure_gives_up_after_max_attempts(self) -> None:
        from memassist.memory_judge import MAX_JUDGE_ATTEMPTS

        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = "{}"
            try:
                payload = {
                    "session_id": "sess_give_up",
                    "cwd": str(project_dir),
                    "prompt": "isolated_memory_judge give up test",
                }
                with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
                for _ in range(MAX_JUDGE_ATTEMPTS):
                    _stop_for(payload)
                project = detect_project()
                with Store() as store:
                    events = [dict(event) for event in store.trace_events("sess_give_up")]
                    memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                event_types = [event["event_type"] for event in events]
                self.assertIn("memory_judged", event_types)
                self.assertFalse(any(memory.source_kind == "isolated_memory_judge" for memory in memories))
                # One more stop: source is already processed (give-up is terminal), no new failures.
                failed_before = sum(1 for e in events if e["event_type"] == "memory_judge_failed")
                _stop_for(payload)
                with Store() as store:
                    events3 = [dict(event) for event in store.trace_events("sess_give_up")]
                failed_after = sum(1 for e in events3 if e["event_type"] == "memory_judge_failed")
                self.assertEqual(failed_before, failed_after)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

    def test_genuine_skip_is_terminal_not_retried(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            fixture = json.dumps(
                {
                    "should_store": False,
                    "memory_content": "n/a",
                    "source_quote": "isolated skip terminal test",
                    "memory_type": "fact",
                    "enforcement": "none",
                    "activation": "rejected",
                    "candidate_paths": [],
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "One-off task; nothing durable.",
                }
            )
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = fixture
            try:
                payload = {
                    "session_id": "sess_skip_terminal",
                    "cwd": str(project_dir),
                    "prompt": "isolated skip terminal test",
                }
                with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
                _stop_for(payload)
                project = detect_project()
                with Store() as store:
                    events = [dict(event) for event in store.trace_events("sess_skip_terminal")]
                    memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                judged = [e for e in events if e["event_type"] == "memory_judged"]
                failed = [e for e in events if e["event_type"] == "memory_judge_failed"]
                self.assertEqual(len(judged), 1)
                self.assertEqual(len(failed), 0)
                self.assertFalse(any(memory.source_kind == "isolated_memory_judge" for memory in memories))
                # Second stop: source already processed; no new failed events.
                _stop_for(payload)
                with Store() as store:
                    events2 = [dict(event) for event in store.trace_events("sess_skip_terminal")]
                failed2 = [e for e in events2 if e["event_type"] == "memory_judge_failed"]
                self.assertEqual(len(failed2), 0)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

    def test_judge_subprocess_env_is_de_nested(self) -> None:
        from memassist.memory_judge import (
            INTERPRETER_ACTIVE_ENV,
            JUDGE_ACTIVE_ENV,
            _judge_subprocess_env,
        )

        with patch.dict(
            os.environ,
            {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "abc", "CLAUDE_CODE_ENTRYPOINT": "cli"},
        ):
            result = _judge_subprocess_env()
        self.assertNotIn("CLAUDECODE", result)
        self.assertFalse(any(key.startswith("CLAUDE_CODE_") for key in result))
        self.assertEqual(result.get(JUDGE_ACTIVE_ENV), "1")
        self.assertEqual(result.get(INTERPRETER_ACTIVE_ENV), "1")

    def test_cleanup_supersedes_existing_directive_paraphrase_candidate(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="앞으로 리프레시토큰 변경은 묻지 않고 하지마",
                    tags=["explicit", "user_prompt", "auth", "token", "refresh", "policy"],
                    status="active",
                    importance=0.9,
                    confidence=0.95,
                    enforcement="block",
                    source_kind="user_prompt_directive",
                )
                candidate_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="앞으로 리프레시 토큰 관련 변경은 먼저 확인하고 진행하겠습니다.",
                    tags=["explicit", "preference", "semantic", "high"],
                    status="candidate",
                    importance=0.7,
                    confidence=0.8,
                    enforcement="none",
                    source_kind="lifecycle",
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["daemon", "once", "--session", "missing", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertIn(candidate_id, result["cleanup"]["duplicates"])
            with Store() as store:
                candidate = store.get_memory(candidate_id)
                self.assertEqual(candidate.status, "archived")  # type: ignore[union-attr]
                self.assertEqual(candidate.superseded_by, memory_id)  # type: ignore[union-attr]


class ClaudeMemoryJudgeTest(unittest.TestCase):
    @staticmethod
    def _status(tool: str, installed: bool):
        from memassist.integrations.base import IntegrationStatus

        return IntegrationStatus(
            tool=tool,
            installed=installed,
            path=Path(tmpfile_root() / f"{tool}.json"),
            events=("UserPromptSubmit", "Stop") if installed else (),
            detail="installed" if installed else "not installed",
        )

    @contextmanager
    def _clean_judge_env(self):
        keys = (
            "MEMASSIST_MEMORY_JUDGE_MODE",
            "MEMASSIST_MEMORY_JUDGE_ACTIVE",
            INTERPRETER_ACTIVE_ENV,
            "MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE",
        )
        saved = {key: os.environ.pop(key, None) for key in keys}
        try:
            yield
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def _select(self, installed_tools: set[str]):
        from memassist import memory_judge

        statuses = [self._status(tool, tool in installed_tools) for tool in ("codex", "claude", "opencode")]
        with patch.object(memory_judge, "status_tools", return_value=statuses):
            return memory_judge.select_memory_judge(object())

    def test_select_returns_claude_when_only_claude_installed(self) -> None:
        from memassist.memory_judge import ClaudeMemoryJudge

        with self._clean_judge_env():
            judge = self._select({"claude"})
        self.assertIsInstance(judge, ClaudeMemoryJudge)

    def test_select_prefers_codex_when_both_installed(self) -> None:
        from memassist.memory_judge import CodexMemoryJudge

        with self._clean_judge_env():
            judge = self._select({"codex", "claude"})
        self.assertIsInstance(judge, CodexMemoryJudge)

    def test_select_unavailable_when_no_judge_tool_installed(self) -> None:
        from memassist.memory_judge import UnavailableMemoryJudge

        with self._clean_judge_env():
            judge = self._select({"opencode"})
        self.assertIsInstance(judge, UnavailableMemoryJudge)

    def test_recursion_guard_blocks_selection(self) -> None:
        from memassist.memory_judge import UnavailableMemoryJudge

        with self._clean_judge_env():
            os.environ[INTERPRETER_ACTIVE_ENV] = "1"
            judge = self._select({"claude"})
        self.assertIsInstance(judge, UnavailableMemoryJudge)

    def test_claude_command_shape_and_stdin(self) -> None:
        import subprocess

        from memassist.memory_judge import ClaudeMemoryJudge

        judge = ClaudeMemoryJudge()
        self.assertEqual(judge.name, "claude")
        self.assertEqual(judge.executable, "claude")
        self.assertEqual(judge._stdin(), subprocess.DEVNULL)
        command = judge._command("INSTRUCTION", project=object())
        self.assertEqual(command[:2], ["claude", "-p"])
        self.assertIn("--output-format", command)
        self.assertEqual(command[command.index("--output-format") + 1], "json")
        # instruction stays last so _debug_command masks it
        self.assertEqual(command[-1], "INSTRUCTION")

    def test_parses_judge_json_from_claude_result_envelope(self) -> None:
        from memassist.memory_judge import _candidate_from_output

        inner = {
            "should_store": True,
            "memory_content": "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정한다",
            "source_quote": "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.",
            "memory_type": "rule",
            "enforcement": "block",
            "activation": "active",
            "candidate_paths": [],
            "meaning_preserved": True,
            "contamination_risk": "low",
            "reason": "durable directive",
        }
        envelope = json.dumps({"type": "result", "subtype": "success", "result": json.dumps(inner, ensure_ascii=False)})
        candidate = _candidate_from_output(envelope)
        self.assertTrue(candidate.should_store)
        self.assertEqual(candidate.memory_type, "rule")
        # durable/transient separation: the one-shot instruction is not in memory_content
        self.assertNotIn("응답은 OK만 해", candidate.memory_content)
        self.assertIn("리프레시 토큰", candidate.memory_content)

    def test_parses_fenced_json_from_result_envelope(self) -> None:
        from memassist.memory_judge import _candidate_from_output

        inner = {
            "should_store": True,
            "memory_content": "리프레시 토큰 변경 전 사용자 확인을 받는다",
            "source_quote": "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.",
            "memory_type": "rule",
            "enforcement": "block",
            "activation": "active",
            "candidate_paths": [],
            "meaning_preserved": True,
            "contamination_risk": "low",
            "reason": "durable directive; one-shot response instruction excluded",
        }
        fenced = "```json\n" + json.dumps(inner, ensure_ascii=False) + "\n```"
        envelope = json.dumps({"type": "result", "subtype": "success", "result": fenced})
        candidate = _candidate_from_output(envelope)
        self.assertTrue(candidate.should_store)
        self.assertEqual(candidate.memory_type, "rule")
        self.assertNotIn("응답은 OK", candidate.memory_content)
        self.assertIn("리프레시 토큰", candidate.memory_content)

    def test_claude_judge_uses_configurable_low_cost_model(self) -> None:
        from memassist.memory_judge import ClaudeMemoryJudge

        judge = ClaudeMemoryJudge()
        default_cmd = judge._command("INSTR", project=object())
        self.assertIn("--model", default_cmd)
        self.assertEqual(default_cmd[default_cmd.index("--model") + 1], "haiku")
        self.assertEqual(default_cmd[-1], "INSTR")  # instruction stays last
        with patch.dict(os.environ, {"MEMASSIST_MEMORY_JUDGE_MODEL": "sonnet"}):
            override_cmd = judge._command("INSTR", project=object())
        self.assertEqual(override_cmd[override_cmd.index("--model") + 1], "sonnet")

    def test_parser_still_handles_raw_and_codex_jsonl(self) -> None:
        from memassist.memory_judge import _candidate_from_output

        raw = {
            "should_store": False,
            "memory_content": "x",
            "source_quote": "y",
            "memory_type": "fact",
            "enforcement": "none",
            "activation": "rejected",
            "candidate_paths": [],
            "meaning_preserved": True,
            "contamination_risk": "low",
            "reason": "r",
        }
        self.assertFalse(_candidate_from_output(json.dumps(raw)).should_store)
        jsonl = json.dumps({"type": "item", "item": {"text": json.dumps(raw, ensure_ascii=False)}})
        self.assertEqual(_candidate_from_output(jsonl).memory_type, "fact")


def tmpfile_root() -> Path:
    return Path(tempfile.gettempdir())


def _replace_artifact_metadata_status(path: Path, status: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index("<!-- memassist\n") + len("<!-- memassist\n")
    end = text.index("\n-->", start)
    metadata = json.loads(text[start:end])
    metadata["status"] = status
    path.write_text(
        text[:start] + json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + text[end:],
        encoding="utf-8",
    )


def _insert_legacy_sqlite_only_memory(store: Store, *, project_id: str, status: str) -> str:
    memory_id = f"mem_legacy_{status}"
    ts = now_iso()
    memory = Memory(
        id=memory_id,
        scope_type="project",
        project_id=project_id,
        session_id=None,
        type="fact",
        content=f"legacy sqlite status sentinel {status}",
        reason=None,
        tags=["legacy", status],
        paths=[],
        status=status,
        importance=0.9,
        confidence=0.9,
        strength=0.9,
        recurrence=1,
        retrieval_count=0,
        utility=0.0,
        half_life_days=30.0,
        enforcement="none",
        source_kind="legacy_sqlite_row",
        source_ref=None,
        created_at=ts,
        updated_at=ts,
        last_used_at=None,
        expires_at=None,
        superseded_by=None,
    )
    store._upsert_memory_index(memory)
    store.conn.commit()
    return memory_id


if __name__ == "__main__":
    unittest.main()
