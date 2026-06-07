## Why

Stop hooks currently run memory extraction, isolated LLM judgment, optional relation judgment, and lifecycle cleanup synchronously. That can exceed Codex or Claude hook timeouts and makes normal agent use feel blocked at turn end.

## What Changes

- Change Stop hook behavior so it records turn-end source evidence and returns quickly.
- Move prompt-derived memory judgment, relation judgment, and lifecycle cleanup out of the Stop hook critical path.
- Add a background/one-shot ingestion worker that processes pending source records after the hook returns.
- Add bounded worker execution and duplicate-run protection so multiple Stop hooks do not launch overlapping expensive judgment work.
- Add diagnostics for pending async ingestion and worker failures.
- Preserve a synchronous/debug path for tests and manual troubleshooting.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `memory-source-ledger`: Stop hooks must enqueue source records without waiting for LLM memory ingestion.
- `isolated-memory-judgment`: Prompt-derived isolated judgment must be eligible for asynchronous background processing, with bounded retries and diagnostics.
- `retrieval-first-memory`: Memory created asynchronously must become retrievable after background ingestion without blocking prompt submission or turn completion.

## Impact

- Affected code:
  - `src/memassist/cli.py`
  - `src/memassist/memory_judge.py`
  - `src/memassist/conflict_resolver.py`
  - `src/memassist/doctor.py`
  - `src/memassist/integrations/*`
  - tests covering hooks, daemon processing, diagnostics, and E2E retrieval
- User-facing behavior:
  - `memassist init --tools codex,claude` remains the main setup flow.
  - Stop hook latency should no longer depend on LLM judge latency.
  - Newly observed memories may appear on the next prompt only after the async worker has processed them.
- No new external service dependency is introduced.
