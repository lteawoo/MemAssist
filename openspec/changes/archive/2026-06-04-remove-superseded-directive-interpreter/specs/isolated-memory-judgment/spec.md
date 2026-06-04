## ADDED Requirements

### Requirement: memassist SHALL report isolated judge backend readiness in diagnostics

memassist diagnostics (`doctor`) SHALL report whether an isolated judge backend is available for the project and which initialized tool backs it, instead of reporting a separate directive interpreter. The report MUST NOT hardcode a single tool: a project whose only judge-capable tool is Claude SHALL be reported as judge-ready.

#### Scenario: Claude-only project reports judge ready

- **WHEN** a project has initialized the `claude` tool and `doctor` runs
- **THEN** the diagnostic SHALL report that an isolated judge backend is available
- **AND** the diagnostic SHALL identify `claude` as the backing tool
- **AND** the diagnostic SHALL NOT report that a directive interpreter is missing

#### Scenario: Codex project reports judge ready

- **WHEN** a project has initialized the `codex` tool and `doctor` runs
- **THEN** the diagnostic SHALL report that an isolated judge backend is available
- **AND** the diagnostic SHALL identify `codex` as the backing tool

#### Scenario: No judge-capable tool reports degraded judgment

- **WHEN** a project has not initialized any tool that provides a judge adapter and `doctor` runs
- **THEN** the diagnostic SHALL report that isolated judgment is unavailable
- **AND** the diagnostic SHALL NOT claim a working LLM directive interpreter
