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
from memassist.hooks import codex_hooks_status, install_codex_hooks, uninstall_codex_hooks
from memassist.policy import PolicyEngine, default_policy_yaml, load_policy
from memassist.project import detect_project
from memassist.retrieval import build_memory_pack
from memassist.storage import Store


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
            warn = engine.check_pre_tool(tool="file_write", args={"path": ".env"})
            self.assertEqual(warn.action, "warn")

    def test_codex_hook_install_status_uninstall(self) -> None:
        with isolated_env() as (_root, project, _home):
            path = install_codex_hooks(project_root=project)
            self.assertTrue(path.exists())
            self.assertEqual(path, project / ".codex" / "hooks.json")
            status = codex_hooks_status(project_root=project)
            self.assertTrue(status["installed"])
            self.assertEqual(status["scope"], "project")
            self.assertIn("PreToolUse", status["events"])
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
            self.assertIn("allow", stdout.getvalue())

            out = StringIO()
            with patch("sys.stdout", out):
                main(["logs", "--json"])
            events = json.loads(out.getvalue())
            self.assertEqual(events[0]["session_id"], "sess_1")
            self.assertEqual(events[0]["event_type"], "pre_tool_use")


if __name__ == "__main__":
    unittest.main()
