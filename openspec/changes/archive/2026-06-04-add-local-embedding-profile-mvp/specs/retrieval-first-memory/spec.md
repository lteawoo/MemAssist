## ADDED Requirements

### Requirement: memassist SHALL run retrieval through a mandatory hybrid engine with explicit vector status

Prompt-time memory retrieval SHALL use one hybrid retrieval engine that collects lexical, vector, metadata, path, verifier, and link signals according to availability. The engine SHALL attempt vector retrieval for the active embedding profile unless vector retrieval is explicitly disabled, and it SHALL expose the vector status in diagnostics.

#### Scenario: Active vector profile participates in hybrid retrieval
- **GIVEN** an active embedding profile has a registered local provider
- **AND** current active Markdown-backed memories have valid vector cache rows for that profile
- **WHEN** a UserPromptSubmit query is processed
- **THEN** memassist SHALL collect lexical candidates
- **AND** memassist SHALL collect vector candidates for the active profile
- **AND** memassist SHALL fuse the candidates into one ranked memory pack
- **AND** diagnostics SHALL report vector status as ok

#### Scenario: Missing vector dependency is explicit
- **GIVEN** an active embedding profile names a provider whose dependency is unavailable
- **WHEN** a UserPromptSubmit query is processed
- **THEN** memassist SHALL still collect lexical, metadata, path, verifier, and link candidates
- **AND** memassist SHALL NOT fail prompt submission solely because the vector provider is unavailable
- **AND** diagnostics SHALL report vector status as missing_dependency

#### Scenario: Explicitly disabled vector retrieval is visible
- **GIVEN** vector retrieval is explicitly disabled by the active profile
- **WHEN** memassist builds a memory pack
- **THEN** memassist SHALL skip vector candidate collection
- **AND** diagnostics SHALL report vector status as disabled

### Requirement: memassist SHALL keep retrieved memories contextual during profile-based retrieval

Embedding profiles, vector rankings, and retrieval diagnostics SHALL affect only memory retrieval and ranking. They SHALL NOT create hidden approval gates, tool blocks, warnings, or permission decisions.

#### Scenario: Profile diagnostics do not become policy
- **GIVEN** a retrieved memory has high vector similarity under the active profile
- **WHEN** memassist injects memory context for a coding agent
- **THEN** memassist SHALL inject the memory as relevant context
- **AND** memassist SHALL NOT present the vector score or profile result as a memassist-enforced approval or denial

#### Scenario: PreToolUse remains trace-only
- **GIVEN** an active embedding profile retrieves a directive memory related to a file path
- **WHEN** a `PreToolUse` hook observes a tool call touching that path
- **THEN** memassist SHALL NOT warn or block because of embedding retrieval
- **AND** memassist SHALL record the event as trace data only
