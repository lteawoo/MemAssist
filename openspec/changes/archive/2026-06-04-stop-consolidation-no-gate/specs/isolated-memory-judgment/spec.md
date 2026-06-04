## MODIFIED Requirements

### Requirement: memassist SHALL control judge token cost

memassist SHALL record every non-empty user prompt as a pending source event without keyword-based pre-filtering, and SHALL run the isolated judge over pending source events at turn end (the Stop lifecycle event) using a low-cost model, rather than blocking the prompt with inline judgment. Whether a prompt yields durable memory SHALL be decided by the isolated judge, not by a keyword rule.

#### Scenario: Directive without trigger keywords is still judged

- **WHEN** a user prompt expresses a durable preference but matches no predefined keyword list
- **THEN** memassist SHALL record it as a pending source event
- **AND** memassist SHALL submit it to the isolated judge at turn end

#### Scenario: Ordinary task is recorded but not inline-judged

- **WHEN** a user prompt asks for a one-off task such as `refresh token TTL을 15분으로 바꿔줘`
- **THEN** memassist SHALL NOT run the isolated judge during `UserPromptSubmit`
- **AND** the isolated judge at turn end MAY determine that no durable memory should be stored

#### Scenario: UserPromptSubmit only reads memory

- **WHEN** a user submits a prompt
- **THEN** memassist SHALL retrieve and inject relevant memory during `UserPromptSubmit`
- **AND** memassist SHALL NOT create durable memory during `UserPromptSubmit`
