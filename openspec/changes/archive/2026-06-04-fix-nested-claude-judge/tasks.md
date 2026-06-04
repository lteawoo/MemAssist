## 1. Tests first (fitness)

- [x] 1.1 Test (A): forced judge failure (fixture `{}`) records a failure event and NO durable memory; source NOT processed (re-attempted on next Stop → 2 failures).
- [x] 1.2 Test (A): after `MAX_JUDGE_ATTEMPTS` failures the source is terminally given up (`memory_judged` give-up; no further retry, no memory).
- [x] 1.3 Test (A): a genuine skip (candidate present, should_store=false) is terminal — not retried.
- [x] 1.4 Test (B): the judge env-build helper removes `CLAUDECODE` and `CLAUDE_CODE_*` and still sets both guard vars.

## 2. Implementation

- [x] 2.1 (B) `_judge_subprocess_env()` helper strips `CLAUDECODE`/`CLAUDE_CODE_*` then sets guard vars; used in `_SubprocessMemoryJudge.judge`.
- [x] 2.2 (A) `process_memory_intent_event` distinguishes failure (candidate None, judge ran) from decision; failures recorded as `memory_judge_failed` (not processed) with bounded retry via `MAX_JUDGE_ATTEMPTS`, terminal give-up records `memory_judged`. Added `_record_judge_failure`, `_failed_attempt_count`, `gave_up` flag. `unavailable` adapter stays terminal (no pointless retry).
- [x] 2.3 Raised default `MEMASSIST_MEMORY_JUDGE_TIMEOUT` fallback 30 → 60.

## 3. Verify

- [x] 3.1 New tests pass; existing judge tests still pass (`test_invalid_judge_output_does_not_mutate_policy` updated to drive the cap).
- [x] 3.2 Full unittest suite green (88 tests; only the pre-existing Windows path-quoting failure + intermittent GUI socket error).
- [x] 3.3 `eval memory` / `eval rag` / `eval retrieval` unchanged vs baseline.
- [x] 3.4 Verification: implemented by a Sonnet subagent, independently reviewed (logic + assertions + tests + eval) on Opus.
- [ ] 3.5 (manual, user) Real Claude Code re-test confirms the directive now stores; note whether B resolved the empty output.
