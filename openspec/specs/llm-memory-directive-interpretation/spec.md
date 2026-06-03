# llm-memory-directive-interpretation Specification

## Purpose
Define how memassist semantically interprets direct user memory directives into structured candidates while keeping policy compilation behind later validation and activation.
## Requirements
### Requirement: memassist SHALL interpret direct user memory directives semantically

memassist SHALL use an LLM-backed interpreter to convert user prompts into structured memory directive candidates through the coding tool selected during `memassist init` when that tool provides an interpreter-capable adapter.

#### Scenario: Multilingual typo-tolerant directive is recognized

- **WHEN** a user prompt says `리프레쉬 토큰 쪽은 담부터 고치기 전에 꼭 나한테 먼저 말해줘`
- **THEN** memassist SHALL recognize the prompt as a direct memory directive
- **AND** the directive candidate SHALL preserve the original prompt and include a normalized subject equivalent to refresh token changes
- **AND** the directive candidate SHALL assign an enforcement level of `warn`

#### Scenario: Soft phrasing is recognized without exact keywords

- **WHEN** a user asks to be told before future changes to a project area using wording that does not match deterministic keyword terms
- **THEN** memassist SHALL still produce a directive candidate when the semantic intent is explicit
- **AND** memassist SHALL record interpreter confidence and rationale

#### Scenario: Non-directive prompt is not stored as policy memory

- **WHEN** a user asks a one-off question about a project area without asking memassist to remember future behavior
- **THEN** memassist SHALL NOT create an active directive memory
- **AND** memassist SHALL NOT mutate project policy

### Requirement: memassist SHALL return structured directive interpretation results

The directive interpreter SHALL return constrained structured fields that downstream memory and policy code can validate before activation.

#### Scenario: Interpreter returns required fields

- **WHEN** the interpreter processes a prompt
- **THEN** the result SHALL include `is_directive`, `intent`, `subject`, `enforcement`, `scope_terms`, `candidate_paths`, `confidence`, `rationale`, and `normalized_prompt`
- **AND** `enforcement` SHALL be one of `none`, `remember`, `warn`, or `block`

#### Scenario: Invalid interpreter output falls back safely

- **WHEN** the LLM returns invalid, incomplete, or unparsable structured output
- **THEN** memassist SHALL treat the interpretation as uncertain
- **AND** memassist SHALL NOT create active enforcement policy from that output
- **AND** memassist SHALL record the failure for diagnostics

### Requirement: memassist SHALL use the initialized tool as the default LLM interpreter backend

memassist SHALL prefer the tool selected during `memassist init --tools ...` as the default LLM interpreter backend rather than requiring separate provider configuration.

#### Scenario: Initialized tool provides interpreter adapter

- **WHEN** a project was initialized with a tool that has an interpreter-capable adapter
- **THEN** memassist SHALL use that tool for LLM directive interpretation
- **AND** memassist SHALL record which adapter interpreted the directive

#### Scenario: Interpreter adapter avoids hook recursion

- **WHEN** memassist invokes the initialized tool from inside a hook to interpret a directive
- **THEN** the interpreter subprocess SHALL NOT trigger memassist hooks recursively
- **AND** the interpreter call SHALL use a bounded timeout

#### Scenario: Initialized tool lacks interpreter capability

- **WHEN** the initialized tool does not provide a usable interpreter adapter
- **THEN** memassist SHALL use the deterministic fallback interpreter
- **AND** diagnostics SHALL report that LLM directive interpretation is degraded for that tool

### Requirement: memassist SHALL gate LLM-derived directives before policy activation

memassist SHALL store LLM-derived directives as structured candidates or non-policy memories based on confidence, explicitness, and path resolution rather than compiling every model output into active project policy.

#### Scenario: High-confidence explicit directive is staged for activation

- **WHEN** the interpreter returns a high-confidence explicit directive with an enforcement level and a resolved project path
- **THEN** memassist SHALL store the directive as a candidate or non-policy memory preserving the interpreted subject and source
- **AND** memassist SHALL NOT compile the directive into `sensitive_paths` or `protected_paths` until an explicit activation workflow runs

#### Scenario: Ambiguous directive remains inactive

- **WHEN** the interpreter returns a low-confidence or ambiguous directive candidate
- **THEN** memassist SHALL store it only as a draft or candidate memory
- **AND** memassist SHALL NOT mutate project policy

#### Scenario: Directive without resolved path remains reminder-only

- **WHEN** the interpreter returns a directive with `warn` or `block` enforcement but no resolved project path
- **THEN** memassist SHALL remember the directive without adding `sensitive_paths` or `protected_paths`
- **AND** memassist SHALL inject the directive as context when semantically relevant

### Requirement: memassist SHALL support deterministic fallback when initialized-tool LLM interpretation is unavailable

memassist SHALL continue operating when the initialized tool cannot provide LLM directive interpretation, while making degraded behavior observable.

#### Scenario: Missing initialized-tool interpreter uses fallback interpreter

- **WHEN** the initialized tool cannot provide LLM directive interpretation
- **THEN** memassist SHALL use the deterministic fallback interpreter
- **AND** `doctor` or diagnostic output SHALL indicate that LLM directive interpretation is unavailable

#### Scenario: Fallback does not reintroduce universal policy defaults

- **WHEN** the fallback interpreter cannot classify a prompt as an explicit directive
- **THEN** memassist SHALL NOT create policy entries from universal safety assumptions
- **AND** fresh-project `.env` and destructive-command defaults SHALL remain inactive unless explicitly remembered or configured

### Requirement: memassist SHALL verify directive behavior through temp-project E2E tests

The change SHALL include tests that exercise initialization, directive memory creation, activation-gated policy compilation, retrieval, and the initialized tool's hook execution in a temporary project.

#### Scenario: E2E covers init through initialized tool hook behavior

- **WHEN** the E2E test initializes a temporary project and submits a typo-tolerant directive through the configured hook path
- **THEN** memassist SHALL store the expected memory
- **AND** a later relevant prompt SHALL receive the memory context
- **AND** a protected or sensitive tool operation SHALL produce the expected policy behavior only after the memory has been explicitly activated into project policy

#### Scenario: E2E reports unsupported tool lifecycle gaps

- **WHEN** a coding tool or command mode does not execute one of the required hook lifecycle events
- **THEN** the E2E report SHALL identify which lifecycle capability is missing
- **AND** the report SHALL NOT claim full compatibility for that mode
