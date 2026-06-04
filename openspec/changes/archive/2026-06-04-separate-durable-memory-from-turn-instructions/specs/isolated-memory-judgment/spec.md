## ADDED Requirements

### Requirement: memassist SHALL separate durable memory content from source evidence

memassist SHALL store only the durable preference, directive, fact, or workflow as retrievable memory content when an isolated judge accepts a prompt-derived memory. The original user prompt or source quote MUST remain available as evidence metadata, but transient current-turn instructions MUST NOT be stored as the primary memory content unless the user explicitly asks to remember them for future turns.

#### Scenario: Durable directive with one-shot response instruction

- **WHEN** a user prompt says `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.`
- **THEN** memassist SHALL store a durable memory equivalent to `앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해`
- **AND** the stored memory content SHALL NOT contain `응답은 OK만 해`
- **AND** lifecycle metadata SHALL preserve the original source quote

#### Scenario: Source evidence remains auditable

- **WHEN** memassist stores a prompt-derived memory using the isolated judge
- **THEN** the lifecycle event SHALL include the judge source quote
- **AND** the source quote MAY include transient text that is excluded from the stored memory content

#### Scenario: Retrieval excludes transient source text

- **WHEN** a later prompt retrieves memory relevant to refresh token changes
- **THEN** the injected memory context SHALL include the durable refresh-token confirmation directive
- **AND** the injected memory context SHALL NOT include current-turn response-format text from the original prompt
