from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from memassist.async_ingestion import WorkerSpawnResult, pending_source_count, stop_ingest_mode, worker_lock_path
from memassist.cli import main
from memassist.embedding_profiles import (
    EmbeddingProfile,
    EmbeddingProfileConfig,
    embedding_profiles_path,
    load_embedding_profile_config,
    save_embedding_profile_config,
)
from memassist.embeddings import (
    EmbeddingModelInstallResult,
    Model2VecEmbeddingProvider,
    build_memory_embeddings,
    embedding_model_status,
    install_embedding_model,
    register_embedding_provider,
)
from memassist.evaluator import evaluate_candidate
from memassist.extraction import MemoryCandidate, extract_candidates
from memassist.hooks import codex_hooks_status, install_codex_hooks, uninstall_codex_hooks
from memassist.memory_artifacts import find_memory_artifact
from memassist.memory_judge import (
    INTERPRETER_ACTIVE_ENV,
    build_judge_payload,
    observe_turn_end_memory_source,
    pending_memory_source_events,
)
from memassist.models import MEMORY_STATUSES, Memory
from memassist.verification_config import default_verification_config_yaml, load_verification_config
from memassist.project import detect_project, detect_project_for_init
from memassist.retrieval import analyze_query_intent, build_memory_pack, render_prompt_context
from memassist.source_ledger import load_source_records
from memassist.storage import Store, now_iso
from memassist.trace import extract_files


class _TestEmbeddingProvider:
    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile
        self.dimension = profile.dimension or 8

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for index, token in enumerate(text.lower().split()):
            vector[index % self.dimension] += float(len(token) or 1)
        norm = sum(value * value for value in vector) ** 0.5
        return [round(value / norm, 8) for value in vector] if norm else vector


register_embedding_provider("test", _TestEmbeddingProvider)
os.environ.setdefault("MEMASSIST_INIT_SKIP_EMBEDDING_INSTALL", "1")
os.environ.setdefault("MEMASSIST_STOP_INGEST_MODE", "sync")


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


def no_memory_fixture(*, source_integrity: str = "clean", reject_reason: str = "not_durable", reason: str = "test fixture") -> str:
    return json.dumps(
        {
            "memory": None,
            "source_integrity": source_integrity,
            "reject_reason": reject_reason,
            "reason": reason,
        },
        ensure_ascii=False,
    )


def relation_fixture(*relations: dict[str, str]) -> str:
    return json.dumps({"relations": list(relations)}, ensure_ascii=False)


def _write_test_embedding_profiles(mem_dir: Path, *, active: str = "test-local") -> None:
    save_embedding_profile_config(
        EmbeddingProfileConfig(
            active=active,
            profiles={
                "none": EmbeddingProfile(id="none", provider="none", model="none", dimension=0, normalize=False),
                "test-local": EmbeddingProfile(id="test-local", provider="test", model="test-local-v1", dimension=8),
                "test-alt": EmbeddingProfile(id="test-alt", provider="test", model="test-alt-v1", dimension=8),
            },
        ),
        mem_dir=mem_dir,
    )


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
    with patch.dict(os.environ, {"MEMASSIST_STOP_INGEST_MODE": "sync", "MEMASSIST_HOOK_MODE": "full"}):
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
    def test_evaluator_activates_explicit_directive_from_source_metadata(self) -> None:
        candidate = MemoryCandidate(
            type="directive",
            content="앞으로 refresh token 변경은 묻지 않고 수정하지마",
            tags=["explicit", "user_prompt", "auth", "token"],
            importance=0.85,
            confidence=0.85,
            reason="User explicitly asked to remember this future behavior.",
        )

        decision = evaluate_candidate(candidate, existing_memories=[])

        self.assertEqual(decision.status, "active")
        self.assertEqual(decision.decision, "activate")
        self.assertEqual(decision.risk, "low")

    def test_evaluator_keeps_inferred_rule_candidate_without_explicit_source(self) -> None:
        candidate = MemoryCandidate(
            type="rule",
            content="Do not change refresh token policy without asking.",
            tags=["lesson"],
            importance=0.8,
            confidence=0.7,
            reason="Inferred from trace behavior, not a direct user source.",
        )

        decision = evaluate_candidate(candidate, existing_memories=[])

        self.assertEqual(decision.status, "candidate")
        self.assertEqual(decision.decision, "keep_candidate")

    def test_init_creates_project_policy(self) -> None:
        with isolated_env() as (_root, project, _home):
            code = main(["init"])
            self.assertEqual(code, 0)
            config_path = project / ".memassist" / "verification.yaml"
            self.assertTrue(config_path.exists())
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn("verification_commands: []", config_text)
            self.assertNotIn(".env", config_text)
            self.assertNotIn("rm -rf", config_text)
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
                self.assertTrue((project / ".memassist" / "verification.yaml").exists())
                self.assertTrue((project / ".codex" / "hooks.json").exists())
                self.assertFalse((fake_home / ".memassist" / "verification.yaml").exists())
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
                self.assertTrue((project / ".memassist" / "verification.yaml").exists())
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
                os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = judge_fixture(
                    "Confirm before changing refresh token behavior.",
                    source_quote="앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해",
                    memory_type="directive",
                    source_integrity="clean",
                    reason="User asked to confirm before future refresh token changes.",
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
                artifact = find_memory_artifact(local_home, judged_memory.id)
                self.assertIsNotNone(artifact)
                assert artifact is not None
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

    def test_doctor_reports_embedding_model_installation_status(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["doctor", "--json"]), 0)
            checks = {check["name"]: check for check in json.loads(out.getvalue())["checks"]}
            self.assertEqual("warn", checks["embedding_model"]["status"])
            self.assertIn("missing_model", checks["embedding_model"]["detail"])
            self.assertIn("local-default", checks["embedding_model"]["detail"])

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
            self.assertTrue(statuses["codex"]["capabilities"]["pre_tool_trace_capture"])
            self.assertTrue(statuses["codex"]["capabilities"]["pre_tool_trace_only"])
            self.assertTrue(statuses["codex"]["capabilities"]["isolated_memory_judgment"])
            # Claude has a judge adapter (claude -p), so it is judge-capable;
            # OpenCode has no adapter and stays non-judge-capable.
            self.assertTrue(statuses["claude"]["capabilities"]["isolated_memory_judgment"])
            self.assertFalse(statuses["opencode"]["capabilities"]["isolated_memory_judgment"])
            plugin = (project / ".opencode" / "plugins" / "memassist.js").read_text(encoding="utf-8")
            self.assertNotIn("permissionDecision", plugin)
            self.assertNotIn("memassist denied this tool call", plugin)

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
                self.assertTrue(any("refresh token policy" in memory.content for memory in pack.context))
                self.assertTrue(pack.verifier)
            finally:
                store.close()

    def test_query_intent_preserves_query_and_extracts_only_explicit_paths(self) -> None:
        intent = analyze_query_intent(
            "Implement auth session fix in src/auth/session.py and run pytest verification"
        )
        self.assertEqual(intent.task_type, "general")
        self.assertEqual([], intent.domains)
        self.assertIn("src/auth/session.py", intent.likely_paths)
        self.assertEqual(intent.risk_level, "low")
        self.assertFalse(intent.needs_caution_context)
        self.assertFalse(intent.needs_verifier)
        self.assertEqual(
            [
                "Implement auth session fix in src/auth/session.py and run pytest verification",
                "src/auth/session.py",
            ],
            intent.retrieval_queries,
        )

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
                self.assertEqual(pack.intent.task_type if pack.intent else None, "general")
                self.assertTrue(any("retrieval module" in memory.content for memory in pack.context))
                self.assertTrue(any("policy behavior changes" in memory.content for memory in pack.context))
                self.assertTrue(any(memory.type == "workflow" for memory in pack.verifier))
            finally:
                store.close()

    def test_memory_pack_retrieves_source_language_refresh_token_memory_across_variants(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            _write_test_embedding_profiles(project.root / ".memassist")
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
                    content="Refresh token change reminders stay as agent context, not host tool control.",
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

    def test_memory_quality_eval_measures_wrong_context_promotion_and_stale_rates(self) -> None:
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
                    content="Refresh token change reminders stay as agent context, not host tool control.",
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
            self.assertEqual(result["wrong_context_promotion_rate"], 0.0)
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
                    content="Ask before editing refresh token behavior; this is agent context, not a tool block.",
                    tags=["refresh", "token", "directive"],
                    status="active",
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

    def test_hook_uses_payload_cwd_project_home_not_shell_cwd_or_user_home(self) -> None:
        with isolated_env() as (root, project_dir, home):
            main(["init"])
            other = root / "other"
            other.mkdir()
            os.chdir(other)
            payload = {
                "session_id": "sess_payload_project_home",
                "cwd": str(project_dir),
                "toolName": "shell",
                "toolArgs": {"command": "pwd"},
            }
            with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()):
                self.assertEqual(main(["hook", "pre-tool-use"]), 0)

            project_db = project_dir / ".memassist" / "memassist.db"
            user_db = home / ".memassist" / "memassist.db"
            with Store(project_db) as store:
                events = store.trace_events("sess_payload_project_home")
            self.assertTrue(any(event["event_type"] == "pre_tool_use" for event in events))
            self.assertFalse(user_db.exists())

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
            self.assertNotIn("SQLite-only stale retrieval sentinel", stdout.getvalue())

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

    def test_stop_hook_records_project_local_source_ledger_and_links_judged_memory(self) -> None:
        fixture = json.loads(
            judge_fixture(
                "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해.",
                memory_type="preference",
                source_integrity="clean",
                reason="Persistent user preference.",
            )
        )
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            old_fixture = os.environ.get("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE")
            os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = json.dumps(fixture, ensure_ascii=False)
            try:
                payload = {
                    "session_id": "sess_source_ledger",
                    "cwd": str(project_dir),
                    "prompt": "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해.",
                }
                with patch.dict(os.environ, {"MEMASSIST_STOP_INGEST_MODE": "sync", "MEMASSIST_HOOK_MODE": "full"}), patch(
                    "sys.stdin", StringIO(json.dumps(payload, ensure_ascii=False))
                ), patch("sys.stdout", StringIO()):
                    self.assertEqual(main(["hook", "stop"]), 0)
            finally:
                if old_fixture is None:
                    os.environ.pop("MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE", None)
                else:
                    os.environ["MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE"] = old_fixture

            records = load_source_records(project_dir / ".memassist")
            self.assertEqual(1, len(records))
            self.assertEqual("sess_source_ledger", records[0].session_id)
            self.assertIn("리프레시 토큰", records[0].text)

            with Store() as store:
                memories = store.list_memories(project_id=detect_project().id, include_global=False, status="active")
            stored = [memory for memory in memories if "리프레시 토큰" in memory.content]
            self.assertEqual(1, len(stored))
            self.assertEqual([records[0].id], stored[0].source_ids)
            self.assertEqual(fixture["memory"]["source_quote"], stored[0].source_quote)

    def test_export_import_preserves_source_evidence_fields(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="Keep refresh token changes source-grounded.",
                    tags=["auth"],
                    status="active",
                    source_ref="turn://source",
                    source_quote="source quote for export",
                    source_ids=["src_export"],
                )
            export_path = project_dir / "memories.json"
            with patch("sys.stdout", StringIO()):
                self.assertEqual(main(["memory", "export", str(export_path), "--all"]), 0)
            payload = json.loads(export_path.read_text(encoding="utf-8"))
            exported = payload["memories"][0]
            self.assertEqual("source quote for export", exported["source_quote"])
            self.assertEqual(["src_export"], exported["source_ids"])
            self.assertTrue(exported["content_hash"])

            with Store() as store:
                store.delete_memory(memory_id)
            with patch("sys.stdout", StringIO()):
                self.assertEqual(main(["memory", "import", str(export_path), "--activate"]), 0)
            with Store() as store:
                imported = store.list_memories(project_id=project.id, include_global=False, status="active")
            self.assertTrue(any(memory.source_quote == "source quote for export" for memory in imported))
            self.assertTrue(any(memory.source_ids == ["src_export"] for memory in imported))

    def test_rebuild_updates_content_hash_after_manual_markdown_edit(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Use lexical search first.",
                    tags=["retrieval"],
                    status="active",
                )
                before = store.get_memory(memory_id)
            self.assertIsNotNone(before)
            artifact = project_dir / ".memassist" / "memories" / "active" / f"{memory_id}.md"
            artifact.write_text(
                artifact.read_text(encoding="utf-8").replace("Use lexical search first.", "Use hybrid search first."),
                encoding="utf-8",
            )
            with Store() as store:
                store.rebuild_memory_index_from_artifacts()
                after = store.get_memory(memory_id)
            self.assertIsNotNone(after)
            self.assertEqual("Use hybrid search first.", after.content)  # type: ignore[union-attr]
            self.assertNotEqual(before.content_hash, after.content_hash)  # type: ignore[union-attr]

    def test_profile_embedding_cache_is_project_local_and_invalidates_on_content_hash(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            _write_test_embedding_profiles(project.root / ".memassist")
            with Store() as store:
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="lesson",
                    content="Semantic retrieval should find session timeout memories.",
                    tags=["retrieval", "session"],
                    status="active",
                )
                memory = store.get_memory(memory_id)
                self.assertIsNotNone(memory)
                profile = load_embedding_profile_config(store.path.parent).active_profile
                summary = store.embedding_cache_summary(profile_id="test-local")
                self.assertEqual(0, summary["row_count"])
                self.assertEqual(0, summary["chunk_row_count"])
                build_memory_embeddings(store, project_id=project.id, profile=profile)
                rows, stats = store.memory_embedding_rows_for_profile(
                    project_id=project.id,
                    profile=profile,
                    dimension=8,
                )
                self.assertEqual(1, len(rows))
                self.assertEqual(1, stats["row_count"])
                store.conn.execute(
                    "UPDATE memory_embeddings SET content_hash = ? WHERE memory_id = ?",
                    ("stale", memory_id),
                )
                store.conn.commit()
                stale_rows, stale_stats = store.memory_embedding_rows_for_profile(
                    project_id=project.id,
                    profile=profile,
                    dimension=8,
                )
                self.assertEqual([], stale_rows)
                self.assertEqual(1, stale_stats["stale_count"])
                results = store.search_memories("session timeout", project_id=project.id)
                self.assertTrue(any(item.id == memory_id for item in results))

    def test_memory_pack_reports_hybrid_retrieval_diagnostics(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            _write_test_embedding_profiles(project.root / ".memassist")
            with Store() as store:
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Run focused retrieval tests after hybrid search changes.",
                    tags=["verification", "retrieval"],
                    status="active",
                )
                profile = load_embedding_profile_config(store.path.parent).active_profile
                build_memory_embeddings(store, project_id=project.id, profile=profile)
                pack = build_memory_pack(store, query="hybrid retrieval verification", project_id=project.id)
            payload = pack.as_dict()
            diagnostics = payload["diagnostics"]
            self.assertIn("vector", diagnostics["channels"])
            self.assertTrue(diagnostics["vector_enabled"])
            self.assertEqual("ok", diagnostics["vector_status"])
            self.assertEqual("test-local", diagnostics["vector"]["profile_id"])
            self.assertIn("vector", diagnostics["contributions"][memory_id])

    def test_init_creates_project_local_embedding_profiles_and_ignores_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            parent = root / "parent"
            project = parent / "plain-project"
            home.mkdir()
            parent.mkdir()
            project.mkdir()
            (parent / ".memassist").mkdir()
            _write_test_embedding_profiles(parent / ".memassist", active="test-alt")
            old_cwd = Path.cwd()
            old_home = os.environ.get("HOME")
            old_memassist = os.environ.get("MEMASSIST_HOME")
            os.environ["HOME"] = str(home)
            os.environ.pop("MEMASSIST_HOME", None)
            os.chdir(project)
            try:
                self.assertEqual(main(["init"]), 0)
                local_config = load_embedding_profile_config(project / ".memassist")
                parent_config = load_embedding_profile_config(parent / ".memassist")
            finally:
                os.chdir(old_cwd)
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home
                if old_memassist is None:
                    os.environ.pop("MEMASSIST_HOME", None)
                else:
                    os.environ["MEMASSIST_HOME"] = old_memassist
            self.assertEqual("local-default", local_config.active)
            self.assertEqual("test-alt", parent_config.active)
            self.assertTrue(embedding_profiles_path(project / ".memassist").exists())

    def test_init_installs_active_embedding_profile_unless_skipped(self) -> None:
        with isolated_env() as (_root, project, _home):
            result = EmbeddingModelInstallResult(
                profile_id="local-default",
                status="ok",
                path=str(project / ".memassist" / "models" / "local-default"),
                detail="installed",
            )
            with patch.dict(os.environ, {"MEMASSIST_INIT_SKIP_EMBEDDING_INSTALL": "0"}):
                with patch("memassist.cli.install_embedding_model", return_value=result) as installer:
                    with patch("sys.stdout", StringIO()) as out:
                        self.assertEqual(main(["init"]), 0)
            installer.assert_called_once()
            profile_arg = installer.call_args.args[0]
            self.assertEqual("local-default", profile_arg.id)
            self.assertEqual((project / ".memassist").resolve(), installer.call_args.kwargs["mem_dir"].resolve())
            self.assertIn("Embedding model: installed", out.getvalue())

    def test_init_can_skip_embedding_install_explicitly(self) -> None:
        with isolated_env():
            with patch.dict(os.environ, {"MEMASSIST_INIT_SKIP_EMBEDDING_INSTALL": "0"}):
                with patch("memassist.cli.install_embedding_model") as installer:
                    self.assertEqual(main(["init", "--skip-embedding-install"]), 0)
            installer.assert_not_called()

    def test_init_fails_when_active_embedding_install_fails(self) -> None:
        with isolated_env():
            result = EmbeddingModelInstallResult(
                profile_id="local-default",
                status="install_error",
                path=None,
                detail="network unavailable",
            )
            with patch.dict(os.environ, {"MEMASSIST_INIT_SKIP_EMBEDDING_INSTALL": "0"}):
                with patch("memassist.cli.install_embedding_model", return_value=result):
                    with patch("sys.stdout", StringIO()) as out:
                        self.assertEqual(main(["init"]), 1)
            self.assertIn("Embedding model: install_error", out.getvalue())

    def test_default_profile_reports_missing_dependency_without_breaking_retrieval(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Hybrid retrieval still uses lexical fallback when local vector dependency is unavailable.",
                    tags=["retrieval"],
                    status="active",
                )
                with patch.dict("memassist.embeddings._PROVIDER_FACTORIES", {}, clear=True):
                    pack = build_memory_pack(store, query="lexical fallback vector dependency", project_id=project.id)
            payload = pack.as_dict()
            self.assertEqual("missing_dependency", payload["diagnostics"]["vector_status"])
            self.assertTrue(payload["context"])

    def test_default_profile_reports_missing_model_without_runtime_download(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Lexical retrieval remains available when the embedding model is not installed.",
                    tags=["retrieval"],
                    status="active",
                )
                pack = build_memory_pack(store, query="embedding model not installed lexical", project_id=project.id)
            payload = pack.as_dict()
            self.assertEqual("missing_model", payload["diagnostics"]["vector_status"])
            self.assertTrue(payload["context"])

    def test_none_profile_reports_disabled_vector_status(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with patch("sys.stdout", StringIO()):
                self.assertEqual(main(["embedding", "activate", "none"]), 0)
            with Store() as store:
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Vector disabled diagnostics keep lexical retrieval working.",
                    tags=["retrieval"],
                    status="active",
                )
                pack = build_memory_pack(store, query="vector disabled diagnostics", project_id=project.id)
            payload = pack.as_dict()
            self.assertEqual("disabled", payload["diagnostics"]["vector_status"])
            self.assertTrue(payload["context"])

    def test_embedding_cli_build_cleanup_and_compare_profiles(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            _write_test_embedding_profiles(project.root / ".memassist")
            with Store() as store:
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Embedding profile comparison uses active vector diagnostics.",
                    tags=["embedding", "retrieval"],
                    status="active",
                )
            with patch("sys.stdout", StringIO()) as out:
                self.assertEqual(main(["embedding", "profiles", "--json"]), 0)
                self.assertIn("test-local", out.getvalue())
            with patch("sys.stdout", StringIO()) as out:
                self.assertEqual(main(["embedding", "build", "--profile", "test-local", "--json"]), 0)
                payload = json.loads(out.getvalue())
                self.assertEqual("ok", payload["status"])
            with Store() as store:
                self.assertEqual(1, store.embedding_cache_summary(profile_id="test-local")["row_count"])
            case_file = project.root / "cases.json"
            case_file.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "query": "embedding profile comparison",
                                "expect": ["profile comparison"],
                                "forbid": ["hidden approval gate"],
                                "seed": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch("sys.stdout", StringIO()) as out:
                self.assertEqual(main(["eval", "compare", "--case-file", str(case_file), "--profiles", "test-local", "--json"]), 0)
                compare = json.loads(out.getvalue())
                self.assertEqual("test-local", compare["profiles"][0]["profile_id"])
            with patch("sys.stdout", StringIO()) as out:
                self.assertEqual(main(["embedding", "cleanup", "--profile", "test-local", "--json"]), 0)
                self.assertEqual(2, json.loads(out.getvalue())["removed"])

    def test_source_ledger_preserves_full_text_and_judge_gets_preview(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            late_memory = "후반부에만 있는 지속 메모리: 리프레시 토큰 변경 전에는 반드시 사용자에게 물어본다."
            content = ("  앞부분은 일회성 작업 설명이며 지속 메모리가 아니다. " * 80) + late_memory + "\n\t"
            with Store() as store:
                source_event = observe_turn_end_memory_source(
                    store,
                    session_id="sess_full_source",
                    project_id=project.id,
                    payload={"prompt": content},
                )
                self.assertIsNotNone(source_event)
                row = store.trace_events("sess_full_source")[0]
                payload = build_judge_payload(store, project=project, source_event=dict(row))

            records = load_source_records(project_dir / ".memassist")
            self.assertEqual(1, len(records))
            self.assertEqual(content, records[0].text)
            self.assertGreater(len(records[0].text), 1000)
            source_payload = payload["source_event"]  # type: ignore[index]
            self.assertTrue(source_payload["content_truncated"])  # type: ignore[index]
            self.assertLessEqual(len(source_payload["content"]), 600)  # type: ignore[index]
            self.assertNotIn(late_memory, source_payload["content"])  # type: ignore[index]
            chunks = source_payload["content_chunks"]  # type: ignore[index]
            self.assertGreater(len(chunks), 1)
            self.assertEqual(content, "".join(str(chunk["text"]) for chunk in chunks))  # type: ignore[index]
            self.assertTrue(any(late_memory in str(chunk["text"]) for chunk in chunks))  # type: ignore[index]

    def test_memory_chunks_retrieve_long_memory_and_budget_prompt_context(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with patch("sys.stdout", StringIO()):
                main(["embedding", "activate", "none"])
            early = "초기 정책은 데이터베이스 마이그레이션을 먼저 검토한다. " * 30
            late = "후반 정책은 벡터 검색 전에 chunk FTS recall을 반드시 확인한다."
            long_content = early + late
            with Store() as store:
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content=long_content,
                    tags=["retrieval"],
                    status="active",
                )
                chunks = store.search_memory_chunks("chunk FTS recall", project_id=project.id)
                self.assertTrue(any(chunk.memory_id == memory_id for chunk in chunks))
                pack = build_memory_pack(store, query="chunk FTS recall", project_id=project.id)
                rendered = render_prompt_context(pack, max_chars=520)

            self.assertTrue(any(memory.id == memory_id for memory in pack.context))
            self.assertLessEqual(len(rendered), 520)
            self.assertIn("chunk FTS recall", rendered)
            self.assertNotEqual(long_content, rendered)

    def test_raw_source_chunks_are_evidence_not_default_retrieval_context(self) -> None:
        from memassist.source_ledger import append_source_record

        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with patch("sys.stdout", StringIO()):
                main(["embedding", "activate", "none"])
            with Store() as store:
                record = append_source_record(
                    project_dir / ".memassist",
                    kind="hook_payload",
                    text="raw-only sentinel should remain evidence, not automatic context",
                    session_id="sess_raw_source",
                    project_id=project.id,
                    source_ref="hook_payload",
                )
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Store normalized durable memories as the primary recall target.",
                    tags=["memory"],
                    status="active",
                    source_ids=[record.id],
                )
                default_chunks = store.search_memory_chunks("raw-only sentinel", project_id=project.id)
                evidence_chunks = store.search_memory_chunks("raw-only sentinel", project_id=project.id, include_raw=True)
                pack = build_memory_pack(store, query="raw-only sentinel", project_id=project.id)

            self.assertEqual([], default_chunks)
            self.assertTrue(any(chunk.memory_id == memory_id and chunk.chunk_kind == "raw_source" for chunk in evidence_chunks))
            self.assertFalse(any(memory.id == memory_id for memory in pack.context))

    def test_source_quote_recall_does_not_inject_source_quote(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with patch("sys.stdout", StringIO()):
                main(["embedding", "activate", "none"])
            source_quote = "리프레쉬 토큰 정책은 담부터 묻지 않고 고치지마"
            content = "Do not change refresh token policy without asking first."
            with Store() as store:
                memory_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content=content,
                    source_quote=source_quote,
                    status="active",
                )
                chunks = store.search_memory_chunks("리프레시 토큰 15분", project_id=project.id)
                pack = build_memory_pack(store, query="리프레시 토큰 15분으로 변경해줘", project_id=project.id)
                rendered = render_prompt_context(pack)

            self.assertTrue(any(chunk.memory_id == memory_id and chunk.chunk_kind == "source_quote" for chunk in chunks))
            self.assertTrue(any(memory.id == memory_id for memory in pack.context))
            self.assertIn(content, rendered)
            self.assertNotIn(source_quote, rendered)
            self.assertFalse(any(snippet.chunk_kind == "source_quote" for snippet in pack.snippets))

    def test_embedding_install_cli_installs_selected_profile(self) -> None:
        with isolated_env() as (_root, project, _home):
            self.assertEqual(main(["init"]), 0)
            _write_test_embedding_profiles(project / ".memassist")
            result = EmbeddingModelInstallResult(
                profile_id="test-local",
                status="ok",
                path=str(project / ".memassist" / "models" / "test-local"),
                detail="installed",
            )
            with patch("memassist.cli.install_embedding_model", return_value=result) as installer:
                with patch("sys.stdout", StringIO()) as out:
                    self.assertEqual(main(["embedding", "install", "--profile", "test-local", "--json"]), 0)
            payload = json.loads(out.getvalue())
            self.assertEqual("ok", payload["status"])
            self.assertEqual("test-local", installer.call_args.args[0].id)
            self.assertEqual((project / ".memassist").resolve(), installer.call_args.kwargs["mem_dir"].resolve())

    def test_install_embedding_model_downloads_remote_model_to_profile_cache_dir(self) -> None:
        with isolated_env() as (_root, project, _home):
            self.assertEqual(main(["init"]), 0)
            profile = load_embedding_profile_config(project / ".memassist").active_profile
            expected_path = project / ".memassist" / "models" / "local-default"
            validated = EmbeddingModelInstallResult(profile_id=profile.id, status="ok", path=str(expected_path))
            with patch("huggingface_hub.snapshot_download") as snapshot:
                with patch("memassist.embeddings._validate_installed_profile", return_value=validated):
                    result = install_embedding_model(profile, mem_dir=project / ".memassist")
            self.assertEqual("ok", result.status)
            self.assertEqual(str(expected_path), snapshot.call_args.kwargs["local_dir"])
            self.assertEqual(profile.model, snapshot.call_args.kwargs["repo_id"])

    def test_install_embedding_model_rejects_unsupported_provider(self) -> None:
        with isolated_env() as (_root, project, _home):
            self.assertEqual(main(["init"]), 0)
            profile = EmbeddingProfile(id="st-local", provider="sentence-transformers", model="sentence/model", dimension=384)
            result = install_embedding_model(profile, mem_dir=project / ".memassist")
            self.assertEqual("unsupported", result.status)
            self.assertIn("sentence-transformers", result.detail or "")

    def test_embedding_model_status_validates_cache_by_loading_provider(self) -> None:
        with isolated_env() as (_root, project, _home):
            self.assertEqual(main(["init"]), 0)
            mem_dir = project / ".memassist"
            profile = load_embedding_profile_config(mem_dir).active_profile
            cache_dir = mem_dir / "models" / "local-default"
            cache_dir.mkdir(parents=True)
            (cache_dir / "README.txt").write_text("not a model\n", encoding="utf-8")

            class FakeStaticModel:
                @classmethod
                def from_pretrained(cls, *_args: object, **_kwargs: object) -> "FakeStaticModel":
                    raise RuntimeError("invalid model layout")

            fake_module = types.SimpleNamespace(StaticModel=FakeStaticModel)
            with patch.dict(sys.modules, {"model2vec": fake_module}):
                status = embedding_model_status(profile, mem_dir=mem_dir)
            self.assertEqual("missing_model", status.status)
            self.assertIn("invalid model layout", status.detail or "")

    def test_model2vec_provider_passes_profile_loading_options_without_force_download(self) -> None:
        captured: dict[str, object] = {}

        class FakeStaticModel:
            dim = 256

            @classmethod
            def from_pretrained(cls, path: object, **kwargs: object) -> "FakeStaticModel":
                captured["path"] = path
                captured.update(kwargs)
                return cls()

            def encode(self, texts: list[str]) -> list[list[float]]:
                assert texts
                return [[1.0, 0.0]]

        profile = EmbeddingProfile(
            id="local-default",
            provider="model2vec",
            model="/tmp/local-model",
            quantization="int8",
            dimension=256,
            normalize=True,
        )
        fake_module = types.SimpleNamespace(StaticModel=FakeStaticModel)
        with patch.dict(sys.modules, {"model2vec": fake_module}):
            provider = Model2VecEmbeddingProvider(profile)
        self.assertEqual("/tmp/local-model", captured["path"])
        self.assertEqual("int8", captured["quantize_to"])
        self.assertEqual(256, captured["dimensionality"])
        self.assertEqual(False, captured["force_download"])
        self.assertEqual(256, provider.dimension)

    def test_embedding_profile_diagnostics_do_not_create_pretool_policy(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            self.assertEqual(main(["init", "--tools", "codex"]), 0)
            project = detect_project()
            _write_test_embedding_profiles(project.root / ".memassist")
            with Store() as store:
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content="Ask before changing src/auth/session.py.",
                    tags=["auth"],
                    paths=["src/auth/session.py"],
                    status="active",
                )
            payload = {
                "session_id": "sess_embedding_profile_pretool",
                "cwd": str(project_dir),
                "toolName": "apply_patch",
                "toolArgs": {"command": "*** Begin Patch\n*** Update File: src/auth/session.py\n@@\n-1\n+2\n*** End Patch"},
            }
            with patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", StringIO()) as out:
                self.assertEqual(main(["hook", "pre-tool-use"]), 0)
                self.assertEqual("", out.getvalue())
            config_text = (project_dir / ".memassist" / "verification.yaml").read_text(encoding="utf-8")
            self.assertNotIn("src/auth/session.py", config_text)
            with Store() as store:
                events = store.trace_events("sess_embedding_profile_pretool")
            self.assertEqual(1, len(events))
            self.assertEqual("pre_tool_use", events[0]["event_type"])

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

    def test_lifecycle_keeps_trace_lessons_as_inactive_candidates(self) -> None:
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_trace_event(
                    session_id="sess_trace_lesson",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="apply_patch",
                    input_json={"command": "*** Update File: src/auth/refresh-token-policy.ts"},
                    files=["src/auth/refresh-token-policy.ts"],
                    tool_decision="deny",
                )
                store.add_trace_event(
                    session_id="sess_trace_lesson",
                    project_id=project.id,
                    event_type="stop",
                    input_json={
                        "last_assistant_message": "앞으로 refresh token 정책은 묻지 않고 수정하지 않습니다."
                    },
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["daemon", "once", "--session", "sess_trace_lesson", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            lifecycle = result["lifecycle"]
            self.assertEqual(len(lifecycle["candidates"]), 0)
            self.assertEqual(len(lifecycle["decisions"]), 0)

            out = StringIO()
            with patch("sys.stdout", out):
                main(["memory", "list", "--all", "--json"])
            memories = json.loads(out.getvalue())
            heuristic_candidates = [
                memory
                for memory in memories
                if memory.get("source_kind") in {"lifecycle", "extracted"} and memory.get("status") == "candidate"
            ]
            self.assertEqual(len(heuristic_candidates), 0)

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
                (project_dir / ".memassist" / "verification.yaml").read_text(encoding="utf-8"),
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
            self.assertNotIn("src/auth/refresh-token-policy.ts", (project_dir / ".memassist" / "verification.yaml").read_text())

    def test_stop_async_enqueues_source_and_spawns_without_judging(self) -> None:
        old_mode = os.environ.pop("MEMASSIST_STOP_INGEST_MODE", None)
        try:
            self.assertEqual("async", stop_ingest_mode())
        finally:
            if old_mode is not None:
                os.environ["MEMASSIST_STOP_INGEST_MODE"] = old_mode

        with isolated_env() as (_root, project_dir, _home), patch.dict(
            os.environ,
            {"MEMASSIST_STOP_INGEST_MODE": "async", "MEMASSIST_HOOK_MODE": "full"},
        ):
            self.assertEqual(main(["init", "--tools", "codex"]), 0)
            payload = {
                "session_id": "sess_async_stop",
                "cwd": str(project_dir),
                "prompt": "앞으로 API schema 변경 전에는 migration 테스트를 먼저 실행해줘",
            }
            with patch(
                "memassist.cli.spawn_async_ingestion_worker",
                return_value=WorkerSpawnResult(status="spawned", session_id="sess_async_stop", pid=123),
            ) as spawn, patch(
                "memassist.cli.process_pending_memory_intents"
            ) as process:
                self.assertEqual(_fire_hook("stop", payload), 0)
            spawn.assert_called_once()
            process.assert_not_called()
            with Store() as store:
                events = [dict(event) for event in store.trace_events("sess_async_stop")]
            event_types = [event["event_type"] for event in events]
            self.assertIn("stop", event_types)
            self.assertIn("memory_source_observed", event_types)
            self.assertIn("async_ingestion_requested", event_types)
            self.assertNotIn("memory_judged", event_types)

    def test_daemon_once_processes_pending_source_into_retrievable_memory(self) -> None:
        with isolated_env() as (_root, project_dir, _home), patch.dict(
            os.environ,
            {
                "MEMASSIST_STOP_INGEST_MODE": "off",
                "MEMASSIST_HOOK_MODE": "full",
                "MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE": judge_fixture(
                    "API schema changes should run migration tests first.",
                    source_quote="앞으로 API schema 변경 전에는 migration 테스트를 먼저 실행해줘",
                    memory_type="workflow",
                ),
            },
        ):
            self.assertEqual(main(["init", "--tools", "codex"]), 0)
            payload = {
                "session_id": "sess_daemon_ingest",
                "cwd": str(project_dir),
                "prompt": "앞으로 API schema 변경 전에는 migration 테스트를 먼저 실행해줘",
            }
            self.assertEqual(_fire_hook("stop", payload), 0)
            with Store() as store:
                before_events = [event["event_type"] for event in store.trace_events("sess_daemon_ingest")]
            self.assertIn("memory_source_observed", before_events)
            self.assertNotIn("memory_judged", before_events)
            self.assertNotIn("async_ingestion_requested", before_events)
            doctor_out = StringIO()
            with patch("sys.stdout", doctor_out):
                self.assertEqual(main(["doctor", "--json"]), 0)
            doctor = json.loads(doctor_out.getvalue())
            async_check = next(check for check in doctor["checks"] if check["name"] == "async_ingestion")
            self.assertEqual("warn", async_check["status"])
            self.assertIn("pending=1", async_check["detail"])

            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(main(["daemon", "once", "--session", "sess_daemon_ingest", "--skip-eval", "--json"]), 0)
            result = json.loads(out.getvalue())
            self.assertEqual("ok", result["ingestion"]["status"])
            self.assertEqual(1, result["ingestion"]["processed"])
            project = detect_project()
            with Store() as store:
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                pack = build_memory_pack(store, query="migration test API schema", project_id=project.id)
            self.assertTrue(any(memory.source_kind == "isolated_memory_judge" for memory in memories))
            self.assertTrue(any("API schema changes" in item["content"] for item in pack.as_dict()["context"]))

    def test_daemon_once_respects_async_batch_limit(self) -> None:
        with isolated_env() as (_root, project_dir, _home), patch.dict(
            os.environ,
            {
                "MEMASSIST_STOP_INGEST_MODE": "off",
                "MEMASSIST_HOOK_MODE": "full",
                "MEMASSIST_MEMORY_JUDGE_FIXTURE_RESPONSE": judge_fixture(
                    "Run migration tests before API schema changes.",
                    memory_type="workflow",
                ),
            },
        ):
            self.assertEqual(main(["init", "--tools", "codex"]), 0)
            for index in range(2):
                payload = {
                    "session_id": "sess_daemon_batch",
                    "cwd": str(project_dir),
                    "prompt": f"앞으로 API schema 변경 전 migration 테스트 {index}를 기억해",
                }
                self.assertEqual(_fire_hook("stop", payload), 0)

            out = StringIO()
            with patch("sys.stdout", out):
                self.assertEqual(
                    main(["daemon", "once", "--session", "sess_daemon_batch", "--batch-limit", "1", "--skip-eval", "--json"]),
                    0,
                )
            result = json.loads(out.getvalue())
            self.assertEqual(1, result["ingestion"]["processed"])
            with Store() as store:
                judged = [event for event in store.trace_events("sess_daemon_batch") if event["event_type"] == "memory_judged"]
                self.assertEqual(1, len(judged))
                self.assertEqual(1, pending_source_count(store, project_id=detect_project().id, session_id="sess_daemon_batch"))

    def test_trace_hook_mode_stop_does_not_enqueue_or_spawn_ingestion(self) -> None:
        with isolated_env() as (_root, project_dir, _home), patch.dict(
            os.environ,
            {"MEMASSIST_STOP_INGEST_MODE": "async", "MEMASSIST_HOOK_MODE": "trace"},
        ):
            self.assertEqual(main(["init", "--tools", "codex", "--mode", "trace"]), 0)
            payload = {
                "session_id": "sess_trace_stop",
                "cwd": str(project_dir),
                "prompt": "앞으로 trace 모드에서는 memory ingest를 하지 않는다",
            }
            with patch("memassist.cli.spawn_async_ingestion_worker") as spawn:
                self.assertEqual(_fire_hook("stop", payload), 0)
            spawn.assert_not_called()
            with Store() as store:
                event_types = [event["event_type"] for event in store.trace_events("sess_trace_stop")]
            self.assertIn("stop", event_types)
            self.assertNotIn("memory_source_observed", event_types)
            self.assertNotIn("async_ingestion_requested", event_types)

    def test_stop_async_skips_spawn_when_worker_lock_is_active(self) -> None:
        with isolated_env() as (_root, project_dir, _home), patch.dict(
            os.environ,
            {"MEMASSIST_STOP_INGEST_MODE": "async", "MEMASSIST_HOOK_MODE": "full"},
        ):
            self.assertEqual(main(["init", "--tools", "codex"]), 0)
            lock_path = worker_lock_path(project_dir / ".memassist")
            lock_path.write_text('{"pid": 1}\n', encoding="utf-8")
            payload = {
                "session_id": "sess_async_locked",
                "cwd": str(project_dir),
                "prompt": "앞으로 locked worker 테스트를 기억해",
            }
            self.assertEqual(_fire_hook("stop", payload), 0)
            with Store() as store:
                requests = [
                    json.loads(str(event["input_json"] or "{}"))
                    for event in store.trace_events("sess_async_locked")
                    if event["event_type"] == "async_ingestion_requested"
                ]
            self.assertEqual(1, len(requests))
            self.assertEqual("skipped_locked", requests[0]["spawned"]["status"])

    def test_daemon_once_records_async_failure_diagnostics(self) -> None:
        with isolated_env() as (_root, project_dir, _home), patch.dict(
            os.environ,
            {"MEMASSIST_STOP_INGEST_MODE": "off", "MEMASSIST_HOOK_MODE": "full"},
        ):
            self.assertEqual(main(["init", "--tools", "codex"]), 0)
            payload = {
                "session_id": "sess_async_failure",
                "cwd": str(project_dir),
                "prompt": "앞으로 실패 진단 테스트를 기억해",
            }
            self.assertEqual(_fire_hook("stop", payload), 0)
            with patch("memassist.async_ingestion.process_memory_intent_event", side_effect=RuntimeError("boom")):
                out = StringIO()
                with patch("sys.stdout", out):
                    self.assertEqual(main(["daemon", "once", "--session", "sess_async_failure", "--skip-eval", "--json"]), 1)
            result = json.loads(out.getvalue())
            self.assertEqual("failed", result["ingestion"]["status"])
            self.assertIn("boom", result["ingestion"]["error"])
            with Store() as store:
                event_types = [event["event_type"] for event in store.trace_events("sess_async_failure")]
            self.assertIn("async_ingestion_worker_failed", event_types)

    def test_pending_source_lookup_finds_old_pending_beyond_recent_trace_window(self) -> None:
        with isolated_env():
            self.assertEqual(main(["init"]), 0)
            project = detect_project()
            with Store() as store:
                pending_id = store.add_trace_event(
                    session_id="sess_old_pending",
                    project_id=project.id,
                    event_type="memory_source_observed",
                    tool_name="memassist",
                    input_json={"content": "old pending source", "source_ref": "old", "status": "pending"},
                )
                for index in range(250):
                    source_id = store.add_trace_event(
                        session_id=f"sess_processed_{index}",
                        project_id=project.id,
                        event_type="memory_source_observed",
                        tool_name="memassist",
                        input_json={"content": f"processed source {index}", "source_ref": str(index), "status": "pending"},
                    )
                    store.add_trace_event(
                        session_id=f"sess_processed_{index}",
                        project_id=project.id,
                        event_type="memory_judged",
                        tool_name="memassist",
                        input_json={"source_event_id": source_id, "candidate": None},
                    )
                pending = pending_memory_source_events(store, project_id=project.id, limit=1)
            self.assertEqual(pending_id, pending[0]["id"])

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
            fixture = judge_fixture(
                "refresh token 관련 변경 전 사용자에게 먼저 확인한다.",
                source_quote="앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐",
                memory_type="directive",
                source_integrity="clean",
                reason="Payload isolation test.",
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

    def test_clean_source_judge_preference_auto_activates(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            fixture = judge_fixture(
                "사용자는 한국어로 간결한 답변을 선호한다.",
                source_quote="앞으로 답변은 한국어로 짧게 해줘",
                memory_type="preference",
                source_integrity="clean",
                reason="Clean source user preference.",
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
            self.assertFalse(any(event["event_type"] == "memory_intent_observed" for event in traces))
            self.assertNotIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "verification.yaml").read_text(encoding="utf-8"),
            )

    def test_user_prompt_directive_becomes_active_retrieval_context_without_policy(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            protected.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            fixture = judge_fixture(
                "리프레시 토큰 관련 변경은 사용자 확인 전 수정하지 않는다.",
                source_quote="리프레시 토큰 관련 변경은 절대 묻지 않고 수정하지마",
                memory_type="directive",
                source_integrity="clean",
                reason="User explicitly set a future edit gate for refresh token changes.",
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
            self.assertEqual(direct_memories[0].paths, [])
            self.assertTrue(any(event["event_type"] == "memory_source_observed" for event in traces))
            self.assertTrue(any(event["event_type"] == "memory_judged" for event in traces))
            self.assertNotIn(
                "src/auth/refresh-token-policy.ts",
                (project_dir / ".memassist" / "verification.yaml").read_text(encoding="utf-8"),
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
            fixture = judge_fixture(
                "리프레시 토큰 변경 시 사용자 확인을 받는다.",
                source_quote="리프레시토큰 변경에 승인확인하라",
                memory_type="directive",
                source_integrity="clean",
                reason="Directive without any trigger keyword.",
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
            fixture = no_memory_fixture(
                source_integrity="clean",
                reject_reason="one_shot_task",
                reason="One-off task; nothing durable to remember.",
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
            self.assertEqual(mem, [])

    def test_typo_directive_is_judged_to_active_source_language_memory(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            protected = project_dir / "src" / "auth" / "refresh-token-policy.ts"
            protected.parent.mkdir(parents=True)
            protected.write_text("export const refreshTokenRotation = true;\n", encoding="utf-8")
            fixture = judge_fixture(
                "리프레시 토큰 관련 변경 전 사용자에게 먼저 확인한다.",
                source_quote="리프레쉬 토큰 쪽은 담부터 고치기 전에 꼭 나한테 먼저 말해줘",
                memory_type="directive",
                source_integrity="clean",
                reason="User asked for confirmation before refresh token changes.",
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
            config_text = (project_dir / ".memassist" / "verification.yaml").read_text(encoding="utf-8")
            self.assertNotIn("src/auth/refresh-token-policy.ts", config_text)
            self.assertTrue(
                any(
                    memory.status == "active"
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
            fixture = judge_fixture(
                "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정한다.",
                source_quote="앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.",
                memory_type="directive",
                source_integrity="clean",
                reason="Durable refresh token edit gate separated from the current response-format instruction.",
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
            self.assertEqual(memories[0].status, "active")
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
            fixture = judge_fixture(
                "Ask before changing refresh token settings.",
                source_quote="Always ask before changing refresh token settings. Reply only OK.",
                memory_type="directive",
                source_integrity="clean",
                reason="Durable edit gate separated from a current-turn response format.",
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
            fixture = judge_fixture(
                "Do not change refresh token policy without asking first.",
                source_quote="refresh token policy 변경은 묻지 않고 하지마",
                memory_type="directive",
                source_integrity="clean",
                reason="Explicit future change gate.",
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
                    and memory.content == "Do not change refresh token policy without asking first."
                    for memory in memories
                )
            )
            judged = [event for event in traces if event["event_type"] == "memory_judged"]
            self.assertEqual(len(judged), 1)
            self.assertIn('"adapter_name": "fixture"', judged[0]["input_json"])

    def test_uncertain_source_judge_candidate_does_not_mutate_policy(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            fixture = judge_fixture(
                "Maybe mention refresh token changes later.",
                source_quote="앞으로 refresh token changes tell me later",
                memory_type="directive",
                source_integrity="uncertain",
                reason="Ambiguous but potentially useful.",
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
                (project_dir / ".memassist" / "verification.yaml").read_text(encoding="utf-8"),
            )

    def test_judge_created_memory_is_path_independent(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            fixture = judge_fixture(
                "Do not change external path.",
                source_quote="외부 경로 건드리지마",
                memory_type="directive",
                source_integrity="clean",
                reason="Memory content is path-independent.",
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
            self.assertNotIn("outside.txt", (project_dir / ".memassist" / "verification.yaml").read_text(encoding="utf-8"))
            self.assertNotIn("/etc/passwd", (project_dir / ".memassist" / "verification.yaml").read_text(encoding="utf-8"))

    def test_uncertain_source_without_paths_does_not_compile_policy(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            target = project_dir / "src" / "auth" / "session.py"
            target.parent.mkdir(parents=True)
            target.write_text("SESSION = True\n", encoding="utf-8")
            fixture = judge_fixture(
                "Tell me before auth changes.",
                source_quote="auth 변경 전에 알려줘",
                memory_type="directive",
                source_integrity="uncertain",
                reason="Source is not clean enough for auto-activation.",
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
                (project_dir / ".memassist" / "verification.yaml").read_text(encoding="utf-8"),
            )

    def test_invalid_judge_output_does_not_mutate_policy(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init", "--tools", "codex"])
            fixture = json.dumps(
                {
                    "memory": {
                        "content": "앞으로 refresh token policy 관련 내용은 기억해줘",
                        "type": "directive",
                    },
                    "source_integrity": "clean",
                    "reason": "missing source_quote should be rejected",
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
                (project_dir / ".memassist" / "verification.yaml").read_text(encoding="utf-8"),
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
                    tool_decision="deny",
                )

            lesson_out = StringIO()
            with patch("sys.stdout", lesson_out):
                code = main(
                    [
                        "lesson",
                        "from-session",
                        "sess_eval",
                        "--feedback",
                        "Remember refresh token edit reminders as agent context only.",
                    ]
                )
            self.assertEqual(code, 0)
            lesson_id = lesson_out.getvalue().strip()
            self.assertTrue(lesson_id.startswith("mem_"))

            with Store() as store:
                memory = store.get_memory(lesson_id)
                self.assertEqual(memory.status, "candidate")  # type: ignore[union-attr]
            self.assertNotIn("src/auth/refresh-token-policy.ts", (project_dir / ".memassist" / "verification.yaml").read_text())

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
                (project_dir / ".memassist" / "verification.yaml").read_text(),
            )

    def test_memory_deactivate_does_not_remove_manual_sensitive_path(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            config_path = project_dir / ".memassist" / "verification.yaml"
            config_path.write_text(
                '# memassist project verification config\nsensitive_paths:\n  - "src/auth/session.py"\nprotected_paths: []\ndangerous_commands: []\nverification_commands: []\n',
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
                    source_kind="test",
                )

            with patch("sys.stdout", StringIO()):
                code = main(["memory", "deactivate", memory_id])
            self.assertEqual(code, 0)
            config_text = (project_dir / ".memassist" / "verification.yaml").read_text()
            self.assertIn("src/auth/session.py", config_text)
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
                        "Ask before billing migration edits; keep this as agent context.",
                        "--tag",
                        "billing",
                        "--importance",
                        "0.9",
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
            self.assertEqual(len(result["stored_candidates"]), 0)
            self.assertEqual(len(result["lifecycle"]["active"]), 0)

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
            self.assertEqual(result["lifecycle"]["duplicates"], 0)
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
            fixture = no_memory_fixture(
                source_integrity="clean",
                reject_reason="one_shot_task",
                reason="One-off task; nothing durable.",
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

    def test_cleanup_does_not_supersede_directive_paraphrase_by_language_keywords(self) -> None:
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
                    source_kind="lifecycle",
                )

            out = StringIO()
            with patch("sys.stdout", out):
                code = main(["daemon", "once", "--session", "missing", "--json"])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertNotIn(candidate_id, result["cleanup"]["duplicates"])
            with Store() as store:
                candidate = store.get_memory(candidate_id)
                self.assertEqual(candidate.status, "candidate")  # type: ignore[union-attr]
                self.assertIsNone(candidate.superseded_by)  # type: ignore[union-attr]


class AdaptiveSignalTest(unittest.TestCase):
    """Task 6.3: Unit tests for signal normalization, cold-start, and lifetime decay."""

    def test_normalize_retrieval_count_log1p_scale(self) -> None:
        from memassist.retrieval import _normalize_retrieval_count
        # Cold-start: 0 retrievals → 0.0
        self.assertEqual(_normalize_retrieval_count(0), 0.0)
        # 100 retrievals → saturates close to 1.0
        self.assertAlmostEqual(_normalize_retrieval_count(100), 1.0, places=5)
        # Monotonic: more retrievals → higher score
        self.assertGreater(_normalize_retrieval_count(10), _normalize_retrieval_count(5))
        self.assertGreater(_normalize_retrieval_count(50), _normalize_retrieval_count(10))
        # Clamped to [0, 1]
        self.assertLessEqual(_normalize_retrieval_count(1000), 1.0)
        self.assertGreaterEqual(_normalize_retrieval_count(0), 0.0)

    def test_recency_decay_cold_start_returns_zero(self) -> None:
        from memassist.retrieval import _recency_decay
        # No timestamp → neutral (0.0)
        self.assertEqual(_recency_decay(None), 0.0)
        self.assertEqual(_recency_decay(""), 0.0)

    def test_recency_decay_recent_timestamp_returns_high_score(self) -> None:
        from datetime import datetime, timezone, timedelta
        from memassist.retrieval import _recency_decay
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        score = _recency_decay(recent)
        self.assertGreater(score, 0.95)  # Very recent → close to 1.0

    def test_recency_decay_old_timestamp_returns_low_score(self) -> None:
        from datetime import datetime, timezone, timedelta
        from memassist.retrieval import _recency_decay
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=365)).isoformat()
        score = _recency_decay(old)
        self.assertLess(score, 0.05)  # Very old → close to 0.0

    def test_usage_score_cold_start_is_zero(self) -> None:
        from memassist.retrieval import _usage_score
        from memassist.models import Memory
        # A cold-start memory with all signals at defaults
        memory = Memory(
            id="mem_cold",
            scope_type="project",
            project_id="proj",
            session_id=None,
            type="fact",
            content="cold start",
            reason=None,
            tags=[],
            paths=[],
            status="active",
            importance=0.5,
            confidence=0.8,
            strength=0.0,
            recurrence=1,
            retrieval_count=0,
            utility=0.0,
            half_life_days=60.0,
            source_kind="manual",
            source_ref=None,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            last_used_at=None,
            expires_at=None,
            superseded_by=None,
        )
        score = _usage_score(memory)
        # Cold-start: retrieval_count=0, utility=0, strength=0, last_used_at=None
        # Expected: 0.0 (neutral contribution)
        self.assertEqual(score, 0.0)

    def test_usage_score_high_signals_returns_positive_score(self) -> None:
        from datetime import datetime, timezone, timedelta
        from memassist.retrieval import _usage_score
        from memassist.models import Memory
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        memory = Memory(
            id="mem_rich",
            scope_type="project",
            project_id="proj",
            session_id=None,
            type="fact",
            content="frequently used",
            reason=None,
            tags=[],
            paths=[],
            status="active",
            importance=0.9,
            confidence=0.9,
            strength=0.9,
            recurrence=10,
            retrieval_count=50,
            utility=0.8,
            half_life_days=90.0,
            source_kind="manual",
            source_ref=None,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            last_used_at=recent,
            expires_at=None,
            superseded_by=None,
        )
        score = _usage_score(memory)
        self.assertGreater(score, 0.5)
        self.assertLessEqual(score, 1.0)

    def test_usage_score_higher_signals_ranks_higher(self) -> None:
        """D2: Memory with higher retrieval_count and more recent last_used_at ranks higher."""
        from datetime import datetime, timezone, timedelta
        from memassist.retrieval import _usage_score
        from memassist.models import Memory

        def make_memory(retrieval_count: int, days_ago: int | None, strength: float) -> Memory:
            last_used = None if days_ago is None else (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
            return Memory(
                id=f"mem_{retrieval_count}",
                scope_type="project",
                project_id="proj",
                session_id=None,
                type="fact",
                content="test",
                reason=None,
                tags=[],
                paths=[],
                status="active",
                importance=0.5,
                confidence=0.5,
                strength=strength,
                recurrence=1,
                retrieval_count=retrieval_count,
                utility=0.0,
                half_life_days=60.0,
                source_kind="manual",
                source_ref=None,
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
                last_used_at=last_used,
                expires_at=None,
                superseded_by=None,
            )

        memory_a = make_memory(retrieval_count=50, days_ago=1, strength=0.8)
        memory_b = make_memory(retrieval_count=5, days_ago=60, strength=0.3)
        self.assertGreater(_usage_score(memory_a), _usage_score(memory_b))

    def test_half_life_signal_based_not_type_mapped(self) -> None:
        """D3: half-life is based on strength, not type→fixed-days mapping."""
        from memassist.storage import _default_half_life_days
        # All types should return the same value for the same strength
        types = ["workflow", "fact", "preference", "rule", "decision", "lesson", "directive", "open_thread"]
        strength = 0.5
        half_lives = [_default_half_life_days(t, "active", strength=strength) for t in types]
        # All types with same strength should have same half-life (type-agnostic)
        self.assertEqual(len(set(half_lives)), 1, f"Expected uniform half-life for strength={strength}, got: {set(half_lives)}")

    def test_half_life_scales_with_strength(self) -> None:
        """D3: Higher strength → longer half-life."""
        from memassist.storage import _default_half_life_days
        low_hl = _default_half_life_days("fact", "active", strength=0.1)
        mid_hl = _default_half_life_days("fact", "active", strength=0.5)
        high_hl = _default_half_life_days("fact", "active", strength=1.0)
        self.assertLess(low_hl, mid_hl)
        self.assertLess(mid_hl, high_hl)

    def test_half_life_respects_min_max_clamps(self) -> None:
        """D3: Half-life has continuous min/max clamps, not type-specific values."""
        from memassist.storage import _default_half_life_days
        min_hl = _default_half_life_days("fact", "active", strength=0.0)
        max_hl = _default_half_life_days("fact", "active", strength=1.0)
        self.assertGreaterEqual(min_hl, 7.0)   # min clamp
        self.assertLessEqual(max_hl, 180.0)    # max clamp

    def test_section_score_no_type_based_boost(self) -> None:
        """D1: _section_score no longer adds type-specific boosts."""
        from memassist.retrieval import _section_score, analyze_query_intent
        from memassist.models import Memory

        def make_memory(memory_type: str) -> Memory:
            return Memory(
                id=f"mem_{memory_type}",
                scope_type="project",
                project_id="proj",
                session_id=None,
                type=memory_type,
                content="test verification memory",
                reason=None,
                tags=["verification"],
                paths=[],
                status="active",
                importance=0.5,
                confidence=0.5,
                strength=0.5,
                recurrence=1,
                retrieval_count=0,
                utility=0.0,
                half_life_days=60.0,
                source_kind="manual",
                source_ref=None,
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
                last_used_at=None,
                expires_at=None,
                superseded_by=None,
            )

        intent = analyze_query_intent("test verification")
        workflow_mem = make_memory("workflow")
        fact_mem = make_memory("fact")
        # Same content, same signals, different types → same score (no type boost)
        workflow_score = _section_score("test verification", intent, workflow_mem, "verifier")
        fact_score = _section_score("test verification", intent, fact_mem, "verifier")
        self.assertAlmostEqual(workflow_score, fact_score, places=5)

    def test_type_label_is_not_search_signal(self) -> None:
        """P0: type is display metadata, not lexical/vector/metadata search text."""
        from memassist.embeddings import memory_embedding_text
        from memassist.models import Memory
        from memassist.retrieval import _metadata_terms

        memory = Memory(
            id="mem_type_signal",
            scope_type="project",
            project_id="proj",
            session_id=None,
            type="workflow",
            content="Use the blue deployment checklist before release.",
            reason=None,
            tags=["deploy"],
            paths=[],
            status="active",
            importance=0.5,
            confidence=0.5,
            strength=0.5,
            recurrence=1,
            retrieval_count=0,
            utility=0.0,
            half_life_days=60.0,
            source_kind="manual",
            source_ref=None,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            last_used_at=None,
            expires_at=None,
            superseded_by=None,
        )
        self.assertNotIn("workflow", memory_embedding_text(memory).split())
        self.assertNotIn("workflow", _metadata_terms(memory))

    def test_fts_search_does_not_match_type_only(self) -> None:
        """P0: a memory is not retrievable solely because its type label matches."""
        with isolated_env():
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="Use the blue deployment checklist before release.",
                    tags=[],
                    paths=[],
                    status="active",
                )
                self.assertEqual(store.search_memories("workflow", project_id=project.id), [])

    def test_session_summary_has_commands_not_test_commands(self) -> None:
        """D4: SessionSummary has 'commands' (all raw commands), not 'test_commands'."""
        from memassist.session import SessionSummary
        import inspect
        fields = [f for f in inspect.fields(SessionSummary)] if hasattr(inspect, "fields") else []
        # Check that 'commands' field exists and 'test_commands' does not
        summary = SessionSummary(
            session_id="test",
            event_count=0,
            tools=[],
            files=[],
            commands=["npm test", "git commit"],
            denied_events=0,
            last_message=None,
        )
        self.assertEqual(summary.commands, ["npm test", "git commit"])
        as_dict = summary.as_dict()
        self.assertIn("commands", as_dict)
        self.assertNotIn("test_commands", as_dict)

    def test_build_judge_payload_includes_session_trace_signals(self) -> None:
        """D4/D5: build_judge_payload includes raw_commands, touched_files, denied_tool_events."""
        from memassist.memory_judge import build_judge_payload
        from memassist.project import Project
        from pathlib import Path
        import tempfile
        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            from memassist.project import detect_project
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_trace_event(
                    session_id="sess_judge_signals",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="Bash",
                    input_json={"command": "jest --testPathPattern=auth"},
                    files=["src/auth/auth.test.ts"],
                )
                store.add_trace_event(
                    session_id="sess_judge_signals",
                    project_id=project.id,
                    event_type="pre_tool_use",
                    tool_name="apply_patch",
                    input_json={"command": "*** Update File: src/auth/session.py"},
                    files=["src/auth/session.py"],
                    tool_decision="deny",
                )
                # Create a fake source event to build payload from
                source_event_id = store.add_trace_event(
                    session_id="sess_judge_signals",
                    project_id=project.id,
                    event_type="memory_source_observed",
                    tool_name="memassist",
                    input_json={
                        "content": "always ask before auth changes",
                        "source_ref": "hook_payload",
                        "source_kind": "hook_payload",
                    },
                )
                source_event = dict(store.conn.execute(
                    "SELECT * FROM trace_events WHERE id = ?", (source_event_id,)
                ).fetchone())
                payload = build_judge_payload(store, project=project, source_event=source_event)

            # Must include session_trace with raw signals
            self.assertIn("session_trace", payload)
            trace = payload["session_trace"]
            self.assertIn("raw_commands", trace)
            self.assertIn("touched_files", trace)
            self.assertIn("denied_tool_events", trace)
            # The jest command should be in raw_commands
            raw_cmds = trace["raw_commands"]
            self.assertTrue(any("jest" in cmd for cmd in raw_cmds))
            # The denial should be in denied_tool_events
            denied = trace["denied_tool_events"]
            self.assertTrue(any(ev.get("tool_decision") in {"deny", "block"} for ev in denied))

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

        inner = json.loads(
            judge_fixture(
                "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정한다",
                source_quote="앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.",
                memory_type="rule",
                source_integrity="clean",
                reason="durable directive",
            )
        )
        envelope = json.dumps({"type": "result", "subtype": "success", "result": json.dumps(inner, ensure_ascii=False)})
        candidate = _candidate_from_output(envelope)
        self.assertIsNotNone(candidate.memory)
        self.assertEqual(candidate.memory.type, "rule")  # type: ignore[union-attr]
        self.assertNotIn("응답은 OK만 해", candidate.memory.content)  # type: ignore[union-attr]
        self.assertIn("리프레시 토큰", candidate.memory.content)  # type: ignore[union-attr]

    def test_parses_fenced_json_from_result_envelope(self) -> None:
        from memassist.memory_judge import _candidate_from_output

        inner = json.loads(
            judge_fixture(
                "리프레시 토큰 변경 전 사용자 확인을 받는다",
                source_quote="앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.",
                memory_type="rule",
                source_integrity="clean",
                reason="durable directive; one-shot response instruction excluded",
            )
        )
        fenced = "```json\n" + json.dumps(inner, ensure_ascii=False) + "\n```"
        envelope = json.dumps({"type": "result", "subtype": "success", "result": fenced})
        candidate = _candidate_from_output(envelope)
        self.assertIsNotNone(candidate.memory)
        self.assertEqual(candidate.memory.type, "rule")  # type: ignore[union-attr]
        self.assertNotIn("응답은 OK", candidate.memory.content)  # type: ignore[union-attr]
        self.assertIn("리프레시 토큰", candidate.memory.content)  # type: ignore[union-attr]

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

        raw = json.loads(no_memory_fixture(reason="r"))
        self.assertIsNone(_candidate_from_output(json.dumps(raw)).memory)
        jsonl = json.dumps({"type": "item", "item": {"text": json.dumps(raw, ensure_ascii=False)}})
        self.assertIsNone(_candidate_from_output(jsonl).memory)


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


class JudgeDuplicateReinforceTest(unittest.TestCase):
    """D5: Judge path absorbs duplicate detection and reinforce_memory calls.

    When the same content arrives via the judge a second time, it must not create
    a new memory row but must call reinforce_memory so strength/recurrence rise.
    """

    def _fixture_response(self, content: str, memory_type: str = "directive") -> str:
        return judge_fixture(content, memory_type=memory_type, source_integrity="clean", reason="test duplicate reinforce")

    def _source_event(self, store: Store, *, project_id: str, session_id: str, content: str) -> str:
        return store.add_trace_event(
            session_id=session_id,
            project_id=project_id,
            event_type="memory_source_observed",
            tool_name="memassist",
            input_json={
                "content": content,
                "source_ref": "hook_payload",
                "source_kind": "hook_payload",
            },
        )

    def _judge_result(
        self,
        content: str,
        *,
        memory_type: str = "directive",
        source_integrity: str = "clean",
    ):
        from memassist.memory_judge import MemoryJudgeResult, _candidate_from_output

        fixture = judge_fixture(
            content,
            memory_type=memory_type,
            source_integrity=source_integrity,
            reason="conflict resolver fixture",
        )
        return MemoryJudgeResult(_candidate_from_output(fixture), "fixture", [], {})

    def test_exact_duplicate_via_judge_reinforces_existing_memory(self) -> None:
        """Second judge call with same content reinforces instead of creating new row."""
        from memassist.memory_judge import store_judge_result, MemoryJudgeResult, _candidate_from_output

        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            content = "앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해"

            with Store() as store:
                store.upsert_project(project)
                # First call: stores the memory fresh
                fixture = self._fixture_response(content)
                candidate = _candidate_from_output(fixture)
                result = MemoryJudgeResult(candidate, "fixture", [], {})
                # Need a source event
                source_event_id = store.add_trace_event(
                    session_id="sess_reinforce_test",
                    project_id=project.id,
                    event_type="memory_source_observed",
                    tool_name="memassist",
                    input_json={
                        "content": content,
                        "source_ref": "hook_payload",
                        "source_kind": "hook_payload",
                    },
                )
                first_id = store_judge_result(
                    store,
                    project=project,
                    session_id="sess_reinforce_test",
                    source_event_id=source_event_id,
                    result=result,
                )
                self.assertIsNotNone(first_id)
                first_memory = store.get_memory(first_id)
                self.assertIsNotNone(first_memory)
                initial_strength = first_memory.strength
                initial_recurrence = first_memory.recurrence

                # Second call: same content → should reinforce, not create new
                source_event_id2 = store.add_trace_event(
                    session_id="sess_reinforce_test",
                    project_id=project.id,
                    event_type="memory_source_observed",
                    tool_name="memassist",
                    input_json={
                        "content": content,
                        "source_ref": "hook_payload",
                        "source_kind": "hook_payload",
                    },
                )
                second_id = store_judge_result(
                    store,
                    project=project,
                    session_id="sess_reinforce_test",
                    source_event_id=source_event_id2,
                    result=result,
                )
                # Same memory ID returned (not a new one)
                self.assertEqual(first_id, second_id)

                # Only one memory row with this content
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                matching = [m for m in memories if m.content == content]
                self.assertEqual(len(matching), 1)

                # strength and recurrence must have increased
                reinforced = store.get_memory(first_id)
                self.assertIsNotNone(reinforced)
                self.assertGreater(reinforced.strength, initial_strength,
                    "strength should increase on reinforce")
                self.assertGreater(reinforced.recurrence, initial_recurrence,
                    "recurrence should increase on reinforce")

                # lifecycle events should include a reinforce_duplicate decision
                lifecycle_events = list(store.conn.execute(
                    "SELECT decision FROM lifecycle_events WHERE memory_id = ?", (first_id,)
                ).fetchall())
                decisions = [row[0] for row in lifecycle_events]
                self.assertIn("reinforce_duplicate", decisions)

    def test_exact_duplicate_via_judge_reinforces_across_type_labels(self) -> None:
        """P1: LLM-assigned type labels do not split exact duplicate memories."""
        from memassist.memory_judge import store_judge_result, MemoryJudgeResult, _candidate_from_output

        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            content = "배포 전에는 항상 블루 체크리스트를 확인한다"

            with Store() as store:
                store.upsert_project(project)
                source_event_id = store.add_trace_event(
                    session_id="sess_cross_type_exact",
                    project_id=project.id,
                    event_type="memory_source_observed",
                    tool_name="memassist",
                    input_json={"content": content, "source_ref": "hook_payload"},
                )
                first = MemoryJudgeResult(
                    _candidate_from_output(self._fixture_response(content, memory_type="directive")),
                    "fixture",
                    [],
                    {},
                )
                first_id = store_judge_result(
                    store,
                    project=project,
                    session_id="sess_cross_type_exact",
                    source_event_id=source_event_id,
                    result=first,
                )
                self.assertIsNotNone(first_id)
                initial = store.get_memory(first_id)
                self.assertIsNotNone(initial)

                source_event_id2 = store.add_trace_event(
                    session_id="sess_cross_type_exact",
                    project_id=project.id,
                    event_type="memory_source_observed",
                    tool_name="memassist",
                    input_json={"content": content, "source_ref": "hook_payload"},
                )
                second = MemoryJudgeResult(
                    _candidate_from_output(self._fixture_response(content, memory_type="fact")),
                    "fixture",
                    [],
                    {},
                )
                second_id = store_judge_result(
                    store,
                    project=project,
                    session_id="sess_cross_type_exact",
                    source_event_id=source_event_id2,
                    result=second,
                )

                self.assertEqual(first_id, second_id)
                matching = [
                    memory
                    for memory in store.list_memories(project_id=project.id, include_global=False, status=None)
                    if memory.content == content
                ]
                self.assertEqual(len(matching), 1)
                reinforced = store.get_memory(first_id)
                self.assertGreater(reinforced.recurrence, initial.recurrence)  # type: ignore[union-attr]

    def test_judge_active_decision_is_not_type_whitelisted(self) -> None:
        """P0: active status follows judge/risk signals, not a memory_type whitelist."""
        from memassist.memory_judge import store_judge_result, MemoryJudgeResult, _candidate_from_output

        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()
            content = "규칙 메모리도 judge가 active로 판단하면 active로 저장한다"

            with Store() as store:
                store.upsert_project(project)
                source_event_id = store.add_trace_event(
                    session_id="sess_rule_active",
                    project_id=project.id,
                    event_type="memory_source_observed",
                    tool_name="memassist",
                    input_json={"content": content, "source_ref": "hook_payload"},
                )
                result = MemoryJudgeResult(
                    _candidate_from_output(self._fixture_response(content, memory_type="rule")),
                    "fixture",
                    [],
                    {},
                )
                memory_id = store_judge_result(
                    store,
                    project=project,
                    session_id="sess_rule_active",
                    source_event_id=source_event_id,
                    result=result,
                )
                memory = store.get_memory(memory_id)
                self.assertEqual(memory.status, "active")  # type: ignore[union-attr]

    def test_judge_result_preserves_long_memory_content_without_truncation(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            with patch("sys.stdout", StringIO()):
                main(["embedding", "activate", "none"])
            project = detect_project()
            content = " ".join(
                [
                    "앞으로 인증 정책 변경 시에는 사용자 확인을 먼저 받고,",
                    "변경 이유와 영향 범위를 기록하고,",
                    "릴리스 전 회귀 테스트와 보안 체크리스트를 함께 실행한다.",
                ]
                * 20
            )

            with Store() as store:
                store.upsert_project(project)
                source_event_id = store.add_trace_event(
                    session_id="sess_long_memory",
                    project_id=project.id,
                    event_type="memory_source_observed",
                    tool_name="memassist",
                    input_json={"content": content, "source_ref": "hook_payload"},
                )
                memory_id = store_judge_result(
                    store,
                    project=project,
                    session_id="sess_long_memory",
                    source_event_id=source_event_id,
                    result=self._judge_result(content, memory_type="workflow"),
                )

                memory = store.get_memory(memory_id)
                self.assertIsNotNone(memory)
                self.assertEqual(content, memory.content)  # type: ignore[union-attr]
                self.assertGreater(len(memory.content), 500)  # type: ignore[union-attr]

    def test_semantic_duplicate_via_judge_suppresses_weak_candidate(self) -> None:
        """Weaker candidate with overlapping content is suppressed and existing memory reinforced."""
        from memassist.memory_judge import store_judge_result, MemoryJudgeResult, _candidate_from_output

        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()

            with Store() as store:
                store.upsert_project(project)
                # Pre-existing strong active memory
                active_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content="리프레시 토큰 변경 전에 항상 사용자에게 확인받는다",
                    tags=["isolated_judge", "directive"],
                    status="active",
                    importance=0.85,
                    confidence=0.85,
                )
                active_before = store.get_memory(active_id)
                strength_before = active_before.strength
                recurrence_before = active_before.recurrence

                # Weaker candidate content that overlaps semantically
                weak_content = "리프레시 토큰 변경 전에 사용자에게 확인받아야 한다"
                fixture = judge_fixture(
                    weak_content,
                    memory_type="directive",
                    source_integrity="uncertain",
                    reason="weaker overlapping candidate",
                )
                candidate = _candidate_from_output(fixture)
                result = MemoryJudgeResult(candidate, "fixture", [], {})
                source_event_id = store.add_trace_event(
                    session_id="sess_semantic_test",
                    project_id=project.id,
                    event_type="memory_source_observed",
                    tool_name="memassist",
                    input_json={
                        "content": weak_content,
                        "source_ref": "hook_payload",
                        "source_kind": "hook_payload",
                    },
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": active_id,
                                "relation": "duplicate",
                                "reason": "same durable instruction with different Korean wording",
                            }
                        )
                    },
                ):
                    returned_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_semantic_test",
                        source_event_id=source_event_id,
                        result=result,
                    )

                # No new memory created for weak_content
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                self.assertFalse(
                    any(m.content == weak_content for m in memories),
                    "Weak semantic duplicate should not create a new memory row",
                )

                # If suppression fired, returned_id points to existing active memory
                # and strength/recurrence went up (or a new memory was stored if threshold not met)
                if returned_id == active_id:
                    active_after = store.get_memory(active_id)
                    # strength should be >= before (reinforce was called)
                    self.assertGreaterEqual(active_after.strength, strength_before)
                    lifecycle_events = list(store.conn.execute(
                        "SELECT decision FROM lifecycle_events WHERE memory_id = ?", (active_id,)
                    ).fetchall())
                    decisions = [row[0] for row in lifecycle_events]
                    self.assertIn("reinforce_duplicate", decisions)

    def test_semantic_duplicate_via_judge_suppresses_across_type_labels(self) -> None:
        """P1: semantic duplicate suppression does not gate on compatible type labels."""
        from memassist.memory_judge import store_judge_result, MemoryJudgeResult, _candidate_from_output

        with isolated_env() as (_root, project_dir, _home):
            main(["init"])
            project = detect_project()

            with Store() as store:
                store.upsert_project(project)
                active_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content="배포 전에는 블루 체크리스트를 확인하고 릴리스한다",
                    tags=["deploy", "checklist"],
                    status="active",
                    importance=0.85,
                    confidence=0.85,
                )
                active_before = store.get_memory(active_id)
                weak_content = "배포 전에는 블루 체크리스트를 확인하고 릴리스해야"
                fixture = judge_fixture(
                    weak_content,
                    memory_type="fact",
                    source_integrity="uncertain",
                    reason="weaker overlapping candidate",
                )
                source_event_id = store.add_trace_event(
                    session_id="sess_cross_type_semantic",
                    project_id=project.id,
                    event_type="memory_source_observed",
                    tool_name="memassist",
                    input_json={"content": weak_content, "source_ref": "hook_payload"},
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": active_id,
                                "relation": "duplicate",
                                "reason": "same deploy checklist meaning across type labels",
                            }
                        )
                    },
                ):
                    returned_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_cross_type_semantic",
                        source_event_id=source_event_id,
                        result=MemoryJudgeResult(_candidate_from_output(fixture), "fixture", [], {}),
                    )

                self.assertEqual(returned_id, active_id)
                self.assertFalse(
                    any(
                        memory.content == weak_content
                        for memory in store.list_memories(project_id=project.id, include_global=False, status=None)
                    )
                )
                active_after = store.get_memory(active_id)
                self.assertGreater(active_after.recurrence, active_before.recurrence)  # type: ignore[union-attr]

    def test_conflict_resolver_excludes_other_projects(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            content = "리프레시 토큰 변경 전 사용자에게 확인받는다"
            with Store() as store:
                store.upsert_project(project)
                other_id = store.add_memory(
                    scope_type="project",
                    project_id="other-project",
                    type="directive",
                    content=content,
                    status="active",
                    importance=0.9,
                    confidence=0.9,
                )
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_project_scope",
                    content=content,
                )

                memory_id = store_judge_result(
                    store,
                    project=project,
                    session_id="sess_project_scope",
                    source_event_id=source_event_id,
                    result=self._judge_result(content),
                )

                self.assertNotEqual(memory_id, other_id)
                self.assertEqual(store.get_memory(other_id).recurrence, 1)  # type: ignore[union-attr]
                stored = store.get_memory(memory_id)
                self.assertEqual(stored.project_id, project.id)  # type: ignore[union-attr]
                self.assertEqual(stored.status, "active")  # type: ignore[union-attr]

    def test_conflict_candidate_discovery_does_not_mark_existing_memory_used(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                existing_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="directive",
                    content="package manager는 pnpm을 사용한다",
                    tags=["package"],
                    status="active",
                    importance=0.9,
                    confidence=0.9,
                    retrieval_count=7,
                    utility=0.3,
                    strength=0.6,
                )
                before = store.get_memory(existing_id)
                content = "package manager 설정은 npm을 사용한다"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_read_only_conflict",
                    content=content,
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": existing_id,
                                "relation": "unrelated",
                                "reason": "fixture says no policy relation",
                            }
                        )
                    },
                ):
                    store_judge_result(
                        store,
                        project=project,
                        session_id="sess_read_only_conflict",
                        source_event_id=source_event_id,
                        result=self._judge_result(content),
                    )
                after = store.get_memory(existing_id)
                self.assertEqual(after.retrieval_count, before.retrieval_count)  # type: ignore[union-attr]
                self.assertEqual(after.utility, before.utility)  # type: ignore[union-attr]
                self.assertEqual(after.last_used_at, before.last_used_at)  # type: ignore[union-attr]
                self.assertEqual(after.strength, before.strength)  # type: ignore[union-attr]

    def test_relation_judge_conflict_holds_clean_candidate(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                existing_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="package manager는 pnpm을 사용한다",
                    status="active",
                )
                content = "package manager는 npm을 사용한다"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_conflict_hold",
                    content=content,
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": existing_id,
                                "relation": "conflicts",
                                "reason": "package manager choice conflicts",
                            }
                        )
                    },
                ):
                    memory_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_conflict_hold",
                        source_event_id=source_event_id,
                        result=self._judge_result(content, memory_type="decision"),
                    )
                memory = store.get_memory(memory_id)
                self.assertEqual(memory.status, "candidate")  # type: ignore[union-attr]
                links = store.memory_links(memory_id)
                self.assertTrue(any(link["target_id"] == existing_id and link["relation"] == "conflicts" for link in links))
                decisions = [row["decision"] for row in store.lifecycle_events("sess_conflict_hold")]
                self.assertIn("held_candidate_conflict", decisions)

    def test_clean_candidate_supersedes_existing_project_memory(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                existing_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="테스트는 unittest discover만 실행한다",
                    status="active",
                )
                content = "테스트는 unittest discover와 eval basic을 모두 실행한다"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_supersede_clean",
                    content=content,
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": existing_id,
                                "relation": "candidate_supersedes",
                                "reason": "new verification workflow is more complete",
                            }
                        )
                    },
                ):
                    memory_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_supersede_clean",
                        source_event_id=source_event_id,
                        result=self._judge_result(content, memory_type="workflow"),
                    )
                new_memory = store.get_memory(memory_id)
                old_memory = store.get_memory(existing_id)
                self.assertEqual(new_memory.status, "active")  # type: ignore[union-attr]
                self.assertEqual(old_memory.status, "archived")  # type: ignore[union-attr]
                self.assertEqual(old_memory.superseded_by, memory_id)  # type: ignore[union-attr]

    def test_uncertain_candidate_does_not_supersede_existing_project_memory(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                existing_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="테스트는 unittest discover만 실행한다",
                    status="active",
                )
                content = "테스트는 unittest discover와 eval basic을 모두 실행하는 것 같다"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_supersede_uncertain",
                    content=content,
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": existing_id,
                                "relation": "candidate_supersedes",
                                "reason": "possible replacement but source is uncertain",
                            }
                        )
                    },
                ):
                    memory_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_supersede_uncertain",
                        source_event_id=source_event_id,
                        result=self._judge_result(content, memory_type="workflow", source_integrity="uncertain"),
                    )
                candidate = store.get_memory(memory_id)
                existing = store.get_memory(existing_id)
                self.assertEqual(candidate.status, "candidate")  # type: ignore[union-attr]
                self.assertEqual(existing.status, "active")  # type: ignore[union-attr]
                self.assertIsNone(existing.superseded_by)  # type: ignore[union-attr]

    def test_relation_judge_unavailable_holds_candidate_when_conflict_candidates_exist(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="package manager는 pnpm을 사용한다",
                    status="active",
                )
                content = "package manager는 npm을 사용한다"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_relation_unavailable",
                    content=content,
                )
                with patch.dict(os.environ, {"MEMASSIST_MEMORY_RELATION_JUDGE_MODE": "off"}):
                    memory_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_relation_unavailable",
                        source_event_id=source_event_id,
                        result=self._judge_result(content, memory_type="decision"),
                    )
                memory = store.get_memory(memory_id)
                self.assertEqual(memory.status, "candidate")  # type: ignore[union-attr]
                decisions = [row["decision"] for row in store.lifecycle_events("sess_relation_unavailable")]
                self.assertIn("held_relation_judge_unavailable", decisions)

    def test_zero_overlap_korean_paraphrase_reaches_relation_judge(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                existing_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="preference",
                    content="사용자는 짧은 한국어 답변을 선호한다",
                    status="active",
                )
                content = "간결하게 한글로 답해줘"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_zero_overlap_ko",
                    content=content,
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": existing_id,
                                "relation": "duplicate",
                                "reason": "same durable preference despite no shared tokens",
                            }
                        )
                    },
                ):
                    memory_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_zero_overlap_ko",
                        source_event_id=source_event_id,
                        result=self._judge_result(content, memory_type="preference"),
                    )
                self.assertEqual(memory_id, existing_id)
                memories = store.list_memories(project_id=project.id, include_global=False, status=None)
                self.assertFalse(any(memory.content == content for memory in memories))

    def test_zero_overlap_mixed_language_conflict_reaches_relation_judge(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                existing_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="Use pnpm for JS commands",
                    status="active",
                )
                content = "노드 작업은 npm으로 실행해"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_zero_overlap_conflict",
                    content=content,
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": existing_id,
                                "relation": "conflicts",
                                "reason": "package command tool conflicts across languages",
                            }
                        )
                    },
                ):
                    memory_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_zero_overlap_conflict",
                        source_event_id=source_event_id,
                        result=self._judge_result(content, memory_type="decision"),
                    )
                memory = store.get_memory(memory_id)
                self.assertEqual(memory.status, "candidate")  # type: ignore[union-attr]
                decisions = [row["decision"] for row in store.lifecycle_events("sess_zero_overlap_conflict")]
                self.assertIn("held_candidate_conflict", decisions)

    def test_existing_supersedes_candidate_holds_candidate_and_reinforces_existing(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                existing_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="검증은 full unittest와 eval basic을 실행한다",
                    status="active",
                    confidence=0.8,
                    importance=0.8,
                )
                before = store.get_memory(existing_id)
                content = "검증은 unittest만 실행한다"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_existing_supersedes",
                    content=content,
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": existing_id,
                                "relation": "existing_supersedes",
                                "reason": "existing workflow is more complete",
                            }
                        )
                    },
                ):
                    memory_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_existing_supersedes",
                        source_event_id=source_event_id,
                        result=self._judge_result(content, memory_type="workflow"),
                    )
                candidate = store.get_memory(memory_id)
                existing = store.get_memory(existing_id)
                self.assertEqual(candidate.status, "candidate")  # type: ignore[union-attr]
                self.assertEqual(existing.status, "active")  # type: ignore[union-attr]
                self.assertGreater(existing.recurrence, before.recurrence)  # type: ignore[union-attr]
                decisions = [row["decision"] for row in store.lifecycle_events("sess_existing_supersedes")]
                self.assertIn("held_existing_supersedes_candidate", decisions)

    def test_complementary_relation_stores_memory_and_link(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                existing_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="검증은 unittest discover를 실행한다",
                    status="active",
                )
                content = "검증 후 openspec validate도 실행한다"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_complementary",
                    content=content,
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": existing_id,
                                "relation": "complementary",
                                "reason": "adds another verification step",
                            }
                        )
                    },
                ):
                    memory_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_complementary",
                        source_event_id=source_event_id,
                        result=self._judge_result(content, memory_type="workflow"),
                    )
                memory = store.get_memory(memory_id)
                self.assertEqual(memory.status, "active")  # type: ignore[union-attr]
                links = store.memory_links(memory_id)
                self.assertTrue(
                    any(link["target_id"] == existing_id and link["relation"] == "complementary" for link in links)
                )

    def test_conflict_relation_priority_beats_complementary(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                conflict_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="decision",
                    content="package manager는 pnpm을 사용한다",
                    status="active",
                )
                complementary_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="package manager 변경 후 lockfile을 확인한다",
                    status="active",
                )
                content = "package manager는 npm을 사용하고 lockfile도 확인한다"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_priority_conflict",
                    content=content,
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": complementary_id,
                                "relation": "complementary",
                                "reason": "lockfile step is additive",
                            },
                            {
                                "existing_memory_id": conflict_id,
                                "relation": "conflicts",
                                "reason": "package manager conflicts",
                            },
                        )
                    },
                ):
                    memory_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_priority_conflict",
                        source_event_id=source_event_id,
                        result=self._judge_result(content, memory_type="decision"),
                    )
                memory = store.get_memory(memory_id)
                self.assertEqual(memory.status, "candidate")  # type: ignore[union-attr]
                links = store.memory_links(memory_id)
                self.assertTrue(any(link["target_id"] == conflict_id and link["relation"] == "conflicts" for link in links))
                decisions = [row["decision"] for row in store.lifecycle_events("sess_priority_conflict")]
                self.assertIn("held_candidate_conflict", decisions)

    def test_candidate_supersedes_priority_beats_duplicate(self) -> None:
        from memassist.memory_judge import store_judge_result

        with isolated_env() as (_root, _project_dir, _home):
            main(["init"])
            project = detect_project()
            with Store() as store:
                store.upsert_project(project)
                duplicate_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="검증은 unittest discover를 실행한다",
                    status="active",
                )
                old_id = store.add_memory(
                    scope_type="project",
                    project_id=project.id,
                    type="workflow",
                    content="검증은 unittest만 실행한다",
                    status="active",
                )
                content = "검증은 unittest discover와 openspec validate를 실행한다"
                source_event_id = self._source_event(
                    store,
                    project_id=project.id,
                    session_id="sess_priority_supersede",
                    content=content,
                )
                with patch.dict(
                    os.environ,
                    {
                        "MEMASSIST_MEMORY_RELATION_JUDGE_FIXTURE_RESPONSE": relation_fixture(
                            {
                                "existing_memory_id": duplicate_id,
                                "relation": "duplicate",
                                "reason": "overlaps existing verification memory",
                            },
                            {
                                "existing_memory_id": old_id,
                                "relation": "candidate_supersedes",
                                "reason": "candidate updates older verification rule",
                            },
                        )
                    },
                ):
                    memory_id = store_judge_result(
                        store,
                        project=project,
                        session_id="sess_priority_supersede",
                        source_event_id=source_event_id,
                        result=self._judge_result(content, memory_type="workflow"),
                    )
                new_memory = store.get_memory(memory_id)
                old_memory = store.get_memory(old_id)
                duplicate_memory = store.get_memory(duplicate_id)
                self.assertEqual(new_memory.status, "active")  # type: ignore[union-attr]
                self.assertEqual(old_memory.status, "archived")  # type: ignore[union-attr]
                self.assertEqual(old_memory.superseded_by, memory_id)  # type: ignore[union-attr]
                self.assertEqual(duplicate_memory.status, "active")  # type: ignore[union-attr]

if __name__ == "__main__":
    unittest.main()
