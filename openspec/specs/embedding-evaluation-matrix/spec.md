# embedding-evaluation-matrix Specification

## Purpose
TBD - created by archiving change add-local-embedding-profile-mvp. Update Purpose after archive.
## Requirements
### Requirement: memassist SHALL evaluate retrieval by embedding profile

memassist SHALL allow retrieval and RAG evaluation commands to run against a specified embedding profile without permanently changing the project active profile unless the user explicitly activates that profile.

#### Scenario: Retrieval eval targets a profile
- **GIVEN** a project has multiple embedding profiles
- **WHEN** the user runs retrieval evaluation with a profile id
- **THEN** memassist SHALL build or use vector cache rows for that profile
- **AND** memassist SHALL report retrieval metrics for that profile
- **AND** the project's active profile SHALL remain unchanged unless the command explicitly activates it

#### Scenario: RAG eval includes profile diagnostics
- **WHEN** a RAG evaluation builds a memory pack for a profile
- **THEN** the output SHALL include the profile id, provider, model, vector status, and retrieval channel contributions

### Requirement: memassist SHALL compare local embedding profiles with common metrics

memassist SHALL provide a comparison workflow that runs the same eval cases across multiple profiles and reports quality and operational metrics.

#### Scenario: Compare reports quality metrics
- **GIVEN** eval cases cover multilingual paraphrases, source quote recall, exact code terms, and forbidden terms
- **WHEN** the user compares two or more profiles
- **THEN** memassist SHALL report recall or pass-rate metrics for each profile
- **AND** memassist SHALL report forbidden or policy leak rates for each profile

#### Scenario: Compare reports operational metrics
- **WHEN** the user compares embedding profiles
- **THEN** memassist SHALL report query latency or elapsed time for each profile when available
- **AND** memassist SHALL report embedding cache size or row count for each profile when available

### Requirement: memassist SHALL preserve exact-code eval coverage during semantic model comparison

Every profile comparison intended for default selection SHALL include exact-code recall cases so semantic vector improvements do not regress path, symbol, command, tag, or error-string retrieval.

#### Scenario: Exact path case is required
- **GIVEN** a profile comparison eval set is used for default model selection
- **WHEN** memassist validates the eval set
- **THEN** the eval set SHALL include at least one path, command, symbol, tag, or error-string exact recall case

#### Scenario: Semantic profile cannot hide exact recall regression
- **GIVEN** a semantic profile retrieves paraphrases better than another profile
- **AND** the semantic profile misses an exact path or command case
- **WHEN** memassist reports the comparison
- **THEN** the exact recall regression SHALL be visible in the profile metrics

