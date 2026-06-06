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

#### Scenario: Source quote can recall without being injected
- **GIVEN** an active memory has normalized `content` and original-language `source_quote`
- **AND** the current prompt matches only the `source_quote`
- **WHEN** memassist retrieves memory context
- **THEN** memassist SHALL be allowed to use the `source_quote` as retrieval evidence
- **AND** memassist SHALL inject the normalized memory `content`
- **AND** memassist SHALL NOT inject the `source_quote` as default prompt context

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

#### Scenario: Long memory content is retrieved through chunks
- **GIVEN** an active Markdown memory artifact has content longer than the prompt context budget
- **AND** a relevant phrase appears only in a later chunk
- **WHEN** a prompt matches that later phrase
- **THEN** memassist SHALL retrieve the parent memory through its chunk index
- **AND** memassist SHALL inject a budgeted excerpt or normalized content without dropping the parent memory

### Requirement: memassist SHALL use hybrid retrieval as the default memory retrieval engine

memassist SHALL expose one default memory retrieval behavior that combines exact lexical retrieval with semantic vector retrieval when vector search is configured and indexed. If vector search is unavailable, memassist SHALL degrade to lexical retrieval without failing prompt submission.

#### Scenario: Hybrid retrieval combines lexical and vector candidates
- **GIVEN** active Markdown-backed memories are indexed for FTS
- **AND** vector embeddings are configured and available for some active memories
- **WHEN** a UserPromptSubmit query is processed
- **THEN** memassist SHALL collect lexical candidates
- **AND** memassist SHALL collect vector candidates when vector search is available
- **AND** memassist SHALL fuse candidates into one ranked memory pack

#### Scenario: FTS-only fallback works
- **GIVEN** active Markdown-backed memories are indexed for FTS
- **AND** no embedding provider or valid embedding cache is available
- **WHEN** a UserPromptSubmit query is processed
- **THEN** memassist SHALL retrieve and inject relevant memories using lexical, metadata, path, and link signals
- **AND** memassist SHALL NOT fail solely because vector search is unavailable

### Requirement: memassist SHALL preserve exact-code recall in hybrid retrieval

Hybrid retrieval SHALL preserve exact matches for paths, commands, symbols, tags, and error text even when semantic vector search is available.

#### Scenario: Exact path match remains retrievable
- **GIVEN** an active memory references `src/auth/session.py`
- **WHEN** a prompt mentions `src/auth/session.py`
- **THEN** memassist SHALL retrieve the path-matching memory even if vector results rank other memories semantically nearby

#### Scenario: Verification command remains retrievable
- **GIVEN** an active workflow memory says `Run pytest tests/test_memassist.py`
- **WHEN** a prompt asks how to verify a memassist change
- **THEN** memassist SHALL retrieve the workflow memory through lexical, metadata, or vector signals

### Requirement: memassist SHALL only inject active Markdown-backed memories from hybrid retrieval

Hybrid retrieval SHALL use SQLite and embeddings for speed, but injected memories SHALL correspond to authoritative active Markdown artifacts after reconciliation.

#### Scenario: Vector hit without artifact is ignored
- **GIVEN** a vector cache row matches the current prompt
- **AND** no active Markdown artifact exists for that memory id
- **WHEN** memassist builds the memory pack
- **THEN** memassist SHALL NOT inject that vector hit

#### Scenario: Candidate vector hit is not injected
- **GIVEN** a candidate memory has a matching embedding
- **WHEN** a prompt retrieves memory context
- **THEN** memassist SHALL NOT inject the candidate memory as active context
- **AND** the candidate MAY remain visible through memory management commands

### Requirement: memassist SHALL expose retrieval diagnostics for hybrid participation

When retrieval results are rendered in JSON or diagnostic output, memassist SHALL indicate which retrieval signals contributed when this information is available.

#### Scenario: Memory pack JSON includes retrieval signal metadata
- **WHEN** a user runs `memassist memory pack <query> --json`
- **THEN** memassist SHALL include enough diagnostic metadata to tell whether FTS, vector, metadata, path, or link signals contributed to the ranked pack
- **AND** the diagnostics SHALL NOT expose hidden policy decisions because retrieved memory is context only

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
