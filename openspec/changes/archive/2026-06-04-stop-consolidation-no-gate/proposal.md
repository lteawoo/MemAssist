## Why

The live ingestion path is gated by `_looks_like_memory_intent`, a keyword whitelist (`앞으로/먼저/기억/...`). This is exactly the "brittle keyword rule" the project set out to remove (archived `add-llm-memory-directive-interpreter`): natural directives like `리프레시토큰 변경에 승인확인하라` never reach the judge and are silently dropped, while Claude's own memory captures them. The keyword gate now solely decides what becomes memory — the opposite of the project's intent that the judge (LLM), not keywords, makes that call.

Separately, judging runs inline at `UserPromptSubmit`, blocking the user's prompt with multi-second judge latency. With a low-cost judge model now available (`robust-judge-cheap-model`), judging can move to turn end (Stop) and run on every prompt affordably.

## What Changes

- Record every non-empty user prompt as a pending source event, with no keyword pre-filter and no immediacy flag.
- Stop judging at `UserPromptSubmit`: that hook becomes read-only (retrieve + inject). Durable memory is produced only at turn end (Stop), where the judge already processes pending source events.
- Remove the keyword gate (`_looks_like_memory_intent`) and the immediate/explicit-directive heuristic (`_looks_like_explicit_directive`) and the `immediate` flag.
- Update tests that assumed inline judgment to drive the full turn (UserPromptSubmit then Stop). Add fitness tests: a non-keyword directive is captured at Stop; a trivial task is not stored.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `isolated-memory-judgment`: Prompts SHALL be recorded without keyword gating and judged at turn end (Stop), not inline at UserPromptSubmit; whether a prompt yields durable memory is decided by the judge, not a keyword rule.

## Impact

- Affected code: `src/memassist/memory_judge.py` (`observe_memory_intent`, remove `_looks_like_*`, `MemoryIntentEvent`), `src/memassist/cli.py` (UserPromptSubmit no longer judges inline), tests.
- Behavior: durable memory from a prompt becomes available from the next turn's retrieval (after this turn's Stop), not within the same turn. Prompt latency drops (no inline judge).
- Cost: the judge runs per turn at Stop; affordable due to the low-cost model from `robust-judge-cheap-model`.
- No schema change. Heuristic trace lifecycle and retrieval unchanged.
