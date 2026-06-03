## MODIFIED Requirements

### Requirement: memassist SHALL enforce explicit project policy entries deterministically

When a project has explicit active policy entries, memassist SHALL match those entries deterministically during pre-tool checks. Prompt-derived directives SHALL NOT become active project policy during `UserPromptSubmit`; they MUST be compiled into policy only after isolated judgment and an activation workflow have produced an explicit project policy entry.

#### Scenario: Explicit protected path blocks matching tool calls

- **GIVEN** project policy includes `protected_paths: ["src/auth/session.py"]`
- **WHEN** a pre-tool check receives a write or patch targeting `src/auth/session.py`
- **THEN** memassist SHALL return `block`

#### Scenario: Explicit sensitive path warns on matching tool calls

- **GIVEN** project policy includes `sensitive_paths: [".env"]`
- **WHEN** a pre-tool check receives a tool call targeting `.env`
- **THEN** memassist SHALL return `warn`

#### Scenario: Explicit dangerous command blocks matching shell calls

- **GIVEN** project policy includes `dangerous_commands: ["\\\\brm\\\\s+-r[f]?\\\\b"]`
- **WHEN** a pre-tool check receives a shell command matching that expression
- **THEN** memassist SHALL return `block`

#### Scenario: UserPromptSubmit directive does not immediately compile policy

- **GIVEN** a user prompt says `앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐`
- **WHEN** the prompt is processed by `UserPromptSubmit`
- **THEN** memassist SHALL NOT immediately add a matching path to `protected_paths` or `sensitive_paths`
- **AND** any future policy entry for that directive SHALL require isolated judgment and activation
