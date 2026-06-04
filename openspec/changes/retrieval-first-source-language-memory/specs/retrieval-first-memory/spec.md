## ADDED Requirements

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

### Requirement: memassist SHALL NOT compile memories into autonomous policy
memassist SHALL NOT mutate `policy.yaml`, `sensitive_paths`, `protected_paths`, dangerous command rules, or pre-tool approval gates from stored memories. Memory status changes SHALL affect retrieval eligibility only.

#### Scenario: Memory activation does not mutate policy
- **GIVEN** a directive memory has path metadata for `src/auth/refresh-token-policy.ts`
- **WHEN** the memory is activated
- **THEN** memassist SHALL mark the memory active for retrieval
- **AND** memassist SHALL NOT add the path to `sensitive_paths`
- **AND** memassist SHALL NOT add the path to `protected_paths`

#### Scenario: PreToolUse ignores memory-derived directives
- **GIVEN** an active directive memory says `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해`
- **AND** project `policy.yaml` has no matching manual path policy
- **WHEN** a `PreToolUse` hook receives a patch targeting `src/auth/refresh-token-policy.ts`
- **THEN** memassist SHALL NOT warn or block because of that memory
- **AND** memassist SHALL record the tool event as trace data

#### Scenario: Explicit manual policy remains deterministic
- **GIVEN** project `policy.yaml` manually includes `protected_paths: ["src/auth/refresh-token-policy.ts"]`
- **WHEN** a `PreToolUse` hook receives a patch targeting `src/auth/refresh-token-policy.ts`
- **THEN** memassist SHALL return the manual policy decision
- **AND** that decision SHALL NOT depend on retrieved memories
