# memory-source-ledger Specification

## Purpose
TBD - created by archiving change add-lightweight-hybrid-memory-core. Update Purpose after archive.
## Requirements
### Requirement: memassist SHALL record memory source evidence in a project-local source ledger

memassist SHALL maintain a lightweight project-local source ledger for prompt-derived and trace-derived memory evidence. The source ledger SHALL be append-only for normal operation and SHALL preserve enough information to audit why a memory candidate or stored memory exists.

#### Scenario: Stop records prompt source evidence
- **WHEN** a Stop hook observes a user prompt source that may contain persistent memory
- **THEN** memassist SHALL append a source ledger record with a stable source id
- **AND** the record SHALL include source kind, session id when available, source reference when available, source hash, observed timestamp, and the full observed source text
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

### Requirement: memassist SHALL link memory candidates and artifacts to source ledger records

Every automatically judged memory candidate and stored prompt-derived memory SHALL reference the source ledger record that caused it when one is available.

#### Scenario: Judge candidate references source id
- **GIVEN** a source ledger record exists for a turn-end user prompt
- **WHEN** the isolated memory judge returns a memory candidate for that prompt
- **THEN** the candidate lifecycle event SHALL include the source id
- **AND** any stored memory artifact SHALL include the source id or source reference in its metadata

#### Scenario: Existing source quote remains auditable
- **GIVEN** a stored memory was created from a source ledger record
- **WHEN** a user inspects the memory artifact
- **THEN** the artifact SHALL include the stored memory content
- **AND** the artifact SHALL include source evidence through source id, source reference, or source quote

### Requirement: memassist SHALL allow source-ledger-backed re-extraction without changing active memory automatically

Source ledger records SHALL support future re-extraction or audit workflows, but reprocessing source records SHALL NOT automatically replace active memories without lifecycle recording.

#### Scenario: Re-extraction creates candidate rather than silent replacement
- **GIVEN** a source ledger record previously produced an active memory
- **WHEN** memassist reprocesses that source record with a newer judge or extractor
- **THEN** memassist SHALL create a candidate or lifecycle event for any changed interpretation
- **AND** memassist SHALL NOT silently rewrite the active memory content without recording the lifecycle change
