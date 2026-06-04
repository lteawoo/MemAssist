## 1. Tests first (fitness)

- [x] 1.1 Add a test: a CLI `result` envelope whose inner JSON is ```json-fenced parses into a valid `MemoryJudgeCandidate` (durable content, transient excluded).
- [x] 1.2 Add a test: `ClaudeMemoryJudge._command` includes `--model haiku` by default and honors `MEMASSIST_MEMORY_JUDGE_MODEL`.

## 2. Implementation

- [x] 2.1 Add `_json_candidates(value)` helper (raw + greedy `{...}` extraction); use it for the `item.text` / `text` / `result` candidate strings in `_extract_json_object`. Keep should_store-preferring loop + envelope fallback.
- [x] 2.2 Add `--model` to `ClaudeMemoryJudge._command`, default `haiku`, overridable via `MEMASSIST_MEMORY_JUDGE_MODEL`. Keep instruction last for `_debug_command` masking.

## 3. Verify

- [x] 3.1 New tests pass; existing parser tests (raw, JSONL, clean result) still pass; updated the older command-shape test for the added `--model`.
- [x] 3.2 Full unittest suite green (82 tests; only the pre-existing Windows path-quoting failure remains).
- [x] 3.3 `eval memory` / `eval rag` / `eval retrieval` unchanged vs baseline.
- [x] 3.4 Self-verification (fully test-covered small change; subagent verification reserved for later behavioral changes).
