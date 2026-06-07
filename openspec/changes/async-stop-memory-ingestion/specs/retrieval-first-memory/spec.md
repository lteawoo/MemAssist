## ADDED Requirements

### Requirement: memassist SHALL make asynchronously ingested memories retrievable after worker completion
Memories created by async ingestion SHALL use the same authoritative Markdown-backed storage and retrieval eligibility rules as memories created by synchronous ingestion.

#### Scenario: Async memory becomes prompt context after worker completion
- **GIVEN** a Stop hook stored source evidence for a durable user preference
- **AND** the async ingestion worker later stores that preference as an active memory
- **WHEN** a later UserPromptSubmit hook receives a related prompt
- **THEN** memassist SHALL be able to retrieve and inject that memory as relevant context
- **AND** memassist SHALL NOT present it as a tool-level policy decision

#### Scenario: Pending async memory is not injected prematurely
- **GIVEN** a Stop hook has stored source evidence
- **AND** the async ingestion worker has not yet produced an active memory
- **WHEN** a UserPromptSubmit hook receives a related prompt
- **THEN** memassist SHALL NOT inject a memory that has not been stored as an active Markdown-backed memory
- **AND** prompt submission SHALL still complete normally

### Requirement: memassist SHALL report async ingestion state in diagnostics
memassist diagnostics SHALL expose whether async ingestion is enabled and whether pending source records or recent worker failures exist.

#### Scenario: Doctor reports pending async ingestion
- **GIVEN** pending source records exist for the current project
- **WHEN** `memassist doctor` runs
- **THEN** diagnostics SHALL report pending async memory ingestion
- **AND** diagnostics SHALL NOT report prompt retrieval as failed solely because ingestion is pending
