## Context

memassist currently retrieves memory during `UserPromptSubmit`, but it also records every non-empty user prompt as a pending source event for later isolated judgment. This keeps inline judgment out of the prompt path, but still makes submit-time behavior both read-side and write-side.

The desired model is simpler: submit-time hooks should retrieve existing memory and inject context, while turn-end hooks should capture source evidence, extract candidate memories, and update lifecycle state. Project-local Markdown memory artifacts are the authoritative memory records. SQLite remains valuable as a rebuildable search/index/telemetry cache, but it should not be treated as the source of truth for memory content or lifecycle.

## Goals / Non-Goals

**Goals:**

- Make `UserPromptSubmit` retrieval-only except for read-side telemetry such as `retrieval_count` and `last_used_at`.
- Move prompt-derived memory capture and judgment to `Stop` or equivalent turn-end processing.
- Keep stored memory focused on future behavior: preferences, directives, decisions, workflows, lessons, and open threads.
- Preserve source evidence for each stored memory using `source_quote` and `source_ref`.
- Make Markdown artifacts authoritative for memory content, source evidence, and lifecycle.
- Keep SQLite as a rebuildable search/index/telemetry cache derived from Markdown.
- Reduce lifecycle complexity for the core load/search/inject loop by centering on `candidate`, `active`, and `archived`.

**Non-Goals:**

- No compact `MEMORY.md` durable summary layer is required.
- No vector database or new external dependency is introduced.
- No autonomous PreToolUse enforcement is reintroduced.
- No full transcript archive is required inside memassist if the host provides a stable source reference.

## Decisions

### Decision 1: Treat submit hooks as read-side retrieval hooks

`UserPromptSubmit` will build a memory pack from existing active memory and may update retrieval telemetry for memories that are returned. It will not create trace events, candidate memories, source events, lifecycle events, or persistent memory rows.

Alternatives considered:

- Keep submit-time pending source events. This preserves current implementation shape but violates the clean read/write boundary and makes submit hooks mutate storage for every prompt.
- Disable all submit side effects, including retrieval telemetry. This is stricter, but it loses useful recall signals that are naturally tied to search execution.

### Decision 2: Capture prompt-derived source evidence at turn end

Turn-end processing will obtain source evidence from hook payload fields, host transcript paths, or other host-provided turn/session references. The isolated judge receives only the source evidence and compact project hints; it still excludes assistant responses, injected memory context, system/developer prompts, reasoning, and full conversation history unless a future requirement explicitly allows additional source types.

Alternatives considered:

- Store full raw prompts at submit time. This is simpler but over-records one-off prompts and contradicts the chosen hook boundary.
- Use assistant responses as memory sources. This increases contamination risk and can store assistant echoes as if they were user preferences.

### Decision 3: Let deterministic policy own lifecycle state

The isolated judge should produce candidate content, type, source quote, source reference, confidence/evidence hints, and risk hints. Deterministic lifecycle policy should decide whether a memory becomes `candidate`, `active`, or `archived`, and should handle duplicate/conflict checks.

Alternatives considered:

- Let the judge choose activation directly. This is flexible but makes lifecycle behavior less testable and harder to align across judge backends.
- Keep all memories as candidates until manual review. This is safe but would make automatic low-risk preference memory too passive.

### Decision 4: Make Markdown artifacts authoritative and SQLite rebuildable

Project-local Markdown artifacts are the system of record for memory content, source evidence, and lifecycle state. For every stored memory, memassist writes or updates a Markdown artifact under `memories/active`, `memories/candidates`, or `memories/archived`. SQLite stores a derived row plus full-text index and retrieval telemetry so prompt hooks can search quickly without parsing Markdown on every prompt. A rebuild/sync path imports Markdown artifacts back into the SQLite cache after manual edits.

Alternatives considered:

- Use SQLite as the authoritative store and mirror Markdown. This is simpler to implement but hides the real memory lifecycle behind a database and makes manual review/editing secondary.
- Parse Markdown during every submit hook. This keeps the cache unnecessary, but it makes prompt submission slower and gives read-side hooks filesystem parsing responsibility.

## Risks / Trade-offs

- Turn-end source extraction may be host-specific -> provide small adapter functions for Codex/Claude/OpenCode payloads and retain graceful no-op behavior when no source is available.
- Markdown and SQLite can drift -> write Markdown first for all memory mutations and provide `memory rebuild-index` to refresh the SQLite cache after manual edits.
- Simplifying lifecycle statuses may conflict with older experimental rows -> no compatibility migration is required because there are no production users for older rows.
- Removing submit-time source events changes retry behavior -> judge retries must operate on turn-end source references and diagnostics instead of pending submit events.

## Migration Plan

1. Stop calling `observe_memory_intent()` from the submit hook.
2. Add turn-end source extraction for the latest user prompt or host transcript anchor.
3. Update isolated judge input construction to use turn-end source evidence.
4. Centralize lifecycle state mapping around `candidate`, `active`, and `archived` without preserving older experimental statuses.
5. Add Markdown artifact writer/parser for stored candidate/active/archived memories.
6. Update storage writes so Markdown is authored first and SQLite is updated as a derived search cache.
7. Add a rebuild-index path for importing Markdown edits into SQLite search state.
8. Update retrieval tests to assert submit-time injection still works and submit-time memory creation no longer occurs.
9. Keep tests focused on the new Markdown-authoritative lifecycle and SQLite index/cache behavior.
