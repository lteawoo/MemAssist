## 1. Tests first (fitness)

- [x] 1.1 Add a test: non-keyword directive (fixture judge should_store=true) produces NO `memory_judged` at UserPromptSubmit, but IS stored after the Stop hook.
- [x] 1.2 Add a test: trivial task prompt (fixture should_store=false) records a pending source event but stores no durable memory after Stop.

## 2. Implementation

- [x] 2.1 `observe_memory_intent`: record every non-empty prompt as pending source event; drop the `_looks_like_memory_intent` gate and the `immediate` flag/payload.
- [x] 2.2 `cli.py` UserPromptSubmit: remove the inline `process_memory_intent_event` call; keep source-event recording + retrieval/injection.
- [x] 2.3 Remove `_looks_like_memory_intent`, `_looks_like_explicit_directive`; simplify `MemoryIntentEvent`.

## 3. Verify

- [x] 3.1 Ran full suite; updated existing judge tests to drive submit→stop (added `_stop_for` helper); removed same-prompt-injection assertions that the new model makes impossible.
- [x] 3.2 `grep` shows no `_looks_like_memory_intent` / `_looks_like_explicit_directive` / `.immediate` in `src/`.
- [x] 3.3 `eval memory` / `eval rag` / `eval retrieval` unchanged vs baseline.
- [x] 3.4 Full unittest suite green (84 tests; only the pre-existing Windows path-quoting failure remains).
- [x] 3.5 Independent verification pass (6/6 claims PASS).
