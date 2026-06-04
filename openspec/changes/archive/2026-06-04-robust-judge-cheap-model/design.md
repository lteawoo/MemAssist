## Context

Spike findings on this machine:
- `claude -p --model haiku --output-format json` works; cost ≈ $0.012–0.017 vs ≈ $0.10 for the session-default model (~7x cheaper), latency 15–22s vs 26s.
- haiku judged the canonical case (`앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.`) correctly: `should_store=true`, durable content only, transient `응답은 OK만 해` excluded.
- haiku wraps the JSON in a ```json fence. The current `_extract_json_object` appends the inner `result` string raw; `json.loads` fails on the fence, and the only `{...}` regex runs on the outer envelope, so the inner judgment is never recovered → "should_store must be boolean".

Greedy `\{.*\}` (DOTALL) over a fenced string `"```json\n{...}\n```"` extracts exactly the inner object, because the backtick fences fall outside the first `{` and last `}`. So fence-stripping is unnecessary — applying the existing extraction to the inner strings suffices.

## Goals / Non-Goals

**Goals:**
- Parse judge JSON regardless of code fences or surrounding prose, for any backend.
- Default the Claude judge to a low-cost model, overridable by env.
- No behavior change for the Codex backend or for already-clean outputs.

**Non-Goals:**
- Changing when/where the judge runs (that is `stop-consolidation-no-gate`).
- Structured-output / `--json-schema` (hangs headless; rejected earlier).
- Codex model pinning (different flag semantics; out of scope).

## Decisions

1. **`_json_candidates(value)` helper.** Returns `[value, <greedy {...} extraction>]`. In `_extract_json_object`, replace the three `candidates.append(<string>)` sites (`item.text`, `text`, `result`) with `candidates.extend(_json_candidates(<string>))`. The existing should_store-preferring final loop and envelope fallback are unchanged.

   Rationale: minimal, tool-neutral; recovers the inner object whether fenced, prose-wrapped, or clean.

2. **Configurable cheap model.** `ClaudeMemoryJudge._command` adds `--model <model>` where `model = os.environ.get("MEMASSIST_MEMORY_JUDGE_MODEL", "haiku")`. The `haiku` alias is used (not a dated full id) to avoid version fragility; both were verified to work.

   Rationale: per-turn judging needs low cost; env override keeps it tunable (e.g., set to `sonnet` for higher quality, or a pinned id).

## Risks / Trade-offs

- [Risk] Greedy `{.*}` over-matches if the model emits multiple objects or trailing prose with `}`.
  -> Mitigation: judge returns a single object; rare. A balanced-brace scan is a possible future hardening, noted not implemented.
- [Risk] `haiku` alias resolution changes across CLI versions.
  -> Mitigation: env override allows pinning a full id; default alias verified working.
- [Risk] Lower-cost model quality regressions on edge prompts.
  -> Mitigation: fixture tests lock parsing; model is overridable; real quality was spot-checked in the spike. Broader quality is exercised by later changes' eval cases.

## Quality bar (apply loop)

- New test: a CLI `result` envelope whose inner JSON is ```json-fenced parses into a valid `MemoryJudgeCandidate` (fails before the fix).
- New test: `ClaudeMemoryJudge._command` includes `--model haiku` by default and honors `MEMASSIST_MEMORY_JUDGE_MODEL`.
- Existing parser tests (raw JSON, Codex JSONL `item.text`, clean `result`) still pass.
- Full unittest suite green (pre-existing Windows path-quoting failure and intermittent GUI socket error excepted).
- `eval memory`/`eval rag`/`eval retrieval` unchanged vs baseline (this change does not touch retrieval/lifecycle).
