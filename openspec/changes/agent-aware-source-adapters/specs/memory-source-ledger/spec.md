## MODIFIED Requirements

### Requirement: memassist SHALL record memory source evidence in a project-local source ledger

memassist SHALL maintain a lightweight project-local source ledger for prompt-derived and trace-derived memory evidence. The source ledger SHALL be append-only for normal operation and SHALL preserve enough information to audit why a memory candidate or stored memory exists, including which coding agent produced the source.

#### Scenario: Stop records prompt source evidence
- **WHEN** a Stop hook observes a user prompt source that may contain persistent memory
- **THEN** memassist SHALL append a source ledger record with a stable source id
- **AND** the record SHALL include source kind, session id when available, source reference when available, source hash, observed timestamp, and the full observed source text
- **AND** the record SHALL include the calling agent identifier when it is known from the hook invocation
- **AND** memassist SHALL NOT create or update project policy from that source record

#### Scenario: Long source remains available to the judge
- **GIVEN** a user prompt source is longer than the judge preview budget
- **WHEN** memassist builds an isolated memory judge payload for that source
- **THEN** the payload SHALL include a bounded preview for diagnostics
- **AND** the payload SHALL include ordered source chunks that reconstruct the full observed source text
- **AND** the judge SHALL NOT be limited to only the preview when deciding whether a durable memory exists

#### Scenario: Source ledger is project-local
- **WHEN** memassist is initialized for a project
- **THEN** source ledger records SHALL be stored under that project's `.memassist` home
- **AND** source ledger records SHALL NOT be stored in a parent or user-global `.memassist` home for project-scoped hooks

#### Scenario: Source origin is attributable across multiple installed agents
- **GIVEN** more than one tool integration is installed for the same project
- **WHEN** a source ledger record is appended from a hook that carried an agent identifier
- **THEN** the record SHALL preserve that agent identifier so the source origin remains attributable
