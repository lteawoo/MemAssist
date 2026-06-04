## ADDED Requirements

### Requirement: memassist SHALL judge memories from source-ledger evidence

When a source ledger record exists for a turn-end prompt, memassist SHALL build isolated memory judge input from that source-ledger-backed evidence and compact project hints. The judge input SHALL continue to exclude assistant responses, injected memory context, and hidden agent reasoning.

#### Scenario: Judge payload references source ledger record
- **GIVEN** Stop processing appended a source ledger record for a user prompt
- **WHEN** memassist builds the isolated judge payload
- **THEN** the payload SHALL include the source id or source reference
- **AND** the payload SHALL include the source evidence text or summary
- **AND** the payload SHALL NOT include retrieved memory context as evidence for the new memory

### Requirement: memassist SHALL store judge results as source-backed candidates or memories

Judge output SHALL be recorded with source evidence before it affects managed memory artifacts. A stored memory SHALL retain the source quote or source reference through the Markdown artifact.

#### Scenario: Stored judge memory includes source id and quote
- **GIVEN** the isolated judge accepts a persistent preference
- **WHEN** memassist stores the memory
- **THEN** the memory artifact SHALL include the normalized memory content
- **AND** the artifact SHALL include source evidence through source id, source reference, or source quote
- **AND** the stored memory SHALL remain retrieval context rather than policy enforcement

#### Scenario: Rejected judge output does not create active memory
- **GIVEN** the isolated judge returns `should_store=false` or high contamination risk
- **WHEN** Stop processing completes
- **THEN** memassist SHALL NOT create an active memory artifact
- **AND** memassist MAY record a lifecycle or event entry for audit
