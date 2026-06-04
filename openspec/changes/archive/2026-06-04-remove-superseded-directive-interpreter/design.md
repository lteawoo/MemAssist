## Context

memassist has two historically parallel LLM mechanisms:

- `interpreter.py` — the directive interpreter (`interpret_directive` → `CodexDirectiveInterpreter` / `FallbackDirectiveInterpreter`), reached only via `directives.handle_direct_user_instruction`.
- `memory_judge.py` — the isolated memory judge (`select_memory_judge` → `CodexMemoryJudge` / `ClaudeMemoryJudge`), reached on the live `UserPromptSubmit` / `Stop` hook flow.

The interpreter was superseded by the judge (archived change `add-isolated-memory-judge`). Reference analysis confirms `handle_direct_user_instruction` has zero callers in `src/` (tests only), and no CLI command or hook invokes it. It is dead code.

A small set of symbols in these modules is still live and must be preserved:

- `INTERPRETER_ACTIVE_ENV` (`interpreter.py`) — imported by `cli.py` and `memory_judge.py`; it is the hook recursion guard (`cmd_hook_event` returns early when set). Load-bearing.
- `_instruction_tags`, `_normalize_project_files` (`directives.py`) — imported by `memory_judge.py`; with transitive helpers `_directive_enforcement`, `_path_terms` and the `DIRECTIVE_TERMS` / `GATE_WORDING_TERMS` constants.

## Goals / Non-Goals

**Goals:**

- Delete the dead directive interpreter and dead directive-handling code with zero change to live behavior.
- Preserve the recursion guard and the judge's helper functions exactly.
- Make `doctor` report the live judge backend (codex or claude) instead of the dead interpreter, removing the codex hardcoding.
- Keep README accurate: no description of the interpreter or deterministic fallback as active behavior.

**Non-Goals:**

- No change to the judge's behavior, retrieval, lifecycle, or policy enforcement.
- No reintroduction of policy compilation from prompts.
- No data migration.

## Decisions

1. **Delete `interpreter.py`; relocate `INTERPRETER_ACTIVE_ENV` into `memory_judge.py`.**

   `memory_judge.py` already defines `JUDGE_ACTIVE_ENV` and sets `INTERPRETER_ACTIVE_ENV` on judge subprocesses. Defining the constant there and importing it from `cli.py` keeps the guard string `"MEMASSIST_INTERPRETER_ACTIVE"` unchanged, so installed hooks and the recursion guard behave identically. The name is retained to avoid churn and preserve the documented env var.

   Alternative considered: introduce a new neutral name (e.g. `HOOK_GUARD_ENV`). Rejected for this change — it would change the env string contract and widen scope without behavior benefit.

2. **Trim `directives.py` to judge helpers only.**

   Keep `_instruction_tags`, `_normalize_project_files`, `_directive_enforcement`, `_path_terms`, `DIRECTIVE_TERMS`, `GATE_WORDING_TERMS`. Drop the `candidate: DirectiveCandidate | None` parameter from `_instruction_tags` (only the dead path used it), which also removes the `interpreter` import from `directives.py`. Delete `handle_direct_user_instruction`, `DirectiveResult`, and the policy-compilation/path-inference helpers used only by it.

3. **Replace `doctor`'s `directive_interpreter` + `codex_cli` checks with a `memory_judge` backend check.**

   Use the judge backend selection (`select_memory_judge` / `status_tools`) to report which tool backs the judge, or that judgment is unavailable. This removes `shutil.which("codex")` hardcoding and the `interpreter_diagnostics` dependency. Keep the check non-fatal (`warn` when no backend), consistent with current doctor semantics.

4. **Remove interpreter/directive tests; update doctor/tools tests.**

   Delete tests that exercise `interpret_directive`, `handle_direct_user_instruction`, `interpreter_diagnostics`, and the deterministic fallback. Update the `doctor` test that asserted `directive_interpreter` / `codex executable not found` to assert judge-backend reporting.

## Risks / Trade-offs

- [Risk] Hidden dynamic reference to a removed symbol.
  -> Mitigation: reference analysis across `src/` shows only the live symbols are imported elsewhere; full unittest suite plus `python -c "import memassist..."` smoke import gate the removal.

- [Risk] Relocating `INTERPRETER_ACTIVE_ENV` breaks the recursion guard.
  -> Mitigation: keep the constant name and string value; assert in tests that a nested judge call (env set) yields `UnavailableMemoryJudge` and that `cmd_hook_event` short-circuits.

- [Risk] Spec conflict with the in-progress `separate-durable-memory-from-turn-instructions` change, which adds to `llm-memory-directive-interpretation`.
  -> Mitigation: that durable/transient concern is already covered for the live judge under `isolated-memory-judgment`; the proposal documents that the interpreter-scoped requirement should be dropped as superseded at archive time.

## Quality bar (pass criteria for the apply loop)

- Full Python unittest suite passes with no new failures versus the pre-change baseline (the pre-existing Windows path-quoting failure and any intermittent GUI socket error are the only allowed exceptions).
- `python -c "import memassist.cli, memassist.memory_judge, memassist.directives, memassist.doctor"` imports cleanly (no reference to removed `interpreter` module).
- `grep` shows no remaining `import interpreter` / `from .interpreter` / `handle_direct_user_instruction` references in `src/`.
- `eval memory`, `eval rag`, `eval retrieval` are unchanged versus baseline (this change does not touch retrieval/lifecycle).
- `doctor --json` reports a judge backend for a claude-only project and does not emit a `directive_interpreter` check.
- README contains no description of the directive interpreter or deterministic fallback as active behavior.
