## Purpose

Define how memassist enforces project policy only when the project explicitly configured or remembered that policy, without universal built-in warning or blocking defaults.
## Requirements
### Requirement: memassist SHALL NOT enforce universal built-in policy defaults

Fresh projects SHALL NOT receive active sensitive path or dangerous command enforcement unless the project has explicit policy entries derived from explicit project configuration or memory that has been judged and activated into project policy.

#### Scenario: Fresh project policy has no active sensitive path defaults

- **WHEN** a project is initialized
- **THEN** the generated policy SHALL NOT include active sensitive path entries such as `.env`, `.env.*`, `*.pem`, `*.key`, or `*secret*`
- **AND** a pre-tool check for a `.env` path SHALL return `allow` unless the project explicitly configured or remembered that path policy

#### Scenario: Fresh project policy has no active dangerous command defaults

- **WHEN** a project is initialized
- **THEN** the generated policy SHALL NOT include active dangerous command entries such as `rm -rf`, `git reset --hard`, or `git clean -fd`
- **AND** a pre-tool check for such a command SHALL return `allow` unless the project explicitly configured or remembered that command policy

#### Scenario: Missing policy keys do not revive removed defaults

- **WHEN** a policy file omits `sensitive_paths` or `dangerous_commands`
- **THEN** memassist SHALL treat the missing key as an empty list
- **AND** memassist SHALL NOT fall back to built-in sensitive path or dangerous command rules

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

### Requirement: removed defaults SHALL NOT remain as presets or fallback guidance

Removed universal policy defaults SHALL NOT remain available as generated presets, commented policy examples, suggested baseline policies, or hidden fallback behavior.

#### Scenario: Generated policy file contains no suggested universal preset

- **WHEN** memassist generates a default policy file
- **THEN** the file SHALL NOT include commented examples or suggested presets for `.env`, private keys, `rm -rf`, `git reset --hard`, or `git clean -fd`

#### Scenario: Documentation describes memory-derived enforcement

- **WHEN** a user reads project documentation for policy behavior
- **THEN** the documentation SHALL state that warning and blocking behavior comes from explicit project policy or remembered user/project directives
- **AND** the documentation SHALL NOT describe removed universal defaults as active behavior

### Requirement: Explicit session approval SHALL override the next matching protected or sensitive path decision

When a user gives an explicit approval in the current session, memassist SHALL allow the next matching protected or sensitive path tool call for that same session and project, then consume the approval.

#### Scenario: Approval allows the next protected path edit

- **GIVEN** project policy includes `protected_paths: ["src/auth/session.py"]`
- **AND** the current session receives an explicit approval prompt such as `승인`
- **WHEN** a pre-tool check receives a patch targeting `src/auth/session.py` in the same session
- **THEN** memassist SHALL return `allow`
- **AND** memassist SHALL record that the approval was consumed

#### Scenario: Approval is single-use

- **GIVEN** project policy includes `protected_paths: ["src/auth/session.py"]`
- **AND** the current session receives one explicit approval prompt
- **WHEN** two consecutive pre-tool checks target `src/auth/session.py`
- **THEN** the first check SHALL return `allow`
- **AND** the second check SHALL return `block`

#### Scenario: Approval does not cross sessions

- **GIVEN** project policy includes `sensitive_paths: [".env"]`
- **AND** session `A` receives an explicit approval prompt
- **WHEN** session `B` receives a pre-tool check targeting `.env`
- **THEN** memassist SHALL return `warn`

#### Scenario: Approval matches glob protected path policies

- **GIVEN** project policy includes `protected_paths: ["src/auth/*.py"]`
- **AND** the current session receives an explicit approval prompt such as `승인`
- **WHEN** a pre-tool check receives a patch targeting `src/auth/session.py` in the same session
- **THEN** memassist SHALL return `allow`
- **AND** memassist SHALL record that the approval was consumed
