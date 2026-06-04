## ADDED Requirements

### Requirement: memassist SHALL use hybrid retrieval as the default memory retrieval engine

memassist SHALL expose one default memory retrieval behavior that combines exact lexical retrieval with semantic vector retrieval when vector search is configured and indexed. If vector search is unavailable, memassist SHALL degrade to lexical retrieval without failing prompt submission.

#### Scenario: Hybrid retrieval combines lexical and vector candidates
- **GIVEN** active Markdown-backed memories are indexed for FTS
- **AND** vector embeddings are configured and available for some active memories
- **WHEN** a UserPromptSubmit query is processed
- **THEN** memassist SHALL collect lexical candidates
- **AND** memassist SHALL collect vector candidates when vector search is available
- **AND** memassist SHALL fuse candidates into one ranked memory pack

#### Scenario: FTS-only fallback works
- **GIVEN** active Markdown-backed memories are indexed for FTS
- **AND** no embedding provider or valid embedding cache is available
- **WHEN** a UserPromptSubmit query is processed
- **THEN** memassist SHALL retrieve and inject relevant memories using lexical, metadata, path, and link signals
- **AND** memassist SHALL NOT fail solely because vector search is unavailable

### Requirement: memassist SHALL preserve exact-code recall in hybrid retrieval

Hybrid retrieval SHALL preserve exact matches for paths, commands, symbols, tags, and error text even when semantic vector search is available.

#### Scenario: Exact path match remains retrievable
- **GIVEN** an active memory references `src/auth/session.py`
- **WHEN** a prompt mentions `src/auth/session.py`
- **THEN** memassist SHALL retrieve the path-matching memory even if vector results rank other memories semantically nearby

#### Scenario: Verification command remains retrievable
- **GIVEN** an active workflow memory says `Run pytest tests/test_memassist.py`
- **WHEN** a prompt asks how to verify a memassist change
- **THEN** memassist SHALL retrieve the workflow memory through lexical, metadata, or vector signals

### Requirement: memassist SHALL only inject active Markdown-backed memories from hybrid retrieval

Hybrid retrieval SHALL use SQLite and embeddings for speed, but injected memories SHALL correspond to authoritative active Markdown artifacts after reconciliation.

#### Scenario: Vector hit without artifact is ignored
- **GIVEN** a vector cache row matches the current prompt
- **AND** no active Markdown artifact exists for that memory id
- **WHEN** memassist builds the memory pack
- **THEN** memassist SHALL NOT inject that vector hit

#### Scenario: Candidate vector hit is not injected
- **GIVEN** a candidate memory has a matching embedding
- **WHEN** a prompt retrieves memory context
- **THEN** memassist SHALL NOT inject the candidate memory as active context
- **AND** the candidate MAY remain visible through memory management commands

### Requirement: memassist SHALL expose retrieval diagnostics for hybrid participation

When retrieval results are rendered in JSON or diagnostic output, memassist SHALL indicate which retrieval signals contributed when this information is available.

#### Scenario: Memory pack JSON includes retrieval signal metadata
- **WHEN** a user runs `memassist memory pack <query> --json`
- **THEN** memassist SHALL include enough diagnostic metadata to tell whether FTS, vector, metadata, path, or link signals contributed to the ranked pack
- **AND** the diagnostics SHALL NOT expose hidden policy decisions because retrieved memory is context only
