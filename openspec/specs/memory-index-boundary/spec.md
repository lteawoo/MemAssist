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
