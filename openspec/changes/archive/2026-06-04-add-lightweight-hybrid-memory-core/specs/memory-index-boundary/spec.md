## ADDED Requirements

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
