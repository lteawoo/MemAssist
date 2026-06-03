## ADDED Requirements

### Requirement: memassist SHALL NOT enforce universal built-in policy defaults

Fresh projects SHALL NOT receive active sensitive path or dangerous command enforcement unless the project has explicit policy entries derived from user/project memory or explicit project configuration.

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

When a project has explicit active policy entries, memassist SHALL match those entries deterministically during pre-tool checks.

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

### Requirement: removed defaults SHALL NOT remain as presets or fallback guidance

Removed universal policy defaults SHALL NOT remain available as generated presets, commented policy examples, suggested baseline policies, or hidden fallback behavior.

#### Scenario: Generated policy file contains no suggested universal preset

- **WHEN** memassist generates a default policy file
- **THEN** the file SHALL NOT include commented examples or suggested presets for `.env`, private keys, `rm -rf`, `git reset --hard`, or `git clean -fd`

#### Scenario: Documentation describes memory-derived enforcement

- **WHEN** a user reads project documentation for policy behavior
- **THEN** the documentation SHALL state that warning and blocking behavior comes from explicit project policy or remembered user/project directives
- **AND** the documentation SHALL NOT describe removed universal defaults as active behavior

