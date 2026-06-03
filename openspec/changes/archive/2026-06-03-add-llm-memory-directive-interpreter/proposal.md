## Why

memassist currently relies on keyword and phrase checks to detect direct user memory directives, so multilingual phrasing, typos, and soft instructions can fail before becoming memory. This undermines the product goal: project behavior should be guided by remembered user/project intent, not brittle keyword rules or universal defaults.

## What Changes

- Add an LLM-backed directive interpreter for `UserPromptSubmit` that converts natural language into structured memory candidates using the coding tool selected during `memassist init`.
- Support multilingual, typo-tolerant, and soft-phrased directives such as asking to be told before changing a project area.
- Produce structured fields for intent, scope, enforcement level, confidence, rationale, and candidate paths.
- Immediately compile high-confidence explicit user/project memory directives into project policy entries; do not introduce universal built-in policy defaults.
- Keep deterministic policy matching as the enforcement layer after memory-derived policy has been activated.
- Add graceful fallback behavior when the initialized tool cannot provide LLM interpretation or the interpreter returns low confidence.
- Add E2E coverage from `init` through the initialized tool's hook execution for memory creation, retrieval, and enforcement behavior.

## Capabilities

### New Capabilities

- `llm-memory-directive-interpretation`: Interprets user prompts into structured memory directives and policy candidates using LLM semantics rather than keyword matching alone.

### Modified Capabilities

- `memory-derived-enforcement`: Requires enforcement policy entries to come from accepted/explicit memory directives, including those produced by the LLM directive interpreter.

## Impact

- Affected code: `src/memassist/cli.py`, `src/memassist/directives.py`, `src/memassist/storage.py`, `src/memassist/policy.py`, `src/memassist/trace.py`, tests under `tests/`.
- Affected integrations: Codex hooks first, with adapters and compatibility expectations documented for Claude Code and opencode where hook surfaces allow equivalent prompt/tool lifecycle events.
- New configuration: initialized-tool interpreter selection, adapter timeout, recursion guard, and fallback controls.
- New tests: unit tests for interpreter outputs, integration tests for immediate policy compilation, and temp-project E2E tests covering the tool selected by `memassist init`.
