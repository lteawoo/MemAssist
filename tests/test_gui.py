from __future__ import annotations

import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from memassist.cli import main
from memassist.project import detect_project
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


class GuiApiClient:
    """Small adapter for either a pure GUI API helper or a stdlib HTTP handler."""

    def __init__(self, gui_module: Any) -> None:
        self.gui = gui_module
        self.server: Any | None = None
        self.thread: threading.Thread | None = None
        self.base_url: str | None = None
        self.helper = self._find_helper()
        if self.helper is None:
            self._start_http_server()

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def get_json(self, path: str) -> tuple[int, dict[str, str], Any]:
        status, headers, body = self.request("GET", path)
        self._assert_json_headers_or_body(headers, body)
        return status, headers, json.loads(body)

    def request(self, method: str, path: str) -> tuple[int, dict[str, str], str]:
        if self.helper is not None:
            return self._request_helper(method, path)
        return self._request_http(method, path)

    def _find_helper(self) -> Any | None:
        for name in (
            "api_response",
            "handle_api_request",
            "route_api",
            "get_api_response",
            "get_api_payload",
        ):
            helper = getattr(self.gui, name, None)
            if callable(helper):
                return helper
        return None

    def _request_helper(self, method: str, path: str) -> tuple[int, dict[str, str], str]:
        helper = self.helper
        assert helper is not None
        try:
            raw = helper(path, method=method)
        except TypeError:
            if method != "GET":
                return 405, {"content-type": "application/json"}, json.dumps({"error": "method not allowed"})
            raw = helper(path)
        return self._coerce_response(raw)

    def _start_http_server(self) -> None:
        import http.server

        handler = None
        for name in ("GuiRequestHandler", "MemassistRequestHandler", "RequestHandler"):
            candidate = getattr(self.gui, name, None)
            if isinstance(candidate, type) and issubclass(candidate, http.server.BaseHTTPRequestHandler):
                handler = candidate
                break
        if handler is not None:
            self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        else:
            self.server = self._make_server_from_factory()
        if self.server is None:
            raise unittest.SkipTest(
                "memassist.gui needs an API helper such as api_response(path) "
                "or a stdlib HTTP handler/server factory for GUI API tests."
            )
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _make_server_from_factory(self) -> Any | None:
        for name in ("create_server", "make_server"):
            factory = getattr(self.gui, name, None)
            if not callable(factory):
                continue
            for args, kwargs in (
                ((), {"host": "127.0.0.1", "port": 0}),
                (("127.0.0.1", 0), {}),
                ((("127.0.0.1", 0),), {}),
            ):
                try:
                    server = factory(*args, **kwargs)
                except TypeError:
                    continue
                if hasattr(server, "serve_forever") and hasattr(server, "server_address"):
                    return server
        return None

    def _request_http(self, method: str, path: str) -> tuple[int, dict[str, str], str]:
        assert self.base_url is not None
        data = None if method == "GET" else b"{}"
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
                return response.status, {key.lower(): value for key, value in response.headers.items()}, body
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            return error.code, {key.lower(): value for key, value in error.headers.items()}, body

    def _coerce_response(self, raw: Any) -> tuple[int, dict[str, str], str]:
        status = 200
        headers: dict[str, str] = {}
        body = raw
        if isinstance(raw, tuple):
            if len(raw) == 3:
                status, headers, body = raw
            elif len(raw) == 2:
                first, second = raw
                if isinstance(first, int):
                    status, body = first, second
                else:
                    body, status = first, second
        if hasattr(body, "as_dict"):
            body = body.as_dict()
        if isinstance(body, (dict, list)):
            headers.setdefault("content-type", "application/json")
            body = json.dumps(body)
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        return int(status), {str(key).lower(): str(value) for key, value in dict(headers).items()}, str(body)

    def _assert_json_headers_or_body(self, headers: dict[str, str], body: str) -> None:
        content_type = headers.get("content-type", "")
        if content_type:
            testcase = unittest.TestCase()
            testcase.assertIn("application/json", content_type)
        json.loads(body)


class GuiReadOnlyApiTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.gui = importlib.import_module("memassist.gui")
        except ModuleNotFoundError as exc:
            if exc.name == "memassist.gui":
                raise unittest.SkipTest("memassist.gui has not been implemented yet.") from exc
            raise

    def test_read_only_api_endpoints_return_json(self) -> None:
        with isolated_env() as (_root, project_dir, _home):
            project = self._seed_project()
            client = GuiApiClient(self.gui)
            try:
                payloads: dict[str, Any] = {}
                for endpoint in ("/api/summary", "/api/projects", "/api/memories", "/api/policy", "/api/tools"):
                    status, _headers, payload = client.get_json(endpoint)
                    self.assertEqual(status, 200, endpoint)
                    self.assertIsInstance(payload, (dict, list), endpoint)
                    payloads[endpoint] = payload

                self.assertTrue(
                    _contains_text(payloads["/api/projects"], str(project_dir))
                    or _contains_text(payloads["/api/projects"], project.id)
                )
                self.assertTrue(_contains_text(payloads["/api/memories"], "Keep GUI API read only."))
                self.assertTrue(_contains_text(payloads["/api/policy"], "src/locked.py"))
                for tool in ("codex", "claude", "opencode"):
                    self.assertTrue(_contains_text(payloads["/api/tools"], tool), tool)
                self.assertTrue(_has_summary_signal(payloads["/api/summary"]))
            finally:
                client.close()

    def test_api_does_not_expose_destructive_methods(self) -> None:
        with isolated_env():
            self._seed_project()
            self._assert_no_destructive_route_metadata()
            client = GuiApiClient(self.gui)
            try:
                for endpoint in ("/api/summary", "/api/projects", "/api/memories", "/api/policy", "/api/tools"):
                    for method in ("POST", "PUT", "PATCH", "DELETE"):
                        status, _headers, _body = client.request(method, endpoint)
                        self.assertIn(status, {404, 405, 501}, f"{method} {endpoint}")
            finally:
                client.close()

    def test_memory_api_exposes_read_only_priority_and_relevance_metrics(self) -> None:
        with isolated_env():
            self._seed_project()
            client = GuiApiClient(self.gui)
            try:
                status, _headers, payload = client.get_json("/api/memories")
                self.assertEqual(status, 200)
                self.assertEqual(payload.get("metric"), "priority")
                memories = payload.get("memories") or []
                self.assertGreaterEqual(len(memories), 2)
                for memory in memories:
                    metrics = memory.get("metrics") or {}
                    evidence = metrics.get("evidence") or {}
                    self.assertIsInstance(metrics.get("priority"), int)
                    self.assertGreaterEqual(metrics["priority"], 0)
                    self.assertLessEqual(metrics["priority"], 100)
                    self.assertIsNone(metrics.get("relevance"))
                    for key in ("confidence", "strength", "utility", "uses", "recurrence"):
                        self.assertIn(key, evidence)

                status, _headers, searched = client.get_json("/api/memories?q=locked")
                self.assertEqual(status, 200)
                self.assertEqual(searched.get("metric"), "relevance")
                searched_memories = searched.get("memories") or []
                self.assertTrue(searched_memories)
                relevance_scores = [memory["metrics"]["relevance"] for memory in searched_memories]
                self.assertEqual(relevance_scores, sorted(relevance_scores, reverse=True))
                self.assertTrue(all(isinstance(score, int) for score in relevance_scores))
            finally:
                client.close()

    def test_dashboard_html_exposes_localized_metric_help_without_score_label(self) -> None:
        html = self.gui._dashboard_html()
        self.assertIn('id="locale"', html)
        self.assertIn("localStorage", html)
        self.assertIn("document.documentElement.lang", html)
        self.assertIn("Priority", html)
        self.assertIn("우선순위", html)
        self.assertIn("Relevance", html)
        self.assertIn("관련도", html)
        self.assertIn("priorityTooltip", html)
        self.assertIn("relevanceTooltip", html)
        self.assertNotIn('["Type", "Content", "Paths", "Score", "Updated"]', html)

    def test_localization_dictionary_covers_english_and_korean_metric_terms(self) -> None:
        i18n = getattr(self.gui, "GUI_I18N")
        required = {
            "priority",
            "relevance",
            "confidence",
            "strength",
            "utility",
            "uses",
            "recurrence",
            "priorityTooltip",
            "relevanceTooltip",
        }
        for locale in ("en", "ko"):
            self.assertIn(locale, i18n)
            missing = [key for key in required if not i18n[locale].get(key)]
            self.assertEqual(missing, [], locale)

    def _seed_project(self) -> Any:
        self.assertEqual(main(["init", "--tools", "codex"]), 0)
        policy_path = Path.cwd() / ".memassist" / "policy.yaml"
        with policy_path.open("a", encoding="utf-8") as file:
            file.write('\nprotected_paths:\n  - "src/locked.py"\n')
        project = detect_project()
        with Store() as store:
            store.upsert_project(project)
            store.add_memory(
                scope_type="project",
                project_id=project.id,
                type="fact",
                content="Keep GUI API read only.",
                tags=["gui", "readonly"],
                paths=["src/memassist/gui.py"],
                status="active",
            )
            store.add_memory(
                scope_type="project",
                project_id=project.id,
                type="rule",
                content="Blocked memory policy for locked files.",
                tags=["policy"],
                paths=["src/locked.py"],
                status="block_policy",
                enforcement="block",
            )
            store.add_trace_event(
                session_id="sess_gui_readonly",
                project_id=project.id,
                event_type="tool",
                tool_name="read",
                input_json={"path": "src/memassist/gui.py"},
                files=["src/memassist/gui.py"],
            )
        return project

    def _assert_no_destructive_route_metadata(self) -> None:
        destructive_terms = {
            "add",
            "cleanup",
            "create",
            "delete",
            "import",
            "install",
            "mutate",
            "promote",
            "remove",
            "repair",
            "uninstall",
            "update",
            "write",
        }
        route_metadata = []
        for name in ("API_ROUTES", "ROUTES", "READ_ONLY_API_ROUTES"):
            routes = getattr(self.gui, name, None)
            if routes:
                route_metadata.append(routes)
        for routes in route_metadata:
            rendered = json.dumps(routes, default=str).lower()
            for term in destructive_terms:
                self.assertNotIn(term, rendered)
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertNotIn(method.lower(), rendered)


def _contains_text(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return expected in value
    if isinstance(value, dict):
        return any(_contains_text(key, expected) or _contains_text(item, expected) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_text(item, expected) for item in value)
    return expected in str(value)


def _has_summary_signal(value: Any) -> bool:
    keys = _collect_keys(value)
    summary_keys = {
        "denied_events",
        "event_count",
        "memory_count",
        "memories",
        "project",
        "projects",
        "session",
        "session_id",
        "tools",
    }
    return bool(keys & summary_keys)


def _collect_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        for item in value.values():
            keys.update(_collect_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_collect_keys(item))
        return keys
    return set()
