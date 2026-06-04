## Context

The isolated memory judge (`memory_judge.py`) is the core of memassist's value: it converts a user's natural-language directive into a structured, durable memory while excluding contaminated context (assistant responses, retrieved memory, system prompts). The judge runs as a separate process so its input can be strictly limited to the user source event plus compact project hints.

`select_memory_judge` currently returns `CodexMemoryJudge` only when the `codex` tool is initialized. Claude Code is a fully supported integration (`integrations/claude.py`) — it installs the same hook events and emits the same `additionalContext` / decision contract — but it has no judge adapter, so Claude-only projects get no judge.

The judge's input/output contract is already tool-neutral: `_candidate_from_output` parses JSON, `store_judge_result` never inspects the tool name. The gap is only "how the LLM process is launched and how its JSON is located in the output."

## Spike findings (empirical, this environment)

- `codex` is not installed here; `claude` is. So the current judge runs at 0% — confirming the motivating problem.
- `claude -p "<instr>" --output-format json` returns an envelope `{"type":"result",...,"result":"<model text>"}`. The judge JSON lives inside the `result` field as a string. The existing parser handles Codex's `item.text`/`text` and a regex fallback, but not `result`, so it would parse the envelope and fail on `should_store`.
- A real judge prompt over `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.` returned all 10 fields, `should_store=true`, and correctly excluded `응답은 OK만 해` from `memory_content` — satisfying the in-flight durable/transient separation spec.
- `--json-schema` hangs in headless `-p` (exit 124 / timeout). Not used.
- `claude -p` fires its own `UserPromptSubmit` and `Stop` hooks in the child session, and the parent-set `MEMASSIST_INTERPRETER_ACTIVE=1` propagated into the child hook command (observed value `[1]`). So the existing recursion guard (`cmd_hook_event` returns 0 when `MEMASSIST_INTERPRETER_ACTIVE==1`) holds for Claude. Without the guard, recursion is guaranteed; the guard is load-bearing.

## Goals / Non-Goals

**Goals:**

- A Claude-only project gets a working isolated judge with no extra configuration beyond `memassist init --tools claude`.
- Backend selection generalizes: "first initialized tool with a judge adapter," not "is codex installed."
- Codex behavior is unchanged.
- The output parser stays tool-neutral (one added field, not a Claude-specific branch in storage).
- Recursion is impossible via the existing env guard.

**Non-Goals:**

- No `--json-schema` dependency (hangs in headless mode).
- No direct Anthropic API backend (needs a separate key; breaks tool-auth reuse). Documented as a future option only.
- No change to heuristic lifecycle extraction or retrieval.
- No migration for existing memories.

## Decisions

1. **Generalize `select_memory_judge` to an ordered adapter lookup.**

   Replace the `tool == "codex"` check with a registry/order of judge adapters keyed by tool name, returning the first whose tool is initialized. Order: `codex`, then `claude` (Codex first preserves current behavior for projects that have both).

   Rationale: the user's instinct — "the tool the user uses is at minimum installed" — makes installed-tool selection the natural generalization. A new tool becomes a one-line registry entry, not a special case.

2. **`ClaudeMemoryJudge` mirrors `CodexMemoryJudge`.**

   Same instruction text and payload. Command: `claude -p "<instruction+payload>" --output-format json`. Set `env[MEMASSIST_MEMORY_JUDGE_ACTIVE]=1` and `env[MEMASSIST_INTERPRETER_ACTIVE]=1`. Provide `stdin=DEVNULL` (otherwise the CLI waits ~3s for stdin). Reuse the same timeout env (`MEMASSIST_MEMORY_JUDGE_TIMEOUT`) and debug-writing helpers.

   Rationale: consistency with the existing adapter minimizes new surface and reuses all diagnostics.

3. **Extend `_extract_json_object` to also consider the `result` field.**

   When a parsed JSON line is a dict without `should_store`, in addition to `item.text` and `text`, append `event.get("result")` (when it is a string) to the candidate list. This keeps a single tool-neutral parser.

   Alternative considered: parse the envelope inside `ClaudeMemoryJudge` and pass only the inner string. Rejected because it duplicates JSON-locating logic; the shared parser already exists for exactly this.

4. **`claude.py` reports `llm_directive_interpretation` capability.**

   `status()` SHALL set `llm_directive_interpretation=True` when judge events are installed, so `tools status` / `doctor` reflect that Claude can back the judge. (Capability reflects adapter availability, consistent with how Codex is treated.)

5. **No sandbox flag for the Claude judge (MVP).**

   Codex uses `--sandbox read-only`. Claude headless has no exact equivalent we depend on, and the judge prompt is read-only by construction. Recursion (not file writes) is the real risk, and that is covered by the env guard.

## Risks / Trade-offs

- [Risk] In the real Claude hook execution path the guard env might not propagate to the child `claude -p` the way it did in the shell spike.
  -> Mitigation: the adapter sets the env explicitly on the subprocess (not relying on inheritance from an ambient shell); integration test asserts a nested judge call short-circuits.

- [Risk] `claude -p` cold start (~3s) adds latency to immediate judgment on `UserPromptSubmit`.
  -> Mitigation: acceptable for MVP; immediate judgment only fires for explicit directives, and batch judgment at `Stop` covers the rest. Timeout is bounded by `MEMASSIST_MEMORY_JUDGE_TIMEOUT`.

- [Risk] The real LLM may emit prose around the JSON or wrap it in a code fence.
  -> Mitigation: the parser already strips and regex-falls-back to `{...}`; fixtures cover fenced and enveloped outputs.

- [Risk] A Claude judge that fails (timeout, invalid JSON) could silently drop a directive.
  -> Mitigation: same as Codex — diagnostics are recorded as a `memory_judged` trace event and optional judge-debug JSONL; no durable memory is written on failure.

## Quality bar (pass criteria for the apply loop)

- Full Python unittest suite passes.
- New fixture-backed tests pass: (a) Claude `result`-envelope output is parsed into a candidate; (b) backend selection returns the Claude adapter when only Claude is initialized and the Codex adapter when Codex is initialized; (c) a nested call with `MEMASSIST_INTERPRETER_ACTIVE=1` yields `UnavailableMemoryJudge`.
- `eval memory` and `eval rag` on the existing `evals/` fixtures show no regression versus `main` (equal or better `pass_rate`/`score`, `wrong_promotion_rate` and `policy_leak_rate` not increased).
- The durable/transient separation scenario (`응답은 OK만 해` excluded) holds through the Claude fixture path.
