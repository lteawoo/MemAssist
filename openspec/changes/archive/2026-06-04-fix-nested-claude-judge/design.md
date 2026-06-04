## Context

Evidence from the real session (test project DB): three `memory_judged` events, each `adapter_name=claude, candidate=None, diagnostics=['invalid judge output: empty output','judge returncode: 0','judge produced no stdout or stderr']`. A fresh re-run of the exact prompt+instruction returns valid JSON with `should_store=true` on both haiku and sonnet — so the judge did NOT decide "no"; the nested subprocess produced nothing.

Reproduction attempts from the dev shell (2-level nesting) did not reproduce the empty output, even with `CLAUDECODE`/`CLAUDE_CODE_*` set and guard env set. The empty output only occurs in the real 3-level chain (interactive claude → Stop hook → memassist → `claude -p`).

Current behavior: `process_pending_memory_intents` skips sources whose id appears in any `memory_judged` event (`_processed_source_event_ids`). `process_memory_intent_event` records a `memory_judged` event unconditionally (even when `result.candidate is None`). So one failed call permanently marks the source processed.

## Goals / Non-Goals

**Goals:**
- A failed judge call is retried (bounded), never silently dropped.
- The judge subprocess runs as a clean top-level invocation (de-nested).
- Keep CLI-reuse (no API key). Keep the recursion guard.

**Non-Goals:**
- No switch to a direct Anthropic API backend (product constraint).
- No change to what the judge decides, retrieval, or lifecycle.

## Decisions

1. **Failure vs decision.** A `MemoryJudgeResult` with `candidate is not None` is a decision (store or skip) → terminal `memory_judged` event (processed). A result with `candidate is None` is a failure → record a distinct `memory_judge_failed` event that does NOT count as processed, so the next Stop retries.

2. **Bounded retry.** Add `MAX_JUDGE_ATTEMPTS` (3). Before/after a failure, count prior `memory_judge_failed` events for that source in the session. When attempts reach the cap, record a terminal `memory_judged` (decision marker indicating give-up, no memory) so it is marked processed and retrying stops. `_processed_source_event_ids` continues to count only `memory_judged` events.

3. **De-nest the subprocess env.** Extract env construction into a small testable helper that copies `os.environ`, removes every key equal to `CLAUDECODE` or starting with `CLAUDE_CODE_`, then sets the recursion-guard vars (`MEMASSIST_MEMORY_JUDGE_ACTIVE`, `MEMASSIST_INTERPRETER_ACTIVE`). Applied in `_SubprocessMemoryJudge.judge` for all backends (harmless for codex, which does not read those vars; required to fix the nested `claude -p`).

4. **Timeout headroom.** Raise the default `MEMASSIST_MEMORY_JUDGE_TIMEOUT` fallback from 30s to 60s (cold-start `claude -p` can take 15-25s). Still overridable by env.

## Risks / Trade-offs

- [Risk] Fix B may not fully resolve the empty output (root cause unconfirmed without a real 3-level repro).
  -> Mitigation: fix A guarantees resilience (retry, no permanent loss). Real-app re-test confirms B. If B fails, the de-nest helper is the place to iterate (try removing more markers / different invocation).
- [Risk] Unbounded retries waste judge calls/cost.
  -> Mitigation: `MAX_JUDGE_ATTEMPTS` cap; retries only within the same session's later Stops.
- [Risk] Stripping env vars could remove something the judge needs.
  -> Mitigation: only `CLAUDECODE`/`CLAUDE_CODE_*` removed (host-session markers, not needed by a fresh `claude -p`); guard vars and everything else kept.

## Quality bar (apply loop)

- New test (A): a forced judge failure (fixture that yields no candidate, e.g. `{}`) records a `memory_judge_failed` event and NO durable memory; the source remains un-processed (a subsequent `process_pending` re-attempts it); after `MAX_JUDGE_ATTEMPTS` it is marked terminal and stops retrying.
- New test (B): the judge env-build helper removes `CLAUDECODE` and `CLAUDE_CODE_*` keys and still sets the two guard vars.
- New test: a genuine skip decision (candidate present, should_store=false) is terminal (NOT retried).
- Full unittest suite green (pre-existing Windows path-quoting failure / intermittent GUI socket error excepted).
- `eval memory` / `eval rag` / `eval retrieval` unchanged vs baseline.
- Independent verification pass.
- Manual real-app confirmation that the directive now stores (user-run; documents whether B resolved the empty output).
