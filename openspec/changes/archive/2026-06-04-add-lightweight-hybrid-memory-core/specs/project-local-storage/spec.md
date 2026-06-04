## ADDED Requirements

### Requirement: memassist SHALL initialize a lightweight project-local source ledger

Project initialization SHALL create or prepare a lightweight source ledger under the project `.memassist` home. The source ledger SHALL be scoped to the initialized project and SHALL be independent from user-global memory homes.

#### Scenario: Init prepares source ledger
- **WHEN** a user runs `memassist init` in a project directory
- **THEN** memassist SHALL prepare a project-local source ledger under `<project>/.memassist`
- **AND** future project-scoped hooks SHALL write source evidence to that project-local ledger

#### Scenario: Hook-pinned home uses project ledger
- **WHEN** project-scoped hooks run with `MEMASSIST_HOME=<project>/.memassist`
- **THEN** Stop-time source evidence SHALL be recorded in the project-local source ledger
- **AND** it SHALL NOT be recorded in a parent or global `.memassist` home

### Requirement: memassist SHALL keep vector cache project-local and optional

Any vector embedding cache used for memory retrieval SHALL live under the project `.memassist` home or project-local SQLite database. Vector cache absence SHALL NOT prevent project initialization or memory retrieval.

#### Scenario: Init succeeds without embedding provider
- **WHEN** a user runs `memassist init`
- **AND** no embedding provider is configured
- **THEN** initialization SHALL succeed
- **AND** memory retrieval SHALL be able to run with FTS-only fallback

#### Scenario: Embedding cache stays local
- **WHEN** memassist generates or stores memory embeddings for a project
- **THEN** the embedding cache SHALL be stored in the project `.memassist` home
- **AND** the cache SHALL be treated as derived state that can be rebuilt or deleted without deleting Markdown memories
