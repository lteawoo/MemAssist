## ADDED Requirements

### Requirement: memassist SHALL key embedding cache rows by profile identity

Embedding cache rows SHALL include the active profile id and a stable profile fingerprint in addition to memory id, artifact content hash, provider, model, dimension, and indexed timestamp. Cache rows SHALL remain derived state and SHALL NOT be authoritative memory content.

#### Scenario: Different profiles can cache the same memory
- **GIVEN** a memory artifact has content hash `abc`
- **AND** profiles `potion-int8` and `bge-m3-dense` both build embeddings for that memory
- **WHEN** memassist stores embedding cache rows
- **THEN** both profile cache rows SHALL be able to coexist
- **AND** retrieval with one profile SHALL NOT use the other profile's vector row

#### Scenario: Profile setting change invalidates old cache
- **GIVEN** a profile changes model, quantization, dimension, normalization, or text prefix settings
- **WHEN** memassist computes the profile fingerprint
- **THEN** the fingerprint SHALL differ from cache rows built with the previous settings
- **AND** memassist SHALL NOT use the previous fingerprint's rows for current vector ranking

### Requirement: memassist SHALL ignore stale profile cache rows

memassist SHALL reconcile embedding cache rows against authoritative Markdown artifacts, current artifact content hashes, memory lifecycle status, and profile identity before vector ranking.

#### Scenario: Content hash mismatch is ignored
- **GIVEN** an embedding cache row matches the active profile
- **AND** the row content hash differs from the current Markdown artifact content hash
- **WHEN** vector retrieval reads embedding rows
- **THEN** memassist SHALL ignore the stale row
- **AND** memassist MAY rebuild the embedding for the active profile

#### Scenario: Candidate profile cache is not injected as active context
- **GIVEN** a candidate memory has an embedding row for the active profile
- **WHEN** prompt retrieval builds active memory context
- **THEN** memassist SHALL NOT inject the candidate memory as active context
- **AND** the cache row SHALL remain derived state until lifecycle changes make the memory active

### Requirement: memassist SHALL clean up embedding cache by profile

memassist SHALL provide a way to remove derived embedding cache rows for a selected profile without deleting Markdown memory artifacts or unrelated profile caches.

#### Scenario: Profile cache cleanup preserves memories
- **GIVEN** a project has Markdown memory artifacts
- **AND** embedding cache rows exist for profile `potion-int8`
- **WHEN** the user cleans up cache for `potion-int8`
- **THEN** memassist SHALL delete derived cache rows for that profile
- **AND** memassist SHALL NOT delete Markdown memory artifacts
- **AND** memassist SHALL NOT delete cache rows for other profiles unless explicitly requested
