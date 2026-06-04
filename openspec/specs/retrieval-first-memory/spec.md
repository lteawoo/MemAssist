# retrieval-first-memory Specification

## Purpose
Define how memassist preserves user source-language memory, retrieves it across language and typo variants, and injects it as context for the active coding agent without compiling it into autonomous policy.
## Requirements
### Requirement: memassist SHALL preserve user source language as primary memory content
When memassist stores a memory derived from a user prompt, the primary memory content SHALL preserve the user's source language and meaning. Normalized subjects, tags, paths, or retrieval aliases MAY be stored as metadata, but they MUST NOT replace the source-language memory text.

#### Scenario: Korean directive remains Korean
- **WHEN** a user prompt says `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해`
- **THEN** memassist SHALL store the primary memory content in Korean
- **AND** the stored primary memory SHALL preserve the refresh token subject and confirmation-before-change meaning
- **AND** memassist SHALL NOT replace it with an English policy sentence

#### Scenario: English directive remains English
- **WHEN** a user prompt says `Ask me before changing refresh token behavior`
- **THEN** memassist SHALL store the primary memory content in English
- **AND** retrieval metadata MAY include Korean or normalized aliases without changing the primary memory content

### Requirement: memassist SHALL retrieve relevant memories across language and typo variants
During memory retrieval, memassist SHALL use source content plus meaning-preserving metadata so that relevant memories can be found across multilingual wording, spelling variants, and common typo forms.

#### Scenario: Korean prompt retrieves refresh token memory
- **GIVEN** a stored memory says `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해`
- **WHEN** a later prompt says `리프레시 토큰 15분으로 변경해줘`
- **THEN** memassist SHALL retrieve that memory for RAG context

#### Scenario: Typo variant retrieves refresh token memory
- **GIVEN** a stored memory says `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해`
- **WHEN** a later prompt says `리프레쉬 토큰 TTL 바꿔줘`
- **THEN** memassist SHALL retrieve that memory for RAG context

#### Scenario: English prompt retrieves Korean refresh token memory
- **GIVEN** a stored memory says `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해`
- **WHEN** a later prompt says `change refresh token TTL to 15 minutes`
- **THEN** memassist SHALL retrieve that memory for RAG context

### Requirement: memassist SHALL inject retrieved memories as context, not policy
memassist SHALL inject relevant memories through the coding tool's prompt-context mechanism as contextual memory. The injected text MUST NOT present retrieved memories as memassist-enforced policy or tool-level permission decisions.

#### Scenario: UserPromptSubmit injects relevant memory
- **GIVEN** a stored memory says `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해`
- **WHEN** a later `UserPromptSubmit` hook receives `리프레시 토큰 15분으로 변경해줘`
- **THEN** memassist SHALL emit `additionalContext`
- **AND** that context SHALL include the stored source-language memory
- **AND** the context SHALL be labeled as relevant memory, not as `Policy reminders`

#### Scenario: Agent receives context for judgment
- **GIVEN** a retrieved memory says `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해`
- **WHEN** the coding agent receives the user's current task and memassist additional context
- **THEN** memassist SHALL NOT decide whether the task is approved
- **AND** the active coding agent SHALL be responsible for judging the current user instruction against the retrieved memory

### Requirement: memassist SHALL NOT enforce policy at the PreToolUse hook
memassist SHALL NOT warn, block, or otherwise gate tool execution at the `PreToolUse` hook, whether from stored memories or from manual `verification.yaml` entries. memassist SHALL NOT mutate `verification.yaml` from stored memories. Memory status changes SHALL affect retrieval eligibility only. Retrieved memory is delivered as additional context so the active coding agent can judge the current instruction autonomously.

#### Scenario: Memory activation does not create caution_level
- **GIVEN** a directive memory has path metadata for `src/auth/refresh-token-policy.ts`
- **WHEN** the memory is activated
- **THEN** memassist SHALL mark the memory active for retrieval
- **AND** memassist SHALL NOT create any pre-tool warning or blocking behavior for that path

#### Scenario: PreToolUse ignores memory-derived directives
- **GIVEN** an active directive memory says `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해`
- **WHEN** a `PreToolUse` hook receives a patch targeting `src/auth/refresh-token-policy.ts`
- **THEN** memassist SHALL NOT warn or block because of that memory
- **AND** memassist SHALL record the tool event as trace data

#### Scenario: PreToolUse does not enforce even manual policy entries
- **GIVEN** project `verification.yaml` manually includes `protected_paths: ["src/auth/refresh-token-policy.ts"]`
- **WHEN** a `PreToolUse` hook receives a patch targeting `src/auth/refresh-token-policy.ts`
- **THEN** memassist SHALL NOT return any warn or block decision
- **AND** memassist SHALL record the tool event as trace data only

### Requirement: memassist SHALL keep UserPromptSubmit retrieval-only for memory content
During `UserPromptSubmit`, memassist SHALL retrieve eligible existing memories from derived search/index state, update read-side retrieval telemetry for memories that are returned, and inject relevant context. It SHALL NOT create source events, candidate memories, persistent memories, or lifecycle decisions during `UserPromptSubmit`. Retrieved memories SHALL correspond to authoritative active Markdown artifacts after index reconciliation.

#### Scenario: Submit retrieves without creating memory
- **GIVEN** an active memory exists for the current project
- **WHEN** a `UserPromptSubmit` hook receives a related prompt
- **THEN** memassist SHALL retrieve the existing memory
- **AND** memassist MAY update `retrieval_count` and `last_used_at` for the retrieved memory in derived telemetry
- **AND** memassist SHALL NOT create a new memory record, source event, candidate, or lifecycle judgment for the submitted prompt

#### Scenario: Submit injects only eligible memory
- **GIVEN** one active memory and one candidate memory both match the current prompt
- **WHEN** a `UserPromptSubmit` hook builds additional context
- **THEN** memassist SHALL include the active memory when it passes confidence and scope gates
- **AND** memassist SHALL NOT inject the candidate memory as prompt context

#### Scenario: Submit ignores stale SQLite-only row
- **GIVEN** SQLite contains a search row that matches the current prompt
- **AND** no active Markdown artifact exists for that memory id
- **WHEN** memassist reconciles the index or handles prompt retrieval after reconciliation
- **THEN** memassist SHALL NOT inject that SQLite-only row as memory context

### Requirement: memassist SHALL inject source-grounded memory context
Injected memory context SHALL be derived from stored memory records and their source evidence rather than compact summaries. memassist SHALL keep source evidence available for audit without requiring a `MEMORY.md` summary file.

#### Scenario: Injected memory includes source-grounded record
- **GIVEN** an active memory has `content`, `source_quote`, and `source_ref`
- **WHEN** a related prompt retrieves that memory
- **THEN** memassist SHALL inject the memory content as relevant context
- **AND** memassist SHALL keep the source quote or source reference available in the memory pack or storage record for audit
- **AND** memassist SHALL NOT require a compact summary file to satisfy retrieval

### Requirement: memassist SHALL retrieve from source-grounded Markdown-backed records
Prompt-time retrieval MAY use SQLite for speed, but each injected memory SHALL represent an active Markdown-backed record whose content and source evidence can be audited from the authoritative artifact.

#### Scenario: Retrieved context can be audited
- **GIVEN** an active Markdown memory artifact includes content, source quote, and source reference
- **AND** the derived SQLite index contains a matching row
- **WHEN** prompt retrieval injects that memory
- **THEN** the injected context SHALL include the Markdown-backed memory content
- **AND** the source quote or source reference SHALL remain available from the authoritative artifact or memory pack for audit

#### Scenario: Rebuild restores retrieval from Markdown
- **GIVEN** an active Markdown memory artifact exists
- **AND** SQLite index state is missing or stale
- **WHEN** memassist rebuilds the memory index
- **THEN** later prompt retrieval SHALL find the active memory through the rebuilt index
