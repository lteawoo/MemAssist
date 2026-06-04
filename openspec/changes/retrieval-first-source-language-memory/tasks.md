## 1. Memory Storage

- [x] 1.1 Preserve source-language user prompt text as the primary stored memory content for isolated judge memories.
- [x] 1.2 Keep judge/interpreter normalized subjects and path hints as retrieval metadata only.
- [x] 1.3 Prevent stored memories from receiving `warn_policy` or `block_policy` status.

## 2. Retrieval And Prompt Context

- [x] 2.1 Remove `Policy reminders` output from RAG context rendering.
- [x] 2.2 Include directive memories in relevant memory context without policy framing.
- [x] 2.3 Improve local retrieval metadata so Korean `리프레시/리프레쉬 토큰` prompts retrieve refresh-token memories.
- [x] 2.4 Ensure UserPromptSubmit remains the RAG injection point through `additionalContext`.

## 3. Remove Memory-Derived Enforcement

- [x] 3.1 Change `memory activate` so it only activates retrieval eligibility and never mutates `policy.yaml`.
- [x] 3.2 Remove automatic `sensitive_paths` and `protected_paths` writes from memory lifecycle/directive processing.
- [x] 3.3 Remove session approval grants and memory-derived approval gate handling from UserPromptSubmit/PreToolUse.
- [x] 3.4 Keep PreToolUse deterministic checks only for explicit manual `policy.yaml` entries.

## 4. Tests

- [x] 4.1 Update tests that expected memory activation to create policy paths.
- [x] 4.2 Add tests for Korean source-language memory storage.
- [x] 4.3 Add tests for Korean/English/typo refresh-token retrieval.
- [x] 4.4 Add hook tests proving relevant memory is injected as context and not as `Policy reminders`.
- [x] 4.5 Add pre-tool tests proving active memories do not warn/block without manual policy.
- [x] 4.6 Update E2E tests from policy behavior assertions to retrieval-first assertions.

## 5. Documentation And Validation

- [x] 5.1 Update README to describe retrieval-first memory behavior and manual-only policy checks.
- [x] 5.2 Run focused storage/retrieval/policy tests until passing.
- [x] 5.3 Run full unittest suite until passing.
- [x] 5.4 Run `compileall` and strict OpenSpec validation.
- [x] 5.5 Confirm OpenSpec apply progress reports 100% complete.
