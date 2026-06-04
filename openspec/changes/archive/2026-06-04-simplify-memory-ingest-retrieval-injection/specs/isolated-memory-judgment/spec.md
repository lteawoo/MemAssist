## MODIFIED Requirements

### Requirement: memassist SHALL judge prompt-derived persistent memories in isolation

memassist SHALL use an isolated memory judge for automatic persistent memories derived from user prompts. The judge input MUST be built at turn end from host-provided source evidence and allowed compact project hints. It MUST exclude assistant responses, injected memory context, system/developer prompts, current agent reasoning, and full conversation history unless a future requirement explicitly permits an additional source type.

#### Scenario: User directive is judged from turn-end source evidence only

- **WHEN** a user prompt says `앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐`
- **AND** the turn reaches the Stop lifecycle event with host source evidence for that prompt
- **THEN** memassist SHALL build isolated judge input from that source evidence
- **AND** the isolated judge input SHALL NOT include assistant echo or retrieved memory context

#### Scenario: Assistant echo is not a memory source

- **WHEN** an assistant response says `앞으로 페이지/브라우저/서버 등 리프레시에 해당하는 동작은 실행 전에 먼저 승인 요청하겠습니다`
- **THEN** memassist SHALL NOT use that assistant response as the source for a persistent memory derived from the user's preference

### Requirement: memassist SHALL control judge token cost

memassist SHALL avoid keyword-based inline judging during `UserPromptSubmit`, and SHALL run the isolated judge at turn end using a low-cost model when prompt-derived source evidence is available. Whether a prompt yields persistent memory SHALL be decided by the isolated judge and deterministic lifecycle policy, not by a keyword rule or submit-time storage.

#### Scenario: Directive without trigger keywords is still judged

- **WHEN** a user prompt expresses a persistent preference but matches no predefined keyword list
- **AND** turn-end processing can access source evidence for that prompt
- **THEN** memassist SHALL submit the source evidence to the isolated judge at turn end

#### Scenario: Ordinary task is not inline-judged

- **WHEN** a user prompt asks for a one-off task such as `refresh token TTL을 15분으로 바꿔줘`
- **THEN** memassist SHALL NOT run the isolated judge during `UserPromptSubmit`
- **AND** the isolated judge at turn end MAY determine that no persistent memory should be stored

#### Scenario: UserPromptSubmit only reads memory

- **WHEN** a user submits a prompt
- **THEN** memassist SHALL retrieve and inject relevant memory during `UserPromptSubmit`
- **AND** memassist SHALL NOT create source events, persistent memory, or memory candidates during `UserPromptSubmit`

## ADDED Requirements

### Requirement: memassist SHALL let deterministic lifecycle policy decide memory status
The isolated judge SHALL provide candidate content and evidence fields, while deterministic lifecycle policy SHALL decide whether the memory is stored as `candidate`, `active`, or `archived`. Judge-provided activation hints MAY be used as non-authoritative input but MUST NOT bypass duplicate, conflict, safety, source, scope, or confidence gates.

#### Scenario: Judge candidate is gated by lifecycle policy
- **WHEN** the isolated judge returns memory content with a source quote and confidence hints
- **THEN** memassist SHALL run deterministic duplicate, conflict, safety, source, scope, and confidence checks
- **AND** memassist SHALL choose the stored status using lifecycle policy
- **AND** memassist SHALL NOT activate the memory solely because the judge requested activation

### Requirement: memassist SHALL preserve source evidence for stored memories
Every stored prompt-derived memory SHALL include source evidence that allows the user or system to audit why the memory exists. At minimum, stored evidence SHALL include `source_quote` when available and `source_ref` when the host provides a stable source reference.

#### Scenario: Stored candidate has source evidence
- **WHEN** memassist stores a candidate or active memory from turn-end judgment
- **THEN** the stored record SHALL include the memory content
- **AND** the stored record SHALL include the source quote when available
- **AND** the stored record SHALL include the source reference when available
