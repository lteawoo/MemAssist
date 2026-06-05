## ADDED Requirements

### Requirement: Project-local conflict resolution
The system SHALL resolve conflicts for judge-extracted memory candidates using only memories that belong to the same project.

Conflict resolution SHALL run after extraction judge output is parsed and before any new memory row is stored as active.

#### Scenario: Same project memories are considered
- **WHEN** a judge-extracted candidate is being stored for a project
- **THEN** the resolver inspects active and candidate memories with the same `project_id`

#### Scenario: Other projects are excluded
- **WHEN** a memory with matching content exists under a different `project_id`
- **THEN** the resolver does not reinforce, link, archive, or otherwise mutate that memory

#### Scenario: No global conflict behavior
- **WHEN** a candidate is resolved
- **THEN** the resolver does not use global-scope memories or cross-project memories as conflict targets

### Requirement: Read-only conflict candidate discovery
The system SHALL discover conflict candidates without mutating retrieval usage signals.

Conflict discovery MAY reuse lexical, vector, index, or memory-link data, but SHALL NOT increment `retrieval_count`, update `last_used_at`, increase `utility`, or increase `strength` merely because a memory was inspected for conflict resolution.

#### Scenario: Conflict search does not mark memory as used
- **WHEN** a memory is inspected only as a conflict candidate
- **THEN** its retrieval usage fields remain unchanged

#### Scenario: Archived semantic candidates are excluded
- **WHEN** archived memories are semantically similar but not exact content duplicates
- **THEN** the resolver does not use them for relation classification or lifecycle decisions

### Requirement: Relation judge contract
The system SHALL classify candidate-to-existing memory relationships using a dedicated relation judge contract.

The relation judge SHALL return one relation per related existing memory from the following enum:

- `duplicate`
- `complementary`
- `candidate_supersedes`
- `existing_supersedes`
- `conflicts`
- `unrelated`

The relation judge SHALL include the `existing_memory_id` and a short `reason` for each relation.

#### Scenario: Duplicate relation
- **WHEN** a new candidate and an existing project memory express the same durable meaning with different wording
- **THEN** the relation judge returns `duplicate` for that existing memory

#### Scenario: Supersession relation
- **WHEN** a new clean candidate replaces an older same-project memory
- **THEN** the relation judge returns `candidate_supersedes` for the older memory

#### Scenario: Conflict relation
- **WHEN** a new candidate contradicts an existing same-project memory and priority is unclear
- **THEN** the relation judge returns `conflicts`

### Requirement: No semantic keyword heuristics
The system SHALL NOT classify semantic duplicate, contradiction, supersession, or unrelated relationships with language-specific hardcoded keywords or token-overlap rules.

Deterministic exact content equality MAY be used as a fast path before relation judgment.

#### Scenario: Korean paraphrase duplicate
- **WHEN** a Korean candidate paraphrases an existing Korean memory without exact content equality
- **THEN** the resolver relies on relation judge output rather than token overlap to classify it as duplicate

#### Scenario: Mixed-language conflict
- **WHEN** a candidate and existing memory conflict across mixed Korean and English wording
- **THEN** the resolver relies on relation judge output rather than fixed keyword rules

### Requirement: Deterministic resolution policy
The system SHALL convert relation judge output and `source_integrity` into deterministic storage actions.

The relation judge SHALL NOT decide lifecycle status or mutate storage directly.

#### Scenario: Exact or semantic duplicate
- **WHEN** a candidate duplicates an existing same-project memory
- **THEN** the system reinforces the existing memory and does not store a new memory row

#### Scenario: Clean candidate with no relation
- **WHEN** a clean candidate has no related existing project memory
- **THEN** the system stores it as active

#### Scenario: Uncertain candidate with no relation
- **WHEN** an uncertain candidate has no related existing project memory
- **THEN** the system stores it as candidate

#### Scenario: Conflict blocks active storage
- **WHEN** a candidate conflicts with an existing same-project memory
- **THEN** the system stores the candidate as candidate and records the conflict relation

#### Scenario: Clean candidate supersedes existing project memory
- **WHEN** a clean candidate supersedes an existing same-project memory
- **THEN** the system stores the candidate as active and archives the superseded memory with `superseded_by` pointing to the new memory

#### Scenario: Uncertain candidate supersession is held
- **WHEN** an uncertain candidate would supersede an existing same-project memory
- **THEN** the system stores the candidate as candidate and does not archive the existing memory

#### Scenario: Existing memory supersedes candidate
- **WHEN** an existing same-project memory supersedes a new candidate
- **THEN** the system does not store the candidate as active

### Requirement: Multiple relation priority
The system SHALL collapse multiple relation results into one primary storage action using conservative priority.

The priority order SHALL be:

1. `conflicts`
2. `candidate_supersedes`
3. `existing_supersedes`
4. `duplicate`
5. `complementary`
6. `unrelated`

Exact content duplicate SHALL be handled before relation judgment and SHALL reinforce immediately.

#### Scenario: Conflict beats complementary
- **WHEN** one existing memory conflicts with the candidate and another is complementary
- **THEN** the system stores the candidate as candidate and records the conflict as the primary decision

#### Scenario: Supersession beats duplicate
- **WHEN** relation outputs include both `candidate_supersedes` and `duplicate`
- **THEN** the system applies the supersession policy as the primary decision

### Requirement: Relation judge failure fallback
The system SHALL handle relation judge unavailability, invalid output, or timeout conservatively.

#### Scenario: Related memories exist and relation judge fails
- **WHEN** conflict candidates exist but relation judgment fails
- **THEN** the system stores the new memory as candidate and records a `held_relation_judge_unavailable` lifecycle decision

#### Scenario: No related memories and relation judge skipped
- **WHEN** no conflict candidates exist
- **THEN** the system does not require relation judge output before storing clean candidates as active or uncertain candidates as candidate

### Requirement: Conflict audit trail
The system SHALL record conflict resolver decisions in lifecycle events and durable relation links when storage is changed or a candidate is held.

#### Scenario: Duplicate reinforcement audit
- **WHEN** a duplicate candidate reinforces an existing memory
- **THEN** a lifecycle event records the duplicate decision and the candidate evidence

#### Scenario: Conflict hold audit
- **WHEN** a candidate is held because it conflicts with an existing memory
- **THEN** a lifecycle event records the conflict decision and a memory link records the conflict relation

#### Scenario: Complementary memory audit
- **WHEN** a complementary candidate is stored
- **THEN** a memory link records the complementary relation to the existing memory
