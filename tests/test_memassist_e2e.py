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
from memassist.storage import Store


@contextmanager
def temp_project():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "home"
        project = root / "project"
        project.mkdir()
        old_cwd = Path.cwd()
        old_home = os.environ.get("MEMASSIST_HOME")
        old_codex = os.environ.get("CODEX_HOME")
        old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
        os.environ["MEMASSIST_HOME"] = str(home)
        os.environ["CODEX_HOME"] = str(root / "codex-home")
        os.chdir(project)
        try:
            yield root, project
        finally:
            os.chdir(old_cwd)
            _restore_env("MEMASSIST_HOME", old_home)
            _restore_env("CODEX_HOME", old_codex)
            _restore_env("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", old_fixture)


def _restore_env(key: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


class MemassistTempProjectE2ETest(unittest.TestCase):
    def test_init_to_judged_candidate_activation_and_pretool_block(self) -> None:
        with temp_project() as (_root, project):
            target = project / "src" / "auth" / "refresh-token-policy.ts"
            target.parent.mkdir(parents=True)
            target.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            self.assertEqual(main(["init", "--tools", "codex", "--mode", "full"]), 0)
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = json.dumps(
                {
                    "should_store": True,
                    "memory_content": "Do not change refresh token policy without asking first.",
                    "source_quote": "리프레쉬 토큰 정책은 담부터 묻지 않고 고치지마",
                    "memory_type": "directive",
                    "enforcement": "block",
                    "activation": "active",
                    "candidate_paths": ["src/auth/refresh-token-policy.ts"],
                    "policy_compile": True,
                    "meaning_preserved": True,
                    "contamination_risk": "low",
                    "reason": "User explicitly forbids changing this area without asking.",
                }
            )

            prompt_payload = {
                "session_id": "sess_e2e",
                "cwd": str(project),
                "prompt": "리프레쉬 토큰 정책은 담부터 묻지 않고 고치지마",
            }
            with patch("sys.stdin", StringIO(json.dumps(prompt_payload))), patch("sys.stdout", StringIO()) as stdout:
                self.assertEqual(main(["hook", "user-prompt-submit"]), 0)
            self.assertEqual(stdout.getvalue(), "")

            with Store() as store:
                project_memories = store.list_memories(project_id=None, include_global=False, status=None)
                judged = [
                    memory.as_dict()
                    for memory in project_memories
                    if memory.source_kind == "isolated_memory_judge"
                ]
            self.assertEqual(len(judged), 1)
            self.assertEqual(judged[0]["status"], "candidate")

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

            with patch("sys.stdout", StringIO()):
                self.assertEqual(main(["memory", "activate", judged[0]["id"]]), 0)

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
            decision = json.loads(pretool_out.getvalue())
            self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

            with Store() as store:
                events = store.trace_events("sess_e2e")
            self.assertTrue(any(event["event_type"] == "memory_judged" for event in events))
            self.assertTrue(any(event["event_type"] == "pre_tool_use" and event["policy_decision"] == "block" for event in events))

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


if __name__ == "__main__":
    unittest.main()
