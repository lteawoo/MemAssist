# embedding-profile-management Specification

## Purpose
TBD - created by archiving change add-local-embedding-profile-mvp. Update Purpose after archive.
## Requirements
### Requirement: memassist SHALL manage project-local embedding profiles

memassist SHALL store embedding profile configuration in the project `.memassist` home and SHALL treat profile configuration as the source of truth for local vector retrieval provider selection.

#### Scenario: Init creates embedding profile configuration
- **WHEN** a user runs `memassist init` in a project directory
- **THEN** memassist SHALL prepare `<project>/.memassist/embedding-profiles.yaml`
- **AND** the configuration SHALL include an active profile id
- **AND** the configuration SHALL be scoped to the initialized project

#### Scenario: Profile contains provider and model identity
- **WHEN** memassist reads an embedding profile
- **THEN** the profile SHALL define provider and model identity
- **AND** the profile MAY define quantization, dimension, normalization, query prefix, document prefix, device, runtime, and cache settings

### Requirement: memassist SHALL use a provider registry for local embeddings

memassist SHALL resolve embedding providers through a registry so that local provider implementations can be added, removed, or tested without changing the hybrid retrieval engine.

#### Scenario: Registered provider is loaded lazily
- **GIVEN** an active profile names a registered provider
- **WHEN** memassist builds embeddings or performs vector retrieval
- **THEN** memassist SHALL instantiate that provider lazily
- **AND** provider-specific dependencies SHALL NOT be imported during project initialization unless the provider is used

#### Scenario: Missing dependency is reported without breaking lexical retrieval
- **GIVEN** an active profile names a provider whose dependency is not installed
- **WHEN** memassist handles memory retrieval
- **THEN** memassist SHALL keep lexical, metadata, path, and link retrieval available
- **AND** vector diagnostics SHALL report a missing dependency for the active profile

### Requirement: memassist SHALL support real local embedding profiles for MVP evaluation

The product embedding profile system SHALL use real local embedding providers and models for candidate comparison. Hash or pseudo-vector implementations MUST NOT be product candidate profiles.

#### Scenario: Local model profile can be selected
- **GIVEN** a profile for a real local embedding provider such as `model2vec`
- **WHEN** the user activates that profile
- **THEN** memassist SHALL use that profile for vector retrieval and embedding cache builds
- **AND** the profile identity SHALL appear in retrieval diagnostics

#### Scenario: Hash pseudo-vector is not a product candidate
- **WHEN** memassist lists product embedding profiles or profile comparison candidates
- **THEN** hash pseudo-vector providers SHALL NOT be listed as semantic embedding candidates
- **AND** test-only fake providers MAY be used only in tests or fixtures

### Requirement: memassist SHALL allow explicit vector disablement

memassist SHALL allow users to explicitly disable vector retrieval through a profile or configuration value, but vector disablement SHALL be visible in diagnostics.

#### Scenario: None profile disables vector retrieval
- **GIVEN** the active profile is `none` or has provider `none`
- **WHEN** memassist builds a memory pack
- **THEN** memassist SHALL skip vector retrieval
- **AND** diagnostics SHALL report vector status as disabled
- **AND** the memory pack SHALL still use lexical, metadata, path, and link signals

