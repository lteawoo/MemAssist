## Why

The isolated memory judge is the only path that turns a user's natural-language directive into durable, source-language, meaning-preserved memory. Heuristic session extraction only captures trace artifacts (test commands, touched files, policy denials), so when the judge is unavailable, memassist degrades from "remembers what you told it" to "remembers which commands it saw run."

Today `select_memory_judge` returns a real backend only when the `codex` tool is initialized. A project that uses Claude Code alone has no working judge: the judge runs at 0% even though the user's tool is installed. This was confirmed empirically — on a Claude-only machine the `codex` executable is absent and every prompt-derived directive is silently dropped from durable memory.

## What Changes

- Generalize `select_memory_judge` so it picks the isolated judge backend from any initialized tool that provides a judge adapter, instead of hardcoding `codex`. The user's installed tool is, at minimum, present.
- Add a `ClaudeMemoryJudge` adapter that runs the isolated judge as a separate `claude -p --output-format json` process, mirroring the existing `CodexMemoryJudge` (`codex exec`) shape.
- Teach the judge output parser to extract the judge JSON from the Claude CLI result envelope (`result` field), alongside the existing Codex JSONL `item`/`text` handling, keeping the parser tool-neutral.
- Mark the Claude integration as capable of `llm_directive_interpretation` once a judge backend is available.
- Preserve the existing recursion guard: the Claude judge subprocess sets `MEMASSIST_INTERPRETER_ACTIVE=1` (and `MEMASSIST_MEMORY_JUDGE_ACTIVE=1`) so child Claude hooks become no-ops. Empirically, `claude -p` does fire its own hooks and does propagate this env to the child hook command, so the guard holds.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `isolated-memory-judgment`: The isolated judge backend SHALL be selected from initialized tools rather than a single hardcoded tool, and memassist SHALL support a Claude Code judge backend that runs in a separate isolated process with recursion protection.

## Impact

- Affected code: `src/memassist/memory_judge.py` (backend selection, new adapter, parser), `src/memassist/integrations/claude.py` (capability flag), tests.
- Affected docs: README sections describing which tools can back the isolated judge.
- No migration is required; there are no existing users to preserve. Codex-backed behavior is unchanged.
- Out of scope: the experimental `claude --json-schema` structured-output mode (it hangs in headless `-p` and is intentionally not used), and any direct Anthropic API backend (would require a separate API key and breaks the "reuse the user's tool auth" model).
