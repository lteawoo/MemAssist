# project-local-storage Specification

## Purpose
Define how memassist scopes a project initialization to the current working directory, treating `<project>/.memassist` as the default project home for memory data, policy, ignore files, and hook-pinned runtime state, while never adopting a parent or user/global `.memassist` as the project root.
## Requirements
### Requirement: memassist SHALL initialize plain directories as local projects
When `memassist init` runs in a directory without `.git` or an existing project `.memassist`, memassist SHALL initialize the current working directory as the project root rather than adopting a parent directory.

#### Scenario: Plain folder init stays local
- **GIVEN** the current directory is `/work/plain-project`
- **AND** `/work/plain-project` has no `.git` and no `.memassist`
- **AND** a parent directory has `.memassist`
- **WHEN** the user runs `memassist init`
- **THEN** memassist SHALL create `/work/plain-project/.memassist`
- **AND** memassist SHALL store the project root as `/work/plain-project`
- **AND** memassist SHALL NOT write project verification config or hooks into the parent directory

### Requirement: memassist SHALL use project `.memassist` as the default project home
For project-scoped initialization, memassist SHALL use `<project>/.memassist` as the default home for project memory data, policy, ignore files, and hook-pinned runtime state.

#### Scenario: Project DB is created under project `.memassist`
- **WHEN** a user runs `memassist init` in a project directory
- **THEN** memassist SHALL create `<project>/.memassist/verification.yaml`
- **AND** memassist SHALL create or use `<project>/.memassist/memassist.db` for project memory storage

#### Scenario: Project hook pins local memory home
- **WHEN** `memassist init --tools codex` installs project-scoped hooks
- **THEN** each memassist hook command SHALL set `MEMASSIST_HOME=<project>/.memassist`
- **AND** hook-time project detection SHALL resolve the hook payload cwd to the initialized project

### Requirement: memassist SHALL ignore user/global `.memassist` as project markers
Project root detection SHALL NOT treat user/global memassist homes as project markers.

#### Scenario: User home `.memassist` is ignored
- **GIVEN** `/Users/example/.memassist` exists
- **AND** the current directory is `/Users/example/projects/plain-project`
- **AND** the current directory has no `.git` and no `.memassist`
- **WHEN** memassist detects the project for initialization
- **THEN** memassist SHALL NOT return `/Users/example` as the project root
- **AND** memassist SHALL return `/Users/example/projects/plain-project`

#### Scenario: Explicit external MEMASSIST_HOME does not redefine project root
- **GIVEN** `MEMASSIST_HOME=/tmp/custom-home`
- **AND** `/Users/example/.memassist` exists
- **AND** the current directory is `/Users/example/projects/plain-project`
- **WHEN** memassist detects the project for initialization
- **THEN** memassist SHALL ignore `/Users/example/.memassist` as a project marker
- **AND** memassist SHALL initialize `/Users/example/projects/plain-project`

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
SQLite SHALL be a derived index/cache for runtime retrieval, duplicate checks, relationship lookup acceleration, and retrieval telemetry. Runtime prompt hooks MAY use SQLite search state for speed, but memory content, source evidence, lifecycle, management reads, import/export behavior, and GUI memory rows SHALL be authoritative from Markdown artifacts. SQLite-only memory rows SHALL be stale derived state and MUST NOT own memory lifecycle.

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

#### Scenario: Management reads ignore SQLite-only rows
- **GIVEN** a memory row exists only in SQLite
- **AND** no Markdown artifact exists for that memory id
- **WHEN** memassist lists, exports, displays, updates, activates, archives, or cleans up managed memories
- **THEN** memassist SHALL NOT treat the SQLite-only row as a managed memory

### Requirement: memassist SHALL keep memory management Markdown-authoritative
Project-local memory management SHALL operate on Markdown artifacts as the authoritative store. After each successful management write, memassist SHALL refresh or reconcile the derived SQLite memory index.

#### Scenario: List uses Markdown artifacts
- **GIVEN** active, candidate, and archived Markdown memory artifacts exist
- **AND** SQLite contains no memory index rows
- **WHEN** a user lists all managed memories
- **THEN** memassist SHALL return the memories represented by the Markdown artifacts
- **AND** memassist MAY rebuild or refresh SQLite as derived index state

#### Scenario: Update writes Markdown first
- **GIVEN** an active Markdown memory artifact exists
- **WHEN** a user updates the memory paths or lifecycle status
- **THEN** memassist SHALL update or move the Markdown artifact first
- **AND** memassist SHALL update SQLite only after the authoritative artifact update succeeds

### Requirement: memassist SHALL export and import Markdown-authoritative memories
Memory export and import SHALL use Markdown artifacts as the authoritative source and destination for managed memory. Export SHALL NOT include SQLite-only rows that lack corresponding Markdown artifacts.

#### Scenario: Export excludes stale SQLite row
- **GIVEN** one active Markdown memory artifact exists
- **AND** one stale SQLite-only memory row exists
- **WHEN** a user exports project memories
- **THEN** the export SHALL include the Markdown-backed memory
- **AND** the export SHALL NOT include the stale SQLite-only row

#### Scenario: Import creates candidate artifacts
- **WHEN** a user imports project memories
- **THEN** memassist SHALL write imported memories as Markdown artifacts under `memories/candidates` unless activation is explicitly requested
- **AND** memassist SHALL refresh the derived SQLite index after writing artifacts
