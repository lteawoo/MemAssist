## 1. Submit Hook Boundary

- [x] 1.1 Remove submit-time prompt source event creation from `UserPromptSubmit`.
- [x] 1.2 Keep submit-time memory retrieval and additional context rendering intact.
- [x] 1.3 Ensure retrieval telemetry such as `retrieval_count` and `last_used_at` still updates when memories are returned.
- [x] 1.4 Add regression tests proving `UserPromptSubmit` does not create trace source events, memory candidates, or lifecycle decisions.

## 2. Turn-End Source Extraction

- [x] 2.1 Add a turn-end source extraction helper that reads the latest user source from Stop hook payload fields, transcript paths, or host-specific anchors.
- [x] 2.2 Update isolated memory judgment to consume turn-end source evidence instead of submit-created pending source events.
- [x] 2.3 Preserve source quote and source reference in judge payloads, stored memory records, and lifecycle metadata.
- [x] 2.4 Keep assistant responses, injected memory, system/developer prompts, reasoning, and full conversation history excluded from prompt-derived memory judgment.
- [x] 2.5 Add tests for successful turn-end judgment and graceful no-op behavior when no source evidence is available.

## 3. Lifecycle Policy Simplification

- [x] 3.1 Introduce a deterministic lifecycle policy that maps accepted judge candidates to `candidate`, `active`, or `archived`.
- [x] 3.2 Treat judge activation output as a non-authoritative hint and gate activation through duplicate, conflict, safety, source, scope, and confidence checks.
- [x] 3.3 Keep retrieval eligibility centered on the new active lifecycle bucket instead of legacy status compatibility.
- [x] 3.4 Update memory list/search/pack behavior so only eligible active-equivalent memories are injected.
- [x] 3.5 Add tests for candidate, active, archived, duplicate, and conflict behavior.

## 4. Markdown Memory Artifacts

- [x] 4.1 Extend `memassist init` to create `memories/active`, `memories/candidates`, and `memories/archived` under the project `.memassist` directory.
- [x] 4.2 Add Markdown artifact writer/parser support for stored candidate, active, and archived memories.
- [x] 4.3 Include stable metadata, memory content, source quote, and source reference in each Markdown artifact.
- [x] 4.4 Move or rewrite artifacts when memory status changes between candidate, active, and archived.
- [x] 4.5 Make memory write/update paths author Markdown first and then update SQLite as a derived search cache.
- [x] 4.6 Add `memory rebuild-index` support for importing Markdown edits into SQLite search state.
- [x] 4.7 Add tests proving prompt hooks use SQLite index state without parsing Markdown during `UserPromptSubmit`.
- [x] 4.8 Add tests proving Markdown edits are reflected after rebuilding the SQLite index.

## 5. Migration And Verification

- [x] 5.1 Remove compatibility handling for older submit-time `memory_intent_observed` trace events.
- [x] 5.2 Update CLI and hook tests so submit never creates or relies on legacy source events.
- [x] 5.3 Add end-to-end tests for `init -> submit retrieval -> stop ingestion -> later submit injection`.
- [x] 5.4 Run the full unit test suite and targeted hook lifecycle tests.
- [x] 5.5 Document the simplified memory flow in project docs or command help where appropriate.
