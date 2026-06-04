## 1. Implementation

- [x] 1.1 Update isolated judge instructions to separate durable memory from one-shot turn instructions.
- [x] 1.2 Store `memory_content` as the primary memory content while preserving `source_quote` in metadata/reason.
- [x] 1.3 Update docs to describe source evidence preservation versus retrievable durable content.

## 2. Tests

- [x] 2.1 Add fixture-backed regression tests for Korean mixed durable/transient prompts.
- [x] 2.2 Add retrieval regression checks proving transient phrases are not injected later.
- [x] 2.3 Run the full Python test suite.

## 3. Evaluation

- [x] 3.1 Run actual Codex CLI E2E in `/Users/twlee/projects/test` from init through memory ingestion.
- [x] 3.2 Confirm evaluation metric: stored/retrieved durable memory contains 0 one-shot response-format phrases for the target prompt set.
- [ ] 3.3 Use an independent verification pass to inspect implementation, tests, and E2E evidence.
