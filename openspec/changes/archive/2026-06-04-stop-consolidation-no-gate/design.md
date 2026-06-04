## Context

Current flow:
- `UserPromptSubmit` → `observe_memory_intent` (keyword-gated; sets `immediate`) → if immediate, `process_memory_intent_event` (inline judge) → then retrieve + inject.
- `Stop` → `process_pending_memory_intents` (judges pending source events, skips already-judged) + `process_session_lifecycle` (heuristic trace extraction).

The pending→Stop judging mechanism already exists and is incremental (per-turn, dedup via `_processed_source_event_ids`). The change is to (a) stop gating which prompts get recorded, and (b) stop judging inline.

## Goals / Non-Goals

**Goals:**
- The judge, not a keyword list, decides what becomes memory.
- No prompt-path latency from judging.
- Every prompt is considered (no silent keyword-miss drop).

**Non-Goals:**
- Changing salience/lifecycle/candidate handling (that is `salience-lifecycle-and-candidates`).
- Removing `enforcement` (that is `purge-enforcement-vocabulary`).
- Batching multiple prompts into one judge call (possible later cost optimization).

## Decisions

1. **`observe_memory_intent` records unconditionally.** Drop the `_looks_like_memory_intent` gate; record any non-empty prompt as a `memory_intent_observed` source event. Remove the `immediate` flag from the event payload and `MemoryIntentEvent`.

2. **UserPromptSubmit does not judge.** Remove the `if source_event and source_event.immediate: process_memory_intent_event(...)` block. The hook still records the source event and does retrieval + injection.

3. **Stop is the sole WRITE point.** `process_pending_memory_intents` (unchanged) judges all pending source events at turn end. Per-turn cost is bounded by the low-cost judge model.

4. **Delete dead heuristics.** Remove `_looks_like_memory_intent` and `_looks_like_explicit_directive` (no remaining callers).

5. **Tests reflect the full turn.** Tests that asserted judged memory after only `UserPromptSubmit` now drive `Stop` as well (submit → stop → assert). This mirrors real Claude Code, where Stop fires at turn end.

## Risks / Trade-offs

- [Risk] Judging every prompt increases judge calls (most return should_store=false).
  -> Mitigation: low-cost model (prior change); batching is a future option. Trivial-prompt rejection is validated by a fitness test.
- [Risk] Same-turn freshness lost (a directive isn't stored until that turn's Stop).
  -> Accepted: retrieval reflects it from the next turn, which is the intended model. Inline same-turn availability had no value (retrieval already ran before the inline judge).
- [Risk] Large test churn.
  -> Mitigation: run the suite to enumerate breakage; update affected tests to drive Stop; the change is mechanical and behavior-faithful.

## Quality bar (apply loop)

- New fitness test: a non-keyword directive (e.g. `리프레시토큰 변경에 승인확인하라`, fixture judge returns should_store=true) is NOT recorded/judged at UserPromptSubmit time, but IS stored after the Stop hook runs.
- New fitness test: a trivial task prompt (fixture judge should_store=false) records a pending source event but stores no durable memory after Stop.
- New test: `UserPromptSubmit` produces no `memory_judged` event (judging deferred to Stop).
- Updated existing judge tests drive submit→stop.
- `grep` shows no `_looks_like_memory_intent` / `_looks_like_explicit_directive` / `.immediate` in `src/`.
- Full unittest suite green (pre-existing Windows path-quoting failure / intermittent GUI socket error excepted).
- `eval memory` / `eval rag` / `eval retrieval` unchanged vs baseline.
- Independent verification pass.
