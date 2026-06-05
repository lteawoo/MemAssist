## Why

memassist currently stores judge-extracted memories through ad hoc duplicate checks inside `store_judge_result`, including exact content matching and token-overlap semantic suppression. This leaves contradiction, supersession, complementary memories, and relation audit behavior undefined, while also relying on hardcoded lexical overlap that does not fit the project's multilingual and extensibility goals.

## What Changes

- Add a project-local memory conflict resolver that runs after extraction judge output and before storage mutation.
- Reuse existing memory retrieval/index infrastructure for conflict candidate discovery without using agent context injection rankings or retrieval side effects.
- Add a dedicated relation judge contract for classifying the relationship between a new memory candidate and existing project memories.
- Add deterministic resolution policy that converts relation outputs and `source_integrity` into storage actions.
- Replace `store_judge_result`'s inline exact/semantic duplicate storage decisions with the resolver.
- Persist audit evidence through lifecycle events and memory links for duplicate, complementary, supersession, conflict, and judge-unavailable decisions.
- Keep conflict resolution project-local only; global scope and cross-project memories are out of scope.
- Remove or demote token-overlap semantic duplicate logic so durable relation classification is not driven by language-specific keyword or lexical heuristics.

## Capabilities

### New Capabilities
- `memory-conflict-resolution`: Defines project-local conflict candidate discovery, relation classification, deterministic resolution policy, and audit behavior for judge-extracted memory candidates.

### Modified Capabilities
- None.

## Impact

- Affected modules: `src/memassist/memory_judge.py`, `src/memassist/storage.py`, `src/memassist/retrieval.py`, `src/memassist/dedupe.py`, lifecycle event handling, memory link handling, CLI/GUI surfaces if relation names are exposed.
- Affected tests: unit and e2e coverage for judge storage, duplicate reinforcement, semantic duplicate behavior, lifecycle cleanup, retrieval side effects, and OpenSpec validation.
- No new external runtime dependency is intended.
- Existing stored project memories remain compatible; resolver behavior changes how future judge-extracted memories are stored, reinforced, held, linked, or superseded.
