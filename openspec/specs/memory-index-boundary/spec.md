# memory-index-boundary Specification

## Purpose
Define the boundary between authoritative Markdown memory artifacts and the rebuildable SQLite memory index/cache used for retrieval acceleration and telemetry.
## Requirements
### Requirement: memassist SHALL keep SQLite as a derived memory index only
memassist SHALL treat SQLite memory rows, FTS tables, relationship rows, and retrieval telemetry as a derived index/cache rather than authoritative memory state. Managed memory content, source evidence, and lifecycle SHALL be recoverable from Markdown artifacts without relying on SQLite memory rows.

#### Scenario: SQLite deletion does not delete managed memory
- **GIVEN** a project has active, candidate, and archived Markdown memory artifacts
- **AND** `<project>/.memassist/memassist.db` is deleted
- **WHEN** memassist rebuilds the memory index
- **THEN** the rebuilt index SHALL contain search/index rows derived from the Markdown artifacts
- **AND** the managed memory content, source evidence, and lifecycle buckets SHALL remain unchanged

#### Scenario: SQLite-only memory is not authoritative
- **GIVEN** a row exists in the SQLite memory index
- **AND** no corresponding Markdown memory artifact exists under `memories/active`, `memories/candidates`, or `memories/archived`
- **WHEN** memassist lists, exports, displays, updates, or retrieves managed memories after index reconciliation
- **THEN** memassist SHALL treat the SQLite-only row as stale derived state
- **AND** memassist SHALL NOT present it as an authoritative memory

### Requirement: memassist SHALL perform memory management through Markdown artifacts
memassist SHALL perform memory create, get, list, update, activate, archive, import, export, and cleanup operations against Markdown artifacts as the authoritative data source before refreshing derived SQLite index state.

#### Scenario: Memory update survives index rebuild
- **GIVEN** an active Markdown memory artifact exists
- **WHEN** a memory management command updates its content, paths, tags, type, or lifecycle bucket
- **THEN** memassist SHALL write the Markdown artifact update first
- **AND** rebuilding the SQLite index SHALL preserve the updated memory state

#### Scenario: Archive moves the authoritative artifact
- **GIVEN** a candidate Markdown memory artifact exists under `memories/candidates`
- **WHEN** memassist activates and then archives that memory
- **THEN** memassist SHALL move or rewrite the authoritative artifact under the matching lifecycle bucket each time
- **AND** SQLite SHALL be refreshed only as derived index state

### Requirement: memassist SHALL remove legacy memory lifecycle dead code
memassist SHALL NOT keep active runtime branches for legacy memory statuses outside the current `candidate`, `active`, and `archived` lifecycle unless another active requirement explicitly names that status. Legacy database-only statuses MUST NOT affect retrieval eligibility, lifecycle cleanup, or memory management behavior.

#### Scenario: Legacy status row is ignored
- **GIVEN** a SQLite memory index row has a legacy status such as `draft`, `pinned`, `disabled`, `deleted`, `expired`, `superseded`, or `durable`
- **AND** no active requirement explicitly defines that status
- **WHEN** memassist reconciles the index with Markdown artifacts
- **THEN** memassist SHALL NOT preserve special runtime behavior for that legacy status
- **AND** the row SHALL NOT become retrieval-eligible unless an authoritative active Markdown artifact exists

#### Scenario: Compatibility branch is removed
- **WHEN** the implementation no longer has production users for older lifecycle models
- **THEN** memassist SHALL remove compatibility-only code paths instead of translating old database-only memory states

### Requirement: memassist SHALL keep retrieval telemetry non-authoritative
memassist SHALL allow retrieval telemetry such as `retrieval_count`, `last_used_at`, utility adjustments, and ranking strength adjustments to live in SQLite. Such telemetry SHALL NOT be required to reconstruct authoritative memory content, source evidence, or lifecycle from Markdown artifacts.

#### Scenario: Rebuild loses telemetry without losing memory
- **GIVEN** an active Markdown memory artifact has been retrieved multiple times
- **AND** SQLite contains retrieval telemetry for that memory
- **WHEN** SQLite is deleted and rebuilt from Markdown artifacts
- **THEN** memassist MAY reset retrieval telemetry
- **AND** memassist SHALL preserve the memory content, source evidence, and active lifecycle bucket

#### Scenario: Prompt retrieval may update telemetry
- **GIVEN** an active Markdown memory artifact has been indexed into SQLite
- **WHEN** prompt retrieval returns that memory
- **THEN** memassist MAY update SQLite retrieval telemetry for ranking and metrics
- **AND** memassist SHALL NOT require a Markdown artifact rewrite solely because of that read-side telemetry update

### Requirement: memassist SHALL index memories by artifact hash

memassist SHALL track a content hash or equivalent fingerprint for each indexed Markdown memory artifact. SQLite index rows, FTS rows, and embedding rows SHALL be considered derived from the artifact version identified by that hash.

#### Scenario: Rebuild refreshes changed artifact
- **GIVEN** an active Markdown memory artifact has been edited by a user
- **AND** SQLite contains an index row for an older content hash
- **WHEN** memassist rebuilds the memory index
- **THEN** memassist SHALL update the derived index row to the artifact's current content hash
- **AND** search SHALL use index text derived from the edited artifact

#### Scenario: Stale hash prevents authoritative use
- **GIVEN** SQLite contains a derived index row whose content hash no longer matches the Markdown artifact
- **WHEN** memassist reconciles or rebuilds the index
- **THEN** memassist SHALL treat the Markdown artifact as authoritative
- **AND** memassist SHALL refresh or replace the stale derived row

### Requirement: memassist SHALL keep embeddings as rebuildable derived cache

If memassist stores embedding vectors, those vectors SHALL be treated as a derived cache keyed by memory id, artifact content hash, provider, model, and dimension. Embedding cache rows SHALL NOT be authoritative memory content.

#### Scenario: Embedding cache invalidates on content change
- **GIVEN** a Markdown memory artifact has an embedding cache row for a previous content hash
- **WHEN** the artifact content changes
- **THEN** memassist SHALL NOT use the stale embedding row for vector ranking
- **AND** memassist MAY regenerate the embedding for the new content hash when a provider is configured

#### Scenario: Missing embeddings do not remove memory
- **GIVEN** an active Markdown memory artifact exists
- **AND** no embedding cache row exists for that memory
- **WHEN** memassist rebuilds or searches the memory index
- **THEN** the memory SHALL remain eligible for FTS and metadata retrieval
- **AND** memassist SHALL NOT archive or delete the memory because embeddings are missing

### Requirement: memassist SHALL avoid treating SQLite index text as managed memory content

SQLite MAY store derived index text for FTS or vector indexing, but managed memory reads, exports, edits, activation, archival, and deletion SHALL operate on Markdown artifacts.

#### Scenario: Export uses artifact content instead of index text
- **GIVEN** a Markdown memory artifact exists
- **AND** SQLite contains derived FTS index text for that memory
- **WHEN** memassist exports managed memories
- **THEN** exported memory content and source evidence SHALL come from the Markdown artifact
- **AND** the FTS index text SHALL NOT replace the artifact content

### Requirement: memassist SHALL remove superseded memory migration residue

After this change is implemented, memassist SHALL NOT keep compatibility-only runtime branches, dead lifecycle statuses, or alternate SQLite-owned memory content paths that are superseded by the Markdown-authoritative lightweight memory core. Temporary migration helpers MAY exist only when they are directly exercised by implementation tasks and MUST be removed before the change is complete.

#### Scenario: Legacy lifecycle translation is absent
- **GIVEN** the implementation has moved to Markdown-authoritative memory status buckets
- **WHEN** memassist handles retrieval, lifecycle cleanup, activation, archival, export, import, or GUI memory reads
- **THEN** memassist SHALL NOT translate legacy database-only statuses into managed memory behavior
- **AND** only `candidate`, `active`, and `archived` Markdown-backed lifecycle states SHALL affect managed memory behavior

#### Scenario: Compatibility-only branches are deleted before completion
- **GIVEN** a code path exists only to preserve a superseded memory storage model
- **AND** no active requirement still depends on that code path
- **WHEN** this change is considered implementation-complete
- **THEN** that code path SHALL be deleted rather than left as dormant compatibility code

#### Scenario: SQLite-owned content path is absent
- **GIVEN** SQLite contains derived index text or stale memory rows
- **WHEN** memassist performs managed memory operations
- **THEN** memassist SHALL NOT use SQLite-owned content as a fallback authoritative memory body
- **AND** missing Markdown artifacts SHALL cause the SQLite-derived row to be ignored or pruned

### Requirement: memassist SHALL key embedding cache rows by profile identity

Embedding cache rows SHALL include the active profile id and a stable profile fingerprint in addition to memory id, artifact content hash, provider, model, dimension, and indexed timestamp. Cache rows SHALL remain derived state and SHALL NOT be authoritative memory content.

#### Scenario: Different profiles can cache the same memory
- **GIVEN** a memory artifact has content hash `abc`
- **AND** profiles `potion-int8` and `bge-m3-dense` both build embeddings for that memory
- **WHEN** memassist stores embedding cache rows
- **THEN** both profile cache rows SHALL be able to coexist
- **AND** retrieval with one profile SHALL NOT use the other profile's vector row

#### Scenario: Profile setting change invalidates old cache
- **GIVEN** a profile changes model, quantization, dimension, normalization, or text prefix settings
- **WHEN** memassist computes the profile fingerprint
- **THEN** the fingerprint SHALL differ from cache rows built with the previous settings
- **AND** memassist SHALL NOT use the previous fingerprint's rows for current vector ranking

### Requirement: memassist SHALL ignore stale profile cache rows

memassist SHALL reconcile embedding cache rows against authoritative Markdown artifacts, current artifact content hashes, memory lifecycle status, and profile identity before vector ranking.

#### Scenario: Content hash mismatch is ignored
- **GIVEN** an embedding cache row matches the active profile
- **AND** the row content hash differs from the current Markdown artifact content hash
- **WHEN** vector retrieval reads embedding rows
- **THEN** memassist SHALL ignore the stale row
- **AND** memassist MAY rebuild the embedding for the active profile

#### Scenario: Candidate profile cache is not injected as active context
- **GIVEN** a candidate memory has an embedding row for the active profile
- **WHEN** prompt retrieval builds active memory context
- **THEN** memassist SHALL NOT inject the candidate memory as active context
- **AND** the cache row SHALL remain derived state until lifecycle changes make the memory active

### Requirement: memassist SHALL clean up embedding cache by profile

memassist SHALL provide a way to remove derived embedding cache rows for a selected profile without deleting Markdown memory artifacts or unrelated profile caches.

#### Scenario: Profile cache cleanup preserves memories
- **GIVEN** a project has Markdown memory artifacts
- **AND** embedding cache rows exist for profile `potion-int8`
- **WHEN** the user cleans up cache for `potion-int8`
- **THEN** memassist SHALL delete derived cache rows for that profile
- **AND** memassist SHALL NOT delete Markdown memory artifacts
- **AND** memassist SHALL NOT delete cache rows for other profiles unless explicitly requested

