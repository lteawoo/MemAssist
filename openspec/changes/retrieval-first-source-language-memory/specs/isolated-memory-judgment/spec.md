## MODIFIED Requirements

### Requirement: memassist SHALL stage policy-like memory separately from policy compilation

When the isolated judge identifies a directive that affects approval, warning, blocking, sensitive paths, or protected paths, memassist SHALL store it as source-language memory with retrieval metadata. memassist SHALL NOT compile judged memories into project policy during `UserPromptSubmit`, activation, lifecycle processing, or any automatic memory workflow.

#### Scenario: Approval-before-edit directive becomes retrievable memory

- **WHEN** the isolated judge accepts `앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐`
- **THEN** memassist SHALL store a directive memory preserving the user's source-language phrasing and the `refresh token` subject
- **AND** memassist SHALL NOT add `src/auth/refresh-token-policy.ts` to `protected_paths` during `UserPromptSubmit`
- **AND** memassist SHALL NOT add `src/auth/refresh-token-policy.ts` to `sensitive_paths` during activation

#### Scenario: Judge output preserves source quote

- **WHEN** memassist stores a memory created by the isolated judge
- **THEN** the stored record or lifecycle metadata SHALL include the source quote used by the judge

#### Scenario: Judge output cannot create policy status

- **WHEN** the isolated judge returns enforcement-like metadata such as `warn` or `block`
- **THEN** memassist SHALL use that metadata only for retrieval and display context
- **AND** memassist SHALL NOT store the memory with `warn_policy` or `block_policy` status
