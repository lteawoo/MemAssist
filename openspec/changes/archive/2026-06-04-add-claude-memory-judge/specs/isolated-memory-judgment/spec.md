## ADDED Requirements

### Requirement: memassist SHALL select the isolated judge backend from initialized tools

memassist SHALL choose the isolated memory judge backend from the tools initialized for the project, rather than from a single hardcoded tool. When more than one initialized tool provides a judge adapter, memassist SHALL select deterministically by a fixed preference order. When no initialized tool provides a judge adapter, memassist SHALL return an unavailable judge and SHALL NOT write durable prompt-derived memory.

#### Scenario: Claude-only project selects the Claude judge

- **WHEN** a project has initialized the `claude` tool and not the `codex` tool
- **THEN** memassist SHALL select the Claude judge backend for prompt-derived durable memory
- **AND** memassist SHALL NOT report the judge as unavailable solely because `codex` is absent

#### Scenario: Codex remains the backend when initialized

- **WHEN** a project has initialized the `codex` tool
- **THEN** memassist SHALL select the Codex judge backend
- **AND** Codex-backed judgment behavior SHALL be unchanged

#### Scenario: No initialized judge tool

- **WHEN** a project has not initialized any tool that provides a judge adapter
- **THEN** memassist SHALL return an unavailable judge
- **AND** memassist SHALL NOT write durable prompt-derived memory

### Requirement: memassist SHALL support a Claude Code isolated judge backend

memassist SHALL provide a Claude Code judge adapter that evaluates a source event in a separate `claude` process and returns a structured judgment. The adapter MUST locate the judge JSON within the Claude CLI result envelope, MUST set recursion-guard environment variables on the subprocess, and MUST record diagnostics without writing durable memory when the process fails or returns invalid output.

#### Scenario: Claude judge JSON is parsed from the result envelope

- **WHEN** the Claude judge process returns an envelope whose `result` field contains the judge JSON object
- **THEN** memassist SHALL parse the judgment from the `result` field
- **AND** memassist SHALL apply the same storage rules as any other judge backend, including durable/transient separation and meaning preservation

#### Scenario: Nested Claude hook does not re-run the judge

- **WHEN** memassist launches the Claude judge subprocess with the recursion-guard environment set
- **THEN** any memassist hook invoked by that child Claude session SHALL short-circuit without recording trace, retrieving memory, or running the judge again

#### Scenario: Claude judge failure records diagnostics only

- **WHEN** the Claude judge process times out, errors, or returns output without a valid judgment object
- **THEN** memassist SHALL record judge diagnostics in the trace
- **AND** memassist SHALL NOT write a durable memory for that source event
