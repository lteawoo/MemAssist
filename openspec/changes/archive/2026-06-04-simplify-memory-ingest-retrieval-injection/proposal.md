## Why

memassist memory behavior has grown around multiple lifecycle concepts, but the product goal is simpler: load useful memories, retrieve them before a prompt, and inject only relevant context. This change clarifies the write/read boundary so `UserPromptSubmit` remains retrieval-only while memory ingestion and judgment happen after the turn.

## What Changes

- Stop writing prompt-derived source events during `UserPromptSubmit`.
- Keep `UserPromptSubmit` responsible for memory retrieval, retrieval telemetry, and prompt context injection only.
- Move prompt-derived memory capture to turn-end processing that reads host-provided turn/session source data.
- Simplify initial lifecycle states to `candidate`, `active`, and `archived` for ingestion, retrieval eligibility, and manual cleanup.
- Preserve source evidence (`source_quote` and `source_ref`) for every stored memory.
- Make project-local Markdown memory artifacts the authoritative memory records.
- Keep SQLite as a rebuildable search/index/telemetry cache derived from Markdown memory records.
- Avoid compact durable summary files as a required memory surface; retrieval should operate from original memory records and source evidence.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `retrieval-first-memory`: Clarify that submit-time behavior is retrieval and injection only, while retrieval telemetry may be updated as a read-side effect.
- `isolated-memory-judgment`: Move prompt-derived memory source acquisition from submit-time pending source events to turn-end source extraction, and simplify judge responsibility to candidate extraction plus evidence.
- `project-local-storage`: Make project-local Markdown memory artifacts authoritative and use SQLite as a rebuildable index/cache.

## Impact

- Affected modules: `src/memassist/cli.py`, `src/memassist/memory_judge.py`, `src/memassist/retrieval.py`, `src/memassist/storage.py`, and Markdown artifact helper modules.
- Affected commands/hooks: `memassist init`, `memassist hook user-prompt-submit`, `memassist hook stop`, memory list/search/management commands.
- Data model impact: Markdown artifacts are authoritative for new project memory; SQLite rows are rebuildable index/cache records derived from Markdown.
- No new external dependencies are expected.
