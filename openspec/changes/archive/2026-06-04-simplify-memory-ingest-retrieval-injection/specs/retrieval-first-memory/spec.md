## ADDED Requirements

### Requirement: memassist SHALL keep UserPromptSubmit retrieval-only for memory content
During `UserPromptSubmit`, memassist SHALL retrieve eligible existing memories, update read-side retrieval telemetry for memories that are returned, and inject relevant context. It SHALL NOT create source events, candidate memories, persistent memories, or lifecycle decisions during `UserPromptSubmit`.

#### Scenario: Submit retrieves without creating memory
- **GIVEN** an active memory exists for the current project
- **WHEN** a `UserPromptSubmit` hook receives a related prompt
- **THEN** memassist SHALL retrieve the existing memory
- **AND** memassist MAY update `retrieval_count` and `last_used_at` for the retrieved memory
- **AND** memassist SHALL NOT create a new memory record, source event, candidate, or lifecycle judgment for the submitted prompt

#### Scenario: Submit injects only eligible memory
- **GIVEN** one active memory and one candidate memory both match the current prompt
- **WHEN** a `UserPromptSubmit` hook builds additional context
- **THEN** memassist SHALL include the active memory when it passes confidence and scope gates
- **AND** memassist SHALL NOT inject the candidate memory as prompt context

### Requirement: memassist SHALL inject source-grounded memory context
Injected memory context SHALL be derived from stored memory records and their source evidence rather than compact summaries. memassist SHALL keep source evidence available for audit without requiring a `MEMORY.md` summary file.

#### Scenario: Injected memory includes source-grounded record
- **GIVEN** an active memory has `content`, `source_quote`, and `source_ref`
- **WHEN** a related prompt retrieves that memory
- **THEN** memassist SHALL inject the memory content as relevant context
- **AND** memassist SHALL keep the source quote or source reference available in the memory pack or storage record for audit
- **AND** memassist SHALL NOT require a compact summary file to satisfy retrieval
