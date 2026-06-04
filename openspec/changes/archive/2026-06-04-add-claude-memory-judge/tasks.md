## 1. Implementation

- [x] 1.1 Generalize `select_memory_judge` to pick the first initialized tool with a judge adapter, with a fixed preference order (codex, then claude); keep the mode/fixture/recursion-guard short-circuits unchanged.
- [x] 1.2 Add `ClaudeMemoryJudge` mirroring `CodexMemoryJudge`: `claude -p "<instruction+payload>" --output-format json`, set `MEMASSIST_MEMORY_JUDGE_ACTIVE=1` and `MEMASSIST_INTERPRETER_ACTIVE=1`, pass `stdin=DEVNULL`, reuse timeout and judge-debug helpers.
- [x] 1.3 Extend `_extract_json_object` to also consider a string `result` field (Claude envelope) alongside `item.text` / `text`, keeping the parser tool-neutral.
- [x] 1.4 Set `llm_directive_interpretation=True` in `claude.py` status capabilities when judge events are installed.
- [x] 1.5 Update README to list Claude Code as a judge-capable backend and note `claude -p` usage.

## 2. Tests

- [x] 2.1 Add a fixture-backed test that a Claude `result`-envelope output is parsed into a `MemoryJudgeCandidate`.
- [x] 2.2 Add a test that `select_memory_judge` returns the Claude adapter when only Claude is initialized, the Codex adapter when Codex is initialized, and `UnavailableMemoryJudge` when neither is.
- [x] 2.3 Add a test that `MEMASSIST_INTERPRETER_ACTIVE=1` (and `MEMASSIST_MEMORY_JUDGE_ACTIVE=1`) yields `UnavailableMemoryJudge` (recursion guard).
- [x] 2.4 Add a regression test that the durable/transient separation holds through the Claude fixture path (`응답은 OK만 해` excluded from stored content; source quote preserved).
- [x] 2.5 Run the full Python unittest suite.

## 3. Evaluation

- [x] 3.1 Run `eval memory` and `eval rag` on the existing `evals/` fixtures; confirm `pass_rate`/`score` are equal-or-better versus `main` and `wrong_promotion_rate` / `policy_leak_rate` are not increased.
- [x] 3.2 Re-implement and repeat tests + evaluation until the quality bar in design.md is met.
- [x] 3.3 Use an independent verification pass to inspect implementation, tests, and evaluation evidence.
