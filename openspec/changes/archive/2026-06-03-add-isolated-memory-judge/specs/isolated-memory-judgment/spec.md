## ADDED Requirements

### Requirement: memassist SHALL judge prompt-derived durable memories in isolation

memassist SHALL use an isolated memory judge for automatic durable memories derived from user prompts. The judge input MUST be limited to source events and allowed compact project hints, and MUST exclude assistant responses, injected memory context, system/developer prompts, current agent reasoning, and full conversation history.

#### Scenario: User directive is judged from source event only

- **WHEN** a user prompt says `앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐`
- **THEN** memassist SHALL create or enqueue a source event containing the user prompt
- **AND** the isolated judge input SHALL include that source event
- **AND** the isolated judge input SHALL NOT include assistant echo or retrieved memory context

#### Scenario: Assistant echo is not a memory source

- **WHEN** an assistant response says `앞으로 페이지/브라우저/서버 등 리프레시에 해당하는 동작은 실행 전에 먼저 승인 요청하겠습니다`
- **THEN** memassist SHALL NOT use that assistant response as the source for a durable memory derived from the user's preference

### Requirement: memassist SHALL keep automatic memory ingestion invisible to users

Users SHALL NOT need to know about memory commands or explicitly request memory activation for normal low-risk automatic memory ingestion.

#### Scenario: Low-risk preference is automatically stored

- **WHEN** the isolated judge determines that a user prompt contains a low-risk durable preference
- **THEN** memassist SHALL store the memory without requiring the user to run a memory command
- **AND** future RAG SHALL be able to retrieve that memory

#### Scenario: Memory system knowledge is not required

- **WHEN** a user states a durable preference in natural language
- **THEN** memassist SHALL evaluate it for memory ingestion without requiring the user to mention memassist, memory, RAG, or storage

### Requirement: memassist SHALL stage policy-like memory separately from policy compilation

When the isolated judge identifies a directive that affects approval, warning, blocking, sensitive paths, or protected paths, memassist SHALL store it as a candidate or non-policy active reminder unless an activation workflow explicitly compiles it into project policy.

#### Scenario: Approval-before-edit directive becomes memory candidate

- **WHEN** the isolated judge accepts `앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐`
- **THEN** memassist SHALL store a directive memory or candidate preserving the `refresh token` subject
- **AND** memassist SHALL NOT immediately add `src/auth/refresh-token-policy.ts` to `protected_paths` during `UserPromptSubmit`

#### Scenario: Judge output preserves source quote

- **WHEN** memassist stores a memory created by the isolated judge
- **THEN** the stored record or lifecycle metadata SHALL include the source quote used by the judge

### Requirement: memassist SHALL control judge token cost

memassist SHALL limit isolated judge calls by using immediate judgment for explicit memory-intent events and batch judgment for lower-priority events.

#### Scenario: Explicit directive is eligible for immediate judgment

- **WHEN** a user prompt explicitly asks for future behavior such as `다음부터 테스트 전에 알려줘`
- **THEN** memassist SHALL make the source event eligible for immediate isolated judgment

#### Scenario: Ordinary task request is not immediately judged

- **WHEN** a user prompt asks for a one-off task such as `refresh token TTL을 15분으로 바꿔줘`
- **THEN** memassist SHALL NOT require an immediate isolated judge call before continuing the task
- **AND** the prompt MAY be considered later by batch judgment

### Requirement: memassist SHALL keep session approval separate from durable memory judgment

Explicit approval prompts SHALL continue to create short-lived session approval grants and SHALL NOT create durable memory candidates through the isolated judge.

#### Scenario: Approval prompt creates grant only

- **WHEN** a user prompt says `승인`
- **THEN** memassist SHALL create a session-scoped approval grant when applicable
- **AND** memassist SHALL NOT create a durable memory candidate from that approval prompt
