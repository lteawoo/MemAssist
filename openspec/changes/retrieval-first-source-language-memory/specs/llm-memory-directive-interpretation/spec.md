## MODIFIED Requirements

### Requirement: memassist SHALL interpret direct user memory directives semantically

memassist SHALL use an LLM-backed interpreter to convert user prompts into structured memory directive candidates through the coding tool selected during `memassist init` when that tool provides an interpreter-capable adapter. The interpreted structure SHALL support memory storage and semantic retrieval, not autonomous policy compilation.

#### Scenario: Multilingual typo-tolerant directive is recognized

- **WHEN** a user prompt says `리프레쉬 토큰 쪽은 담부터 고치기 전에 꼭 나한테 먼저 말해줘`
- **THEN** memassist SHALL recognize the prompt as a direct memory directive
- **AND** the directive candidate SHALL preserve the original prompt and include a normalized subject equivalent to refresh token changes
- **AND** the directive candidate SHALL be retrievable for later refresh-token prompts

#### Scenario: Soft phrasing is recognized without exact keywords

- **WHEN** a user asks to be told before future changes to a project area using wording that does not match deterministic keyword terms
- **THEN** memassist SHALL still produce a directive candidate when the semantic intent is explicit
- **AND** memassist SHALL record interpreter confidence and rationale

#### Scenario: Non-directive prompt is not stored as policy memory

- **WHEN** a user asks a one-off question about a project area without asking memassist to remember future behavior
- **THEN** memassist SHALL NOT create an active directive memory
- **AND** memassist SHALL NOT mutate project policy

### Requirement: memassist SHALL return structured directive interpretation results

The directive interpreter SHALL return constrained structured fields that downstream memory code can validate before storage and retrieval.

#### Scenario: Interpreter returns required fields

- **WHEN** the interpreter processes a prompt
- **THEN** the result SHALL include `is_directive`, `intent`, `subject`, `scope_terms`, `candidate_paths`, `confidence`, `rationale`, and `normalized_prompt`
- **AND** any enforcement-like field SHALL be treated as retrieval/display metadata only

#### Scenario: Invalid interpreter output falls back safely

- **WHEN** the LLM returns invalid, incomplete, or unparsable structured output
- **THEN** memassist SHALL treat the interpretation as uncertain
- **AND** memassist SHALL NOT create active enforcement policy from that output
- **AND** memassist SHALL record the failure for diagnostics

### Requirement: memassist SHALL gate LLM-derived directives before policy activation

memassist SHALL store LLM-derived directives as structured memory candidates or active retrieval memories based on confidence, explicitness, and source quality. memassist SHALL NOT compile LLM-derived directives into project policy.

#### Scenario: High-confidence explicit directive is stored for retrieval

- **WHEN** the interpreter returns a high-confidence explicit directive with a normalized subject and project path metadata
- **THEN** memassist SHALL store the directive as source-language memory
- **AND** memassist SHALL NOT compile the directive into `sensitive_paths` or `protected_paths`

#### Scenario: Ambiguous directive remains inactive

- **WHEN** the interpreter returns a low-confidence or ambiguous directive candidate
- **THEN** memassist SHALL store it only as a draft or candidate memory
- **AND** memassist SHALL NOT mutate project policy

#### Scenario: Directive without resolved path remains retrievable

- **WHEN** the interpreter returns a directive with no resolved project path
- **THEN** memassist SHALL remember the directive when it is explicit enough
- **AND** memassist SHALL inject the directive as context when semantically relevant

### Requirement: memassist SHALL verify directive behavior through temp-project E2E tests

The change SHALL include tests that exercise initialization, directive memory creation, retrieval, and initialized tool hook behavior in a temporary project without requiring policy compilation.

#### Scenario: E2E covers init through initialized tool hook behavior

- **WHEN** the E2E test initializes a temporary project and submits a typo-tolerant directive through the configured hook path
- **THEN** memassist SHALL store the expected source-language memory
- **AND** a later relevant prompt SHALL receive the memory context
- **AND** a matching pre-tool operation SHALL NOT produce memory-derived policy behavior

#### Scenario: E2E reports unsupported tool lifecycle gaps

- **WHEN** a coding tool or command mode does not execute one of the required hook lifecycle events
- **THEN** the E2E report SHALL identify which lifecycle capability is missing
- **AND** the report SHALL NOT claim full compatibility for that mode
