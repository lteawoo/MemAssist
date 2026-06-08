# agent-source-extraction Specification

## Purpose
TBD - created by archiving change agent-aware-source-adapters. Update Purpose after archive.
## Requirements
### Requirement: Hook commands SHALL carry the calling agent identifier

Each tool integration SHALL embed its agent identifier in the hook commands it installs, so the runtime knows which coding agent invoked a hook without inferring it from payload shape.

#### Scenario: Integration install embeds agent identifier
- **WHEN** a tool integration installs memassist hooks for a project
- **THEN** every installed hook command SHALL include that integration's agent identifier (for example `--agent claude`)
- **AND** the identifier SHALL be one of the registered tool integration names

#### Scenario: Runtime reads the agent identifier from the hook invocation
- **WHEN** a hook process runs with an agent identifier argument
- **THEN** memassist SHALL resolve the calling agent from that argument
- **AND** memassist SHALL NOT infer the calling agent from payload field shape when an identifier is present

### Requirement: Turn-end source extraction SHALL dispatch to the calling agent's adapter

memassist SHALL extract turn-end user source through an adapter selected by the calling agent identifier. Each agent adapter SHALL parse the user source from that agent's own payload and transcript format.

#### Scenario: Claude Code transcript is parsed by the Claude adapter
- **GIVEN** a Stop hook invocation identified as the Claude agent with a `transcript_path`
- **WHEN** memassist extracts the turn-end source
- **THEN** the Claude adapter SHALL read the latest user message from the Claude transcript format where the user role is at `message.role` and text is at `message.content`
- **AND** memassist SHALL record a source ledger entry containing that user text

#### Scenario: Codex source is parsed by the Codex adapter
- **GIVEN** a Stop hook invocation identified as the Codex agent
- **WHEN** memassist extracts the turn-end source
- **THEN** the Codex adapter SHALL read the latest user source from the Codex payload or Codex history format
- **AND** memassist SHALL preserve the existing Codex extraction behavior

#### Scenario: Direct payload prompt is honored regardless of agent
- **GIVEN** a hook payload that already contains the user prompt text directly
- **WHEN** memassist extracts the turn-end source
- **THEN** memassist SHALL use the direct payload prompt as the source content

### Requirement: Adapter dispatch SHALL fall back safely for unknown or missing identifiers

memassist SHALL remain functional when an agent identifier is absent or unrecognized, so previously installed hooks and unexpected callers do not silently lose source extraction.

#### Scenario: Missing identifier falls back to compatible extraction
- **WHEN** a hook runs without an agent identifier
- **THEN** memassist SHALL attempt source extraction using a compatible fallback path
- **AND** memassist SHALL NOT crash the hook

#### Scenario: Unknown identifier falls back rather than failing
- **WHEN** a hook runs with an agent identifier that has no registered adapter
- **THEN** memassist SHALL fall back to compatible extraction
- **AND** memassist SHALL NOT raise an error that blocks the agent turn

### Requirement: Adding a new agent SHALL require only a new adapter

The runtime source-extraction path SHALL be open for extension and closed for modification: introducing a new coding agent SHALL be achievable by registering a new adapter without editing shared extraction branching.

#### Scenario: New agent adapter is added without touching shared dispatch
- **WHEN** a new coding agent is supported
- **THEN** support SHALL be added by registering a new agent adapter that parses that agent's format
- **AND** the shared turn-end extraction dispatch SHALL NOT require per-agent branching changes to recognize it

