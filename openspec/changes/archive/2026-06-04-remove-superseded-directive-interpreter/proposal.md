## Why

The LLM-backed directive interpreter (`src/memassist/interpreter.py` plus the directive-handling path in `src/memassist/directives.py`) was superseded by the isolated memory judge. The archived change `add-isolated-memory-judge` moved durable-memory creation off the `UserPromptSubmit` hot path into the judge specifically because the interpreter could distort user intent with contaminated context and compiled policy too eagerly.

The interpreter is now dead code: its only entry point, `handle_direct_user_instruction`, is not called anywhere in `src/` (only in tests), and no CLI command or hook invokes it. The live hook flow uses `observe_memory_intent` → the isolated judge. The deterministic fallback interpreter and the `CodexDirectiveInterpreter` adapter never run in production.

This dead code also drives a stale, misleading diagnostic: `doctor` reports `directive_interpreter` readiness (hardcoded to `codex`) for a mechanism that no longer runs, and a `codex_cli` check that ignores the now-supported Claude judge backend. A Claude-only user sees "codex executable not found" even though their judge works.

## What Changes

- Remove `src/memassist/interpreter.py` entirely. Relocate the still-live recursion-guard constant `INTERPRETER_ACTIVE_ENV` (consumed by `cli.py` and `memory_judge.py`) to `memory_judge.py`, preserving its name and string value so the hook recursion guard behaves identically.
- Trim `src/memassist/directives.py` to only the helpers the isolated judge still uses (`_instruction_tags`, `_normalize_project_files`, and their transitive helpers `_directive_enforcement`, `_path_terms`, plus the `DIRECTIVE_TERMS` / `GATE_WORDING_TERMS` constants). Drop the dead `handle_direct_user_instruction` path, policy-compilation helpers, and the `DirectiveCandidate` dependency from `_instruction_tags`.
- Replace `doctor`'s `directive_interpreter` and `codex_cli` checks with a single judge-backend readiness check derived from the actual judge backend selection (codex or claude), so diagnostics reflect the live mechanism and stop hardcoding codex.
- Remove the interpreter/directive-handling tests and update the `doctor`/`tools status` tests to assert judge-backend reporting.
- Update README to remove descriptions of the directive interpreter and deterministic fallback as active behavior, and describe diagnostics in terms of the judge backend.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `isolated-memory-judgment`: Diagnostics SHALL report isolated judge backend readiness (which initialized tool backs the judge, or that none is available) instead of reporting a separate directive interpreter.

### Removed Capabilities

- `llm-memory-directive-interpretation`: The separate LLM directive interpreter and its deterministic fallback are removed. Durable memory derived from user directives is produced solely by the isolated memory judge (`isolated-memory-judgment`).

## Impact

- Affected code: `src/memassist/interpreter.py` (deleted), `src/memassist/directives.py` (trimmed), `src/memassist/doctor.py` (judge-backend check), `src/memassist/cli.py` and `src/memassist/memory_judge.py` (import relocation), tests, README.
- No live behavior is lost: the interpreter and its fallback were already unreachable from the hook flow. Only dormant, test-only machinery is removed.
- Dependency note: the in-progress change `separate-durable-memory-from-turn-instructions` adds a requirement to `llm-memory-directive-interpretation`. That concern (separating durable memory from one-shot turn instructions) is already covered for the live mechanism by its delta on `isolated-memory-judgment`. When this removal is archived, the interpreter-scoped requirement should be dropped as superseded rather than retained.
- No migration is required for stored data; there are no existing users to preserve.
