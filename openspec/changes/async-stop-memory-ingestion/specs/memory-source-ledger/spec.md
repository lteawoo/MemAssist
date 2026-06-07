## ADDED Requirements

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
