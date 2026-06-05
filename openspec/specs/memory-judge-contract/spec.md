## Purpose
Define the memory extraction judge contract used by memassist to turn user source text into durable memory candidates without deciding downstream lifecycle, conflict, or retrieval associations.

## Requirements

### Requirement: Extraction Judge V2 Output
The system SHALL use a judge contract where the judge returns either a normalized `memory` object or `memory: null`.

The normalized `memory` object SHALL include:

- `content`
- `type`
- `source_quote`

The top-level object SHALL include:

- `source_integrity`
- `reason`

When `memory` is null, the top-level object SHALL include `reject_reason`.

The judge output SHALL NOT include `should_store`, `meaning_preserved`, `activation`, `candidate_paths`, or `caution_level`.

#### Scenario: Durable memory extracted
- **WHEN** the user source contains a durable future instruction
- **THEN** the judge returns a non-null `memory` object with normalized `content`, `type`, and `source_quote`

#### Scenario: No durable memory extracted
- **WHEN** the user source only contains a current-turn formatting request
- **THEN** the judge returns `memory: null` with a `reject_reason`

### Requirement: Source Integrity
The system SHALL represent extraction cleanliness with `source_integrity`.

`source_integrity` SHALL describe whether the extracted memory came cleanly from user source text without contamination from assistant responses, retrieved memory context, system prompts, developer prompts, agent reasoning, full conversation history, or one-shot current-turn instructions.

#### Scenario: Clean source
- **WHEN** the memory content is directly supported by durable user source text
- **THEN** `source_integrity` is `clean`

#### Scenario: Uncertain source
- **WHEN** the source may contain a durable memory but its persistence or boundaries are ambiguous
- **THEN** `source_integrity` is `uncertain`

#### Scenario: Contaminated source
- **WHEN** the extracted memory appears to include assistant text, retrieved memory text, system/developer instruction text, or one-shot formatting text
- **THEN** `source_integrity` is `contaminated`

### Requirement: Lifecycle Decision Separation
The extraction judge SHALL NOT decide whether a memory is active, candidate, archived, rejected, superseded, or reinforced.

The system SHALL decide final lifecycle state after extraction using explicit policy that can inspect source integrity, duplicate state, conflict state, and other deterministic signals.

#### Scenario: Clean extraction with no conflict
- **WHEN** the judge returns a non-null memory with clean source integrity and downstream policy finds no conflicts
- **THEN** the system may store the memory as active according to activation policy

#### Scenario: Uncertain extraction
- **WHEN** the judge returns a non-null memory with uncertain source integrity
- **THEN** the system stores it as candidate or rejects it according to activation policy, but does not auto-activate it

### Requirement: Conflict Hint Isolation
The judge payload SHALL NOT include existing memory conflicts or related existing memories.

Enhanced conflict resolution SHALL be handled by a later system capability outside the extraction judge contract.

#### Scenario: Existing duplicate memory
- **WHEN** the extracted memory is equivalent to an existing memory
- **THEN** the extraction judge does not receive or decide the relationship to the existing memory

### Requirement: No Language Keyword Heuristics
The system SHALL NOT use language-specific hardcoded keyword rules to classify durable memory meaning, memory type, one-shot instruction status, source integrity, contradiction, or supersession.

Language-dependent semantic interpretation SHALL be performed by the extraction judge or a dedicated relation judge. System policy SHALL consume structured outputs and repository state.

#### Scenario: Korean durable preference
- **WHEN** a Korean user source contains a durable preference without English memory keywords
- **THEN** the system relies on judge output rather than keyword matching to identify the memory

#### Scenario: Mixed-language prompt
- **WHEN** a prompt mixes Korean and English instructions
- **THEN** the system does not classify memory semantics using a fixed keyword list

### Requirement: Path Independence
The extraction judge SHALL NOT return file paths or future file predictions as part of the memory.

The system SHALL treat file/path relevance as retrieval-time evidence or dynamic association outside the extracted memory contract.

#### Scenario: Source mentions a file
- **WHEN** the user source mentions a file path
- **THEN** the judge may use the source to extract durable memory content, but it does not return the file path as part of the judge contract

#### Scenario: Future work may involve unknown files
- **WHEN** a memory may become relevant to files that are not yet known
- **THEN** the memory remains path-independent and later retrieval or association logic determines relevance
