## 1. Resolver Contract and Candidate Discovery

- [x] 1.1 Add conflict resolver data models for relation judge input, relation output, and resolution decisions
- [x] 1.2 Implement project-local conflict candidate discovery for active/candidate memories and exact archived duplicates
- [x] 1.3 Ensure conflict discovery is read-only and does not mutate retrieval usage fields
- [x] 1.4 Remove semantic relation dependence on token-overlap duplicate heuristics from the judge storage path

## 2. Relation Judge

- [x] 2.1 Add relation enum validation for duplicate, complementary, candidate_supersedes, existing_supersedes, conflicts, and unrelated
- [x] 2.2 Implement relation judge adapter contract and JSON parsing
- [x] 2.3 Add deterministic exact-content duplicate fast path before relation judgment
- [x] 2.4 Add conservative relation judge failure handling

## 3. Resolution Policy and Storage Integration

- [x] 3.1 Implement deterministic resolution policy using relation priority and source_integrity
- [x] 3.2 Integrate resolver into store_judge_result before new memory storage
- [x] 3.3 Apply duplicate reinforcement, complementary storage, conflict hold, supersession, existing-supersedes hold, and no-relation storage actions
- [x] 3.4 Persist resolver lifecycle events with candidate evidence and primary decision reason
- [x] 3.5 Persist durable relation links for conflict, complementary, supersession, and duplicate outcomes where applicable

## 4. Tests and Evaluation Coverage

- [x] 4.1 Add unit tests for project-local scoping and cross-project exclusion
- [x] 4.2 Add unit tests for read-only conflict candidate discovery
- [x] 4.3 Add tests for Korean paraphrase duplicate and mixed-language conflict using relation judge fixtures
- [x] 4.4 Add tests for clean and uncertain supersession policies
- [x] 4.5 Add tests for relation judge unavailable fallback
- [x] 4.6 Update or remove obsolete token-overlap semantic duplicate tests

## 5. Validation and QA Loop

- [x] 5.1 Run focused unit tests for memory judge and conflict resolver behavior
- [x] 5.2 Run full unit test suite
- [x] 5.3 Run relevant memassist eval commands
- [x] 5.4 Run OpenSpec validation for add-memory-conflict-resolver
- [x] 5.5 Run independent evaluator review against proposal, design, specs, and task contract
- [x] 5.6 Resolve evaluator findings and repeat verification until no blocking findings remain
