# memory-source-ledger Specification

## Purpose
TBD - created by archiving change add-lightweight-hybrid-memory-core. Update Purpose after archive.
## Requirements
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

### Requirement: memassist SHALL keep Stop source capture fast and enqueue-only by default
Stop hooks SHALL record trace and source-ledger evidence without waiting for isolated memory judgment, relation judgment, or lifecycle cleanup when the default async ingestion mode is active. Stop source capture SHALL be sufficient for a later worker to process the source.

#### Scenario: Stop records source evidence without running the judge synchronously
- **WHEN** a Stop hook receives source evidence for a user prompt
- **AND** Stop ingestion mode is async
- **THEN** memassist SHALL append a project-local source ledger record
- **AND** memassist SHALL leave the source eligible for background ingestion
- **AND** memassist SHALL NOT wait for isolated memory judgment before returning from the hook

#### Scenario: Stop source evidence is durable after fast return
- **WHEN** a Stop hook returns before memory judgment has completed
- **THEN** the observed source text and source metadata SHALL remain stored under the project `.memassist` home
- **AND** a later worker run SHALL be able to build a judge payload from that stored source evidence

### Requirement: memassist SHALL respect trace-only Stop mode
Trace-only integration mode SHALL record Stop trace data without enqueueing or processing prompt-derived memory ingestion.

#### Scenario: Trace mode does not enqueue memory ingestion
- **WHEN** a project integration is installed in trace mode
- **AND** a Stop hook receives prompt source evidence
- **THEN** memassist SHALL record trace data
- **AND** memassist SHALL NOT enqueue asynchronous memory ingestion for that source
- **AND** memassist SHALL NOT launch a background memory worker

