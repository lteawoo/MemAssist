## 1. Implementation

- [x] 1.1 Move `INTERPRETER_ACTIVE_ENV` (name and string value unchanged) into `memory_judge.py`; update `cli.py` and `memory_judge.py` imports.
- [x] 1.2 Delete `src/memassist/interpreter.py`.
- [x] 1.3 Trim `src/memassist/directives.py`: keep `_instruction_tags`, `_normalize_project_files`, `_directive_enforcement`, `_path_terms`, `DIRECTIVE_TERMS`, `GATE_WORDING_TERMS`; drop the `candidate` parameter from `_instruction_tags`; remove the `interpreter` import and all dead directive-handling/policy-compilation helpers and `handle_direct_user_instruction`.
- [x] 1.4 Replace `doctor`'s `directive_interpreter` and `codex_cli` checks with a `memory_judge` backend readiness check (codex/claude, or unavailable); remove the `interpreter_diagnostics` import.
- [x] 1.5 README already describes diagnostics via the judge backend and contains no directive-interpreter / deterministic-fallback active-behavior text; no edit required (verified by grep).

## 2. Tests

- [x] 2.1 Remove tests for `interpret_directive`, `handle_direct_user_instruction`, `interpreter_diagnostics`, and the deterministic fallback.
- [x] 2.2 Update the `doctor` / `tools status` tests to assert judge-backend reporting (claude-only project reports judge ready; no `directive_interpreter` check).
- [x] 2.3 Smoke-import gate (`python -c "import memassist.cli, memassist.memory_judge, memassist.directives, memassist.doctor"`) passes without the removed module.
- [x] 2.4 Run the full Python unittest suite.

## 3. Evaluation

- [x] 3.1 Confirm `grep` finds no `from .interpreter` / `import interpreter` / `handle_direct_user_instruction` in `src/`.
- [x] 3.2 Confirm `eval memory` / `eval rag` / `eval retrieval` are unchanged versus baseline.
- [x] 3.3 Re-implement and repeat tests + evaluation until the quality bar in design.md is met.
- [x] 3.4 Use an independent verification pass to inspect the removal, tests, docs, and evidence. (Found and removed a further dead helper `_path_terms`, its `re` import, and the unused `DIRECTIVE_TERMS` constant flagged during verification.)
