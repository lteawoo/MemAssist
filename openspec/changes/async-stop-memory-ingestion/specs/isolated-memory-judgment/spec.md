## ADDED Requirements

### Requirement: memassist SHALL process prompt-derived memory judgment asynchronously by default
Prompt-derived isolated memory judgment SHALL run outside the Stop hook critical path by default. A background or one-shot worker SHALL process pending source records, run isolated memory judgment, run relation judgment when required, and apply lifecycle storage rules.

#### Scenario: Worker processes pending source records
- **GIVEN** a Stop hook has stored a pending source record
- **WHEN** the async ingestion worker runs for the project
- **THEN** memassist SHALL run isolated memory judgment for the pending source
- **AND** memassist SHALL store, skip, retry, or give up according to existing memory judge and lifecycle rules
- **AND** memassist SHALL record diagnostics for failures without blocking a host hook

#### Scenario: Slow judge does not cause Stop hook timeout
- **WHEN** the isolated judge subprocess takes longer than the host hook timeout budget
- **AND** Stop ingestion mode is async
- **THEN** the Stop hook SHALL still return without waiting for that subprocess
- **AND** any judge timeout SHALL be recorded by the background worker when it occurs

### Requirement: memassist SHALL bound async ingestion worker execution
The async ingestion worker SHALL avoid unbounded work in one run. It SHALL process at most a configured batch size of pending source records and SHALL avoid overlapping workers for the same project.

#### Scenario: Worker batch size is limited
- **GIVEN** more pending source records exist than the configured async batch size
- **WHEN** one worker run processes the project
- **THEN** memassist SHALL process no more than the configured batch size
- **AND** remaining source records SHALL stay eligible for later worker runs

#### Scenario: Overlapping worker is skipped
- **GIVEN** an async ingestion worker is already active for a project
- **WHEN** another Stop hook requests background ingestion
- **THEN** memassist SHALL avoid starting overlapping judge work for that project
- **AND** pending source records SHALL remain eligible for the active or a later worker

### Requirement: memassist SHALL provide explicit sync and off ingestion modes
memassist SHALL support explicit Stop ingestion modes for operational control. Async SHALL be the default. Sync SHALL process memory ingestion before Stop returns. Off SHALL record source evidence without running or spawning memory ingestion.

#### Scenario: Sync mode preserves turn-end memory processing
- **WHEN** Stop ingestion mode is sync
- **AND** a Stop hook receives source evidence
- **THEN** memassist SHALL process pending memory ingestion before returning from the hook

#### Scenario: Off mode leaves source unprocessed
- **WHEN** Stop ingestion mode is off
- **AND** a Stop hook receives source evidence
- **THEN** memassist SHALL record source evidence
- **AND** memassist SHALL NOT run or spawn isolated memory judgment
