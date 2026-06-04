## Why

Moving memory WRITE to a per-turn Stop consolidation (later changes) only works if the isolated judge is cheap enough to run on every turn. Spike results: a small model (`claude --model haiku`) judges durable/transient separation correctly at roughly 1/7 the cost and lower latency than the session-default model.

But the cheap model exposed a parser gap: small models wrap their JSON answer in a markdown code fence (` ```json … ``` `). The current `_extract_json_object` only runs `{...}` extraction on the outer CLI envelope, not on the inner `result`/`text` string, so a fenced inner object fails to parse and the judgment is silently dropped. The session-default model happened not to fence, which masked the bug.

This change hardens the parser and makes the judge model a low-cost, configurable default. It is the enabler for per-turn judging.

## What Changes

- Make `_extract_json_object` tolerant of judge output that wraps the JSON object in a markdown code fence or surrounding prose, by applying `{...}` extraction to the inner string candidates (`result`, `text`, `item.text`), not only the outer envelope.
- Run the Claude judge backend with a configurable low-cost model, defaulting to `haiku`, via an environment override (`MEMASSIST_MEMORY_JUDGE_MODEL`). Codex backend behavior is unchanged.
- Add regression coverage for a fenced `result` envelope and for the model flag wiring.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `isolated-memory-judgment`: Judge output parsing SHALL tolerate code-fenced or prose-wrapped JSON, and the Claude judge SHALL use a configurable low-cost model by default.

## Impact

- Affected code: `src/memassist/memory_judge.py` (`_extract_json_object`, `ClaudeMemoryJudge`), tests.
- No schema or data change. Codex path unchanged.
- Enables the subsequent `stop-consolidation-no-gate` change (per-turn judging needs a cheap, reliably-parsed judge).
