from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from memassist.cli import main
from memassist.memory_judge import judge_backend_diagnostics
from memassist.project import detect_project
from memassist.storage import Store


os.environ.setdefault("MEMASSIST_INIT_SKIP_EMBEDDING_INSTALL", "1")


def judge_fixture(
    content: str,
    *,
    source_quote: str | None = None,
    memory_type: str = "directive",
    source_integrity: str = "clean",
    reason: str = "test fixture",
) -> str:
    return json.dumps(
        {
            "memory": {
                "content": content,
                "type": memory_type,
                "source_quote": source_quote or content,
            },
            "source_integrity": source_integrity,
            "reason": reason,
        },
        ensure_ascii=False,
    )


@contextmanager
def temp_project():
    # ignore_cleanup_errors: on Windows a just-finished tool subprocess (e.g. the
    # real claude/codex judge) can briefly hold the project dir, making rmdir raise
    # PermissionError during teardown. That must not mask the test's own result.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        home = root / "home"
        project = root / "project"
        project.mkdir()
        home.mkdir()
        old_cwd = Path.cwd()
        old_home = os.environ.get("MEMASSIST_HOME")
        old_user_home = os.environ.get("HOME")
        old_codex = os.environ.get("CODEX_HOME")
        old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
        os.environ.pop("MEMASSIST_HOME", None)
        os.environ["HOME"] = str(home)
        os.environ["CODEX_HOME"] = str(root / "codex-home")
        os.chdir(project)
        try:
            yield root, project
        finally:
            os.chdir(old_cwd)
            _restore_env("MEMASSIST_HOME", old_home)
            _restore_env("HOME", old_user_home)
            _restore_env("CODEX_HOME", old_codex)
            _restore_env("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", old_fixture)


def _restore_env(key: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


class MemassistTempProjectE2ETest(unittest.TestCase):
    def test_init_to_judged_memory_retrieval_without_pretool_block(self) -> None:
        with temp_project() as (_root, project):
            target = project / "src" / "auth" / "refresh-token-policy.ts"
            target.parent.mkdir(parents=True)
            target.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            self.assertEqual(main(["init", "--tools", "codex", "--mode", "full"]), 0)
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = judge_fixture(
                "리프레시 토큰 정책은 변경 전에 먼저 확인한다.",
                source_quote="리프레쉬 토큰 정책은 담부터 묻지 않고 고치지마",
                memory_type="directive",
                source_integrity="clean",
                reason="User explicitly forbids changing this area without asking.",
            )

            prompt_payload = {
                "session_id": "sess_e2e",
                "cwd": str(project),
                "prompt": "리프레쉬 토큰 정책은 담부터 묻지 않고 고치지마",
            }
            with patch("sys.stdin", StringIO(json.dumps(prompt_payload))), patch("sys.stdout", StringIO()):
                self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
            # WRITE happens at turn end (Stop); the creating prompt cannot inject memory
            # it has not stored yet. Next-turn injection is verified via rag_payload below.
            with patch(
                "sys.stdin",
                StringIO(
                    json.dumps(
                        {
                            "session_id": "sess_e2e",
                            "cwd": str(project),
                            "prompt": prompt_payload["prompt"],
                        }
                    )
                ),
            ), patch("sys.stdout", StringIO()):
                self.assertEqual(main(["hook", "stop"]), 0)

            with Store() as store:
                project_memories = store.list_memories(project_id=None, include_global=False, status=None)
                judged = [
                    memory.as_dict()
                    for memory in project_memories
                    if memory.source_kind == "isolated_memory_judge"
                ]
            self.assertEqual(len(judged), 1)
            self.assertEqual(judged[0]["status"], "active")
            self.assertEqual(judged[0]["content"], "리프레시 토큰 정책은 변경 전에 먼저 확인한다.")
            self.assertEqual(judged[0]["paths"], [])

            pretool_payload = {
                "session_id": "sess_e2e",
                "cwd": str(project),
                "toolName": "apply_patch",
                "toolArgs": {
                    "command": "*** Begin Patch\n*** Update File: src/auth/refresh-token-policy.ts\n*** End Patch\n"
                },
            }
            pretool_out = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(pretool_payload))), patch("sys.stdout", pretool_out):
                self.assertEqual(main(["hook", "pre-tool-use"]), 0)
            self.assertEqual(pretool_out.getvalue(), "")

            pretool_payload = {
                "session_id": "sess_e2e",
                "cwd": str(project),
                "toolName": "apply_patch",
                "toolArgs": {
                    "command": "*** Begin Patch\n*** Update File: src/auth/refresh-token-policy.ts\n*** End Patch\n"
                },
            }
            pretool_out = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(pretool_payload))), patch("sys.stdout", pretool_out):
                self.assertEqual(main(["hook", "pre-tool-use"]), 0)
            self.assertEqual(pretool_out.getvalue(), "")

            rag_payload = {
                "session_id": "sess_e2e",
                "cwd": str(project),
                "prompt": "리프레시 토큰 15분으로 변경해줘",
            }
            rag_out = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(rag_payload))), patch("sys.stdout", rag_out):
                self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
            injected = json.loads(rag_out.getvalue())
            context = injected["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Relevant memassist memory:", context)
            self.assertIn("리프레시 토큰 정책은 변경 전에 먼저 확인한다.", context)
            self.assertNotIn("Policy reminders", context)

            with Store() as store:
                events = store.trace_events("sess_e2e")
            self.assertTrue(any(event["event_type"] == "memory_judged" for event in events))
            self.assertTrue(any(event["event_type"] == "pre_tool_use" for event in events))

    @unittest.skipUnless(os.environ.get("MEMASSIST_RUN_REAL_CODEX_E2E") == "1", "real Codex CLI E2E is opt-in")
    def test_real_codex_cli_mode_reports_hook_lifecycle(self) -> None:
        if not shutil.which("codex"):
            self.skipTest("codex executable not found")
        with temp_project() as (_root, project):
            self.assertEqual(main(["init", "--tools", "codex", "--mode", "full"]), 0)
            result = subprocess.run(
                [
                    "codex",
                    "exec",
                    "--cd",
                    str(project),
                    "--dangerously-bypass-hook-trust",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "Use the shell tool to run exactly: echo MEMASSIST_REAL_CODEX_E2E",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            with Store() as store:
                events = store.trace_events(project_id=None, limit=20)
            event_types = {str(event["event_type"]) for event in events}
            self.assertTrue(
                {"memory_injected", "pre_tool_use"} & event_types,
                "real Codex CLI command completed, but no memassist hook lifecycle events were captured",
            )

    # --- Tool-parametrized durable/transient separation E2E (tasks 3.1/3.2) ----
    #
    # The judge backend is selected from the initialized tool (select_memory_judge:
    # codex > claude; opencode has no judge adapter). Rather than hard-coding one host
    # CLI, these exercise the judge layer for the tool used at `init` by firing the
    # hooks directly. The real-judge cases are opt-in and require the tool CLI on PATH;
    # the opencode (no-judge) case is deterministic and always runs.

    _MIXED_PROMPT = "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해."
    _TRANSIENT = "응답은 OK만 해"

    def _ingest_mixed_prompt(self, project: Path) -> None:
        """Fire user-prompt-submit then stop for the mixed prompt so the init tool's
        real judge backend runs turn-end judging (no fixture judge)."""
        os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
        submit = {"session_id": "sess_judge_e2e", "cwd": str(project), "prompt": self._MIXED_PROMPT}
        with patch("sys.stdin", StringIO(json.dumps(submit))), patch("sys.stdout", StringIO()):
            self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
        stop = {"session_id": "sess_judge_e2e", "cwd": str(project), "prompt": self._MIXED_PROMPT}
        with patch("sys.stdin", StringIO(json.dumps(stop))), patch("sys.stdout", StringIO()):
            self.assertEqual(main(["hook", "stop"]), 0)

    def _judged_memories(self) -> list:
        with Store() as store:
            return [
                memory
                for memory in store.list_memories(
                    project_id=None, include_global=False, status=None
                )
                if memory.source_kind == "isolated_memory_judge"
            ]

    def _assert_real_judge_excludes_transient(self, tool: str) -> None:
        with temp_project() as (_root, project):
            self.assertEqual(main(["init", "--tools", tool, "--mode", "full"]), 0)
            self._ingest_mixed_prompt(project)

            judged = self._judged_memories()
            # Ingestion (task 3.1) must have happened via the real judge for this tool.
            self.assertTrue(
                judged,
                f"{tool} judge stored no durable memory for the mixed directive prompt",
            )
            # Metric (task 3.2): 0 transient response-format phrases in stored content.
            for memory in judged:
                self.assertNotIn(
                    self._TRANSIENT,
                    memory.content,
                    f"{tool}: transient response-format text leaked into stored durable memory",
                )
            # Transient text is not retrievable, and a relevant follow-up does not inject it.
            judged_project_id = judged[0].project_id
            with Store() as store:
                transient_hits = store.search_memories(self._TRANSIENT, project_id=judged_project_id)
            self.assertFalse(
                any(memory.source_kind == "isolated_memory_judge" for memory in transient_hits),
                f"{tool}: transient response-format text is retrievable as durable memory",
            )
            rag = {
                "session_id": "sess_judge_e2e_rag",
                "cwd": str(project),
                "prompt": "리프레시 토큰 15분으로 변경해줘",
            }
            rag_out = StringIO()
            with patch("sys.stdin", StringIO(json.dumps(rag))), patch("sys.stdout", rag_out):
                self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
            self.assertNotIn(
                self._TRANSIENT,
                rag_out.getvalue(),
                f"{tool}: transient response-format text leaked into injected RAG context",
            )

    @unittest.skipUnless(os.environ.get("MEMASSIST_RUN_REAL_JUDGE_E2E") == "1", "real judge E2E is opt-in")
    def test_real_judge_codex_mixed_prompt_excludes_transient(self) -> None:
        if not shutil.which("codex"):
            self.skipTest("codex executable not found")
        self._assert_real_judge_excludes_transient("codex")

    @unittest.skipUnless(os.environ.get("MEMASSIST_RUN_REAL_JUDGE_E2E") == "1", "real judge E2E is opt-in")
    def test_real_judge_claude_mixed_prompt_excludes_transient(self) -> None:
        if not shutil.which("claude"):
            self.skipTest("claude executable not found")
        self._assert_real_judge_excludes_transient("claude")

    def test_opencode_init_has_no_judge_backend(self) -> None:
        """opencode provides no judge adapter, so init'ing only opencode must leave
        the isolated judge unavailable and store no durable prompt-derived memory
        (spec: 'No initialized judge tool'). Deterministic; requires no tool CLI."""
        with temp_project() as (_root, project):
            self.assertEqual(main(["init", "--tools", "opencode", "--mode", "full"]), 0)
            diagnostics = judge_backend_diagnostics(detect_project())
            self.assertIsNone(diagnostics["backend"])
            self.assertFalse(diagnostics["available"])
            self._ingest_mixed_prompt(project)
            self.assertEqual(
                self._judged_memories(),
                [],
                "opencode has no judge backend; no durable prompt-derived memory should be stored",
            )


if __name__ == "__main__":
    unittest.main()
