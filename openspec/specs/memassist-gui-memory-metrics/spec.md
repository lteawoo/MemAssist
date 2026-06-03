# memassist-gui-memory-metrics Specification

## Purpose
Define behavior-aligned memory metrics for the read-only memassist GUI, replacing ambiguous score labels with priority, relevance, evidence, and localized help.
## Requirements
### Requirement: Behavior-aligned priority metric
The GUI SHALL expose a `Priority` metric for each memory in the default memory list. `Priority` MUST be a deterministic 0-100 computed value derived from existing persisted memory signals including status, strength, utility, confidence, importance, recurrence, and retrieval count.

#### Scenario: Default memory list shows priority
- **WHEN** the user opens the memory list without a search query
- **THEN** each memory row shows a `Priority` metric instead of the old `Score` label
- **AND** the value is derived from the memory's stored behavior signals

#### Scenario: Priority does not mutate memory usage
- **WHEN** the GUI computes or displays `Priority`
- **THEN** the GUI does not update `last_used_at`, `retrieval_count`, `utility`, `strength`, or any other stored memory field

### Requirement: Search-time relevance metric
The GUI SHALL expose a `Relevance` metric when the memory list is filtered by a non-empty search query. `Relevance` MUST be query-dependent and combine query match signals with the memory's priority baseline.

#### Scenario: Search results show relevance
- **WHEN** the user searches memories with a non-empty query
- **THEN** the primary metric column is labeled `Relevance`
- **AND** rows are ordered by the computed search relevance

#### Scenario: Relevance is not shown without a query
- **WHEN** the memory list has no active search query
- **THEN** the GUI does not present a `Relevance` value as if it were a static memory property

### Requirement: Metric evidence is visible
The GUI SHALL show compact supporting evidence for the primary memory metric, including confidence, strength, utility, uses, and recurrence.

#### Scenario: Memory row includes metric evidence
- **WHEN** a memory row is rendered
- **THEN** the row includes the primary metric value
- **AND** the row includes supporting evidence for confidence, strength, utility, uses, and recurrence

### Requirement: Metric help is available
The GUI SHALL provide a `?` help affordance next to metric labels. The help MUST explain the displayed metric in the active GUI language and MUST be accessible by hover and keyboard focus.

#### Scenario: Priority tooltip explains the score
- **WHEN** the user hovers or focuses the `?` help next to `Priority`
- **THEN** the GUI explains that Priority is a behavior-aligned ranking score derived from status, strength, utility, confidence, importance, recurrence, and use count

#### Scenario: Relevance tooltip explains query dependency
- **WHEN** the user hovers or focuses the `?` help next to `Relevance`
- **THEN** the GUI explains that Relevance is query-dependent and combines search match strength with the memory priority baseline

### Requirement: Ambiguous score label is removed
The GUI MUST NOT label raw `importance / confidence` values as `Score`.

#### Scenario: Old score label is absent
- **WHEN** the memory table is rendered
- **THEN** the table does not show `Score` as the primary metric label
- **AND** the table does not present `importance / confidence` as a single score
