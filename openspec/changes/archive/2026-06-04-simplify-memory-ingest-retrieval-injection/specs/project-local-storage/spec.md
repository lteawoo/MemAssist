## ADDED Requirements

### Requirement: memassist SHALL create project-local Markdown memory artifact directories
When project-scoped initialization creates or uses `<project>/.memassist`, memassist SHALL create directories for authoritative Markdown memory artifacts. These artifacts SHALL live under the project `.memassist` home and SHALL be the source of truth for memory content, source evidence, and lifecycle state.

#### Scenario: Init creates Markdown artifact directories
- **WHEN** a user runs `memassist init` in a project directory
- **THEN** memassist SHALL create `<project>/.memassist/memories/active`
- **AND** memassist SHALL create `<project>/.memassist/memories/candidates`
- **AND** memassist SHALL create `<project>/.memassist/memories/archived`
- **AND** memassist SHALL continue to create or use `<project>/.memassist/memassist.db` as a rebuildable search/index/telemetry cache

### Requirement: memassist SHALL author memories as Markdown artifacts
When memassist stores or updates a memory, it SHALL write a Markdown artifact containing stable memory metadata, memory content, and source evidence before updating the SQLite search cache. The artifact SHALL be human-readable, suitable for project-local review, and authoritative for memory state.

#### Scenario: Active memory has a Markdown artifact
- **WHEN** memassist stores an active memory for a project
- **THEN** memassist SHALL write a Markdown artifact under `<project>/.memassist/memories/active`
- **AND** the artifact SHALL include the memory content
- **AND** the artifact SHALL include stable metadata such as id, scope, type, status, confidence, tags, paths, source quote, and source reference

#### Scenario: Candidate memory has a Markdown artifact
- **WHEN** memassist stores a candidate memory for a project
- **THEN** memassist SHALL write a Markdown artifact under `<project>/.memassist/memories/candidates`
- **AND** the artifact SHALL include the memory content and source evidence

### Requirement: memassist SHALL use SQLite as a rebuildable runtime index
SQLite SHALL be a derived index/cache for runtime retrieval, duplicate checks, and retrieval telemetry. Runtime prompt hooks SHALL use SQLite search state for speed, but memory content, source evidence, and lifecycle SHALL be rebuildable from Markdown artifacts.

#### Scenario: Runtime uses SQLite index state
- **GIVEN** an active Markdown memory artifact has been indexed into SQLite
- **WHEN** memassist handles `UserPromptSubmit`
- **THEN** memassist SHALL retrieve memory using SQLite search/index state
- **AND** memassist SHALL NOT require parsing Markdown artifacts during the prompt hook

#### Scenario: Rebuild index imports Markdown edits
- **GIVEN** a user edits an active Markdown memory artifact
- **WHEN** memassist rebuilds the memory index from artifacts
- **THEN** SQLite search state SHALL reflect the edited Markdown memory content
- **AND** later prompt retrieval SHALL use the rebuilt SQLite index

#### Scenario: Rebuild index prunes deleted Markdown memories
- **GIVEN** a memory exists only in the SQLite cache after its Markdown artifact was removed
- **WHEN** memassist rebuilds the memory index from artifacts
- **THEN** the stale SQLite memory cache row SHALL be removed from retrieval eligibility
