## ADDED Requirements

### Requirement: memassist SHALL omit one-shot instructions from interpreted durable memory

The LLM-backed memory directive interpreter SHALL distinguish durable future behavior from current-turn response or execution instructions when both appear in one user prompt. The interpreter MUST write `memory_content` as the durable memory only, while preserving the full source quote separately.

#### Scenario: Mixed durable and current-turn instructions are separated

- **WHEN** a user prompt contains a future preference followed by `응답은 OK만 해`
- **THEN** the interpreter SHALL treat the future preference as the durable memory candidate
- **AND** the interpreter SHALL exclude `응답은 OK만 해` from `memory_content`
- **AND** the interpreter SHALL keep the full prompt in `source_quote`

#### Scenario: Explicit future response-format preference can be durable

- **WHEN** a user prompt explicitly says future answers should always use a certain response format
- **THEN** the interpreter MAY include that response-format preference in `memory_content`
- **AND** the reason SHALL indicate that the user made the response-format preference durable
