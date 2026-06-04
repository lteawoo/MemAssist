## Why

Real-app testing showed the Claude judge silently fails in the actual usage path. In a live Claude Code session, the Stop hook spawns the judge as `claude -p`, but that grandchild process returns empty output (returncode 0, no stdout) — recorded as `invalid judge output: empty output`. The same command works when run standalone, so the failure is specific to the deeply-nested invocation. Two defects compound:

1. **Permanent loss on failure.** When the judge returns no candidate (empty output, timeout, error), memassist still records a terminal `memory_judged` event, which marks the source event as processed. It is never retried, so a transient failure drops the user's directive forever.
2. **Nested invocation suppresses output.** The judge subprocess inherits the host Claude Code session's environment markers (`CLAUDECODE`, `CLAUDE_CODE_*`), which appear to make the nested `claude -p` produce no output.

The product constraint is fixed: the judge must reuse the user's installed CLI tool (codex/claude) — no separate API key (regular users do not configure API keys). So the fix keeps CLI-reuse and addresses both defects.

## What Changes

- **A (retry, robustness):** Distinguish a genuine judge decision (candidate present, store or skip) from a judge-call failure (no candidate). On failure, do not mark the source processed — leave it pending so the next Stop retries — bounded by a maximum attempt count, after which it is given up terminally.
- **B (de-nest):** Strip Claude Code session markers (`CLAUDECODE`, `CLAUDE_CODE_*`) from the judge subprocess environment so the nested `claude -p` runs as a clean top-level invocation. Keep the recursion-guard env vars.
- Raise the default judge timeout to reduce spurious cold-start timeouts (still overridable).

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `isolated-memory-judgment`: A failed judge call SHALL be retried at later turn ends rather than permanently dropped, and the judge subprocess SHALL run de-nested from the host tool session.

## Impact

- Affected code: `src/memassist/memory_judge.py` (subprocess env build, judge result recording, pending retry), tests.
- No schema change.
- Verification dependency: defect (2) could only be reproduced inside a real 3-level nested session, not from the development shell. Fix B is the strongest hypothesis (env markers) and MUST be confirmed by a real Claude Code re-test; fix A makes the system resilient regardless of the root cause.
