## MODIFIED Requirements

### Requirement: memassist SHALL NOT enforce universal built-in policy defaults

Fresh projects SHALL NOT receive active sensitive path or dangerous command enforcement unless the project has explicit policy entries configured in project `policy.yaml`. Memories SHALL NOT create active sensitive path, protected path, or dangerous command enforcement.

#### Scenario: Fresh project policy has no active sensitive path defaults

- **WHEN** a project is initialized
- **THEN** the generated policy SHALL NOT include active sensitive path entries such as `.env`, `.env.*`, `*.pem`, `*.key`, or `*secret*`
- **AND** a pre-tool check for a `.env` path SHALL return `allow` unless the project explicitly configured that path policy

#### Scenario: Fresh project policy has no active dangerous command defaults

- **WHEN** a project is initialized
- **THEN** the generated policy SHALL NOT include active dangerous command entries such as `rm -rf`, `git reset --hard`, or `git clean -fd`
- **AND** a pre-tool check for such a command SHALL return `allow` unless the project explicitly configured that command policy

#### Scenario: Missing policy keys do not revive removed defaults

- **WHEN** a policy file omits `sensitive_paths` or `dangerous_commands`
- **THEN** memassist SHALL treat the missing key as an empty list
- **AND** memassist SHALL NOT fall back to built-in sensitive path or dangerous command rules

### Requirement: memassist SHALL enforce explicit project policy entries deterministically

When a project has explicit active policy entries in `policy.yaml`, memassist SHALL match those entries deterministically during pre-tool checks. Prompt-derived directives and stored memories SHALL NOT become active project policy through `UserPromptSubmit`, memory activation, lifecycle processing, or any automatic memory workflow.

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

#### Scenario: UserPromptSubmit directive does not compile policy

- **GIVEN** a user prompt says `앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐`
- **WHEN** the prompt is processed by `UserPromptSubmit`
- **THEN** memassist SHALL NOT add a matching path to `protected_paths` or `sensitive_paths`
- **AND** memassist SHALL store or retrieve relevant memory only through the memory system

#### Scenario: Memory activation does not compile policy

- **GIVEN** a directive memory has path metadata for `src/auth/refresh-token-policy.ts`
- **WHEN** the memory is activated
- **THEN** memassist SHALL NOT add the path to `protected_paths`
- **AND** memassist SHALL NOT add the path to `sensitive_paths`

### Requirement: removed defaults SHALL NOT remain as presets or fallback guidance

Removed universal policy defaults and memory-derived enforcement behavior SHALL NOT remain available as generated presets, commented policy examples, suggested baseline policies, hidden fallback behavior, or memory activation side effects.

#### Scenario: Generated policy file contains no suggested universal preset

- **WHEN** memassist generates a default policy file
- **THEN** the file SHALL NOT include commented examples or suggested presets for `.env`, private keys, `rm -rf`, `git reset --hard`, or `git clean -fd`

#### Scenario: Documentation describes retrieval-first memory behavior

- **WHEN** a user reads project documentation for memory behavior
- **THEN** the documentation SHALL state that remembered user instructions are retrieved and injected as context
- **AND** the documentation SHALL NOT describe remembered user instructions as automatic warning or blocking policy

## REMOVED Requirements

### Requirement: Explicit session approval SHALL override the next matching protected or sensitive path decision

**Reason**: Session approval grants are part of the removed memory-derived policy gate model. memassist should retrieve memory context and let the active coding agent judge current user approval instead of consuming hidden tool-time approvals.

**Migration**: None. Existing users and old approval grant state do not need to be migrated.
