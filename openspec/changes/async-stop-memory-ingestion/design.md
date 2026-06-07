## Context

memassist hooks run inside Codex, Claude Code, and OpenCode user flows. The current Stop hook records trace data and then synchronously processes pending prompt-derived memory through isolated LLM judgment, optional relation judgment, and lifecycle cleanup. Those judge subprocesses can legitimately take longer than the host hook timeout, which makes turn completion feel blocked and can prevent memassist from recording useful diagnostics.

The project principle is retrieval-first memory. A memory extracted from a finished turn is useful, but it does not need to be committed before the host tool can finish the current turn. The authoritative source evidence can be captured quickly and processed later.

## Goals / Non-Goals

**Goals:**
- Keep Stop hook latency independent of LLM judge latency.
- Preserve source-ledger durability: if Stop observes user source evidence, it must be available for later ingestion.
- Process pending source records in a background/one-shot worker with bounded batch size and retries.
- Keep `memassist init --tools ...` as the main user setup flow.
- Report async ingestion state and failures through existing diagnostics.
- Preserve a synchronous/debug mode for tests and local troubleshooting.

**Non-Goals:**
- Do not introduce a long-running external service dependency.
- Do not change the retrieval-first model into policy enforcement.
- Do not guarantee that a memory from the just-finished turn is available in the immediately next prompt if the worker has not finished yet.
- Do not redesign the isolated memory judge prompt or conflict semantics beyond moving execution out of the Stop critical path.

## Decisions

1. **Stop hook becomes enqueue-first by default.**
   Stop will record the normal stop trace event and source-ledger evidence, request async ingestion, and return. This keeps the host hook path short. Alternative considered: increase hook timeout. That still leaves users waiting and cannot reliably cover external CLI latency.

2. **Use a detached one-shot worker instead of requiring a resident daemon.**
   Stop can spawn `memassist daemon once --session <id>` without waiting. The worker exits after a bounded batch. This avoids install-time service setup while still moving expensive work outside the hook. Alternative considered: require users to run a daemon manually. That would violate the desired `init`-only user flow.

3. **Use a project-local lock for duplicate-run protection.**
   A lock under `.memassist/` prevents overlapping workers in the same project. The lock has a TTL so a crashed worker does not block ingestion forever. Alternative considered: SQLite advisory locking. File locking is simpler and portable enough for local project use.

4. **Keep sync and off modes as explicit escape hatches.**
   `MEMASSIST_STOP_INGEST_MODE=async` is the default. `sync` preserves current behavior for focused debugging and selected tests. `off` records source evidence but does not spawn or process ingestion.

5. **Mode semantics remain product-facing.**
   `full` enables retrieval, trace capture, Stop source enqueue, and async memory ingestion. `trace` records trace and Stop events without memory ingestion. `context` performs prompt retrieval only.

6. **Worker batch size is bounded.**
   The worker processes a small number of pending source records per run. This prevents a backlog from causing a single background process to monopolize local resources. Additional Stop hooks can spawn later workers when the lock is free.

## Risks / Trade-offs

- [Risk] A memory may not be available immediately on the very next prompt. → The source ledger preserves evidence, and diagnostics show pending ingestion; this is preferable to blocking turn completion.
- [Risk] Detached workers may fail silently. → Record worker start/end/error trace events and append logs under `.memassist/` when possible.
- [Risk] Multiple Stop hooks may race. → Use a project-local lock with TTL and bounded batches.
- [Risk] Existing tests expect synchronous storage at Stop. → Add explicit sync mode for those tests or drive `memassist daemon once` in the test.
- [Risk] A worker launched from a hook could trigger nested hooks through judge subprocesses. → Preserve existing judge recursion guards for child tool processes.

## Migration Plan

- New installs default to async Stop ingestion.
- Existing project hook files are updated when users run `memassist tools repair` or reinstall integrations.
- Rollback is setting `MEMASSIST_STOP_INGEST_MODE=sync` for current synchronous behavior or `off` to disable automatic ingestion.
