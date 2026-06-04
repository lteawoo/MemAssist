## 1. Implementation

- [x] 1.1 Update isolated judge instructions to separate durable memory from one-shot turn instructions.
- [x] 1.2 Store `memory_content` as the primary memory content while preserving `source_quote` in metadata/reason.
- [x] 1.3 Update docs to describe source evidence preservation versus retrievable durable content.

## 2. Tests

- [x] 2.1 Add fixture-backed regression tests for Korean mixed durable/transient prompts.
- [x] 2.2 Add retrieval regression checks proving transient phrases are not injected later.
- [x] 2.3 Run the full Python test suite.

## 3. Evaluation

- [x] 3.1 Run actual Codex CLI E2E in `/Users/twlee/projects/test` from init through memory ingestion. (Manual one-time run; superseded by the committed opt-in automated E2E in 3.4 since the manual run left no reproducible artifact.)
- [x] 3.2 Confirm evaluation metric: stored/retrieved durable memory contains 0 one-shot response-format phrases for the target prompt set.
- [x] 3.3 Use an independent verification pass to inspect implementation, tests, and E2E evidence. (Independent pass: implementation sound — `memory_content` stored as primary at `memory_judge.py:462`, judge prompt excludes one-shot text at `:302-305`, `source_quote→tags` leak ruled out via vocabulary-bounded `_instruction_tags`; all three spec scenarios covered by `test_mixed_prompt_stores_only_durable_memory_content` / `test_english_mixed_prompt_excludes_transient_response_text_from_search` / parser tests; full suite 89 ran with only an unrelated pre-existing Windows hook-quoting failure split to a separate issue. Gap found: 3.1/3.2 had no committed E2E artifact — closed by 3.4.)
- [x] 3.4 Add a committed opt-in automated E2E (`test_real_codex_mixed_prompt_stores_no_transient_response_text`) that submits the mixed Korean prompt through real Codex and asserts 0 transient response-format phrases in stored content, FTS retrieval, and injected RAG context. Runs under `MEMASSIST_RUN_REAL_CODEX_E2E=1` in an environment with the `codex` CLI; skips otherwise.
