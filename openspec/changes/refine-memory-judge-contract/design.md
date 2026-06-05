## Context

memassist currently captures turn-end user sources, sends a constrained payload to an isolated judge, parses a compact JSON object, and stores a memory through `store_judge_result`.

The existing judge output includes `should_store`, `memory_content`, `source_quote`, `memory_type`, `caution_level`, `activation`, `candidate_paths`, `meaning_preserved`, `contamination_risk`, and `reason`. Several of these fields are policy decisions rather than extraction results. In particular, `activation` decides lifecycle state, `candidate_paths` predicts future file relevance, and `existing_conflicts` in the judge payload mixes source extraction with system-state conflict handling.

The desired architecture keeps the judge useful for language-heavy extraction while making lifecycle decisions deterministic and auditable.

## Goals / Non-Goals

**Goals:**

- Make the judge contract smaller and easier to explain.
- Keep the judge responsible for durable memory extraction from user source text.
- Move active/candidate/rejected decisions to explicit system policy.
- Remove existing-memory conflict hints from the judge path and defer enhanced conflict resolution to a later change.
- Remove path prediction from judge output.
- Remove caution/reminder severity from the judge contract unless a future change proves it is needed as an independent signal.
- Preserve source provenance and auditability.
- Remove dead code, obsolete compatibility paths, unnecessary comments, and tests that only protect removed behavior.

**Non-Goals:**

- Implement a full dynamic path association system in this change.
- Implement enhanced conflict retrieval, relation classification, or a complete conflict resolver decision table in this change.
- Add a replacement `reminder_severity` field in this change.
- Add tool blocking behavior.
- Preserve backward compatibility for old judge outputs, old artifacts, or old users.

## Decisions

### Decision 1: Judge outputs a nullable memory object

The judge will return `memory: null` when no durable memory should be extracted. This replaces `should_store=false` and avoids representing the same decision twice.

Alternative considered: keep `should_store`. This was rejected because `should_store` and `activation=rejected` duplicate the same no-store path and make tests harder to reason about.

### Decision 2: Judge does not decide activation

The judge may describe the extracted memory and source integrity, but final status is decided by system policy.

System policy can then apply rules such as:

- `memory == null` means no storage.
- `source_integrity == contaminated` means reject/no storage.
- `source_integrity == uncertain` means candidate-only.
- `source_integrity == clean` can become active only if conflict policy allows it.

Alternative considered: keep `activation` as judge recommendation. This was rejected for now because the project goal is to avoid ambiguous ownership of lifecycle state.

### Decision 3: `contamination_risk` becomes `source_integrity`

The important signal is not whether the memory content is risky. The signal is whether the extracted candidate came cleanly from user source text without assistant response, retrieved memory context, system/developer prompt, agent reasoning, or one-shot formatting contamination.

Values:

- `clean`
- `uncertain`
- `contaminated`

### Decision 4: `caution_level` is removed from the judge contract

The current `block` value is misleading because memassist does not block tools in `PreToolUse`. A separate reminder severity field could be useful later, but it is not essential to the extraction judge contract.

For this change, reminder strength should be derived from existing memory type and retrieval policy. For example, `directive`, `rule`, and `lesson` memories can be routed toward verifier/reminder sections without requiring another fuzzy judge output.

Alternative considered: rename `caution_level` to `reminder_severity`. This was rejected for now because it preserves a subjective extra field whose immediate value overlaps with `memory.type`.

### Decision 5: Judge output does not include paths

Paths are unstable and may refer to current source files, future changed files, or no files at all. They are not part of the memory itself.

Path relevance should be derived later through retrieval-time evidence, trace signals, links, or a future dynamic association layer.

### Decision 6: Enhanced conflict resolution is deferred

`existing_conflicts` will be removed from the judge payload. The judge should not infer a new memory from existing memories.

The enhanced resolver for duplicate, paraphrase, contradiction, supersession, reinforcement, keep-both, and candidate downgrade decisions will be designed in a later change. This keeps the current change focused on judge contract cleanup and avoids creating a half-defined conflict system.

### Decision 7: Language meaning is not classified with hardcoded keywords

The system policy must not use language-specific keyword rules to decide whether a user source is a preference, rule, durable memory, one-shot instruction, or contradiction.

Language-dependent interpretation belongs to the extraction judge or a dedicated relation judge. System policy consumes structured outputs and repository state, then performs explicit state transitions.

This avoids brittle behavior across Korean, English, mixed-language prompts, and future languages.

### Decision 8: Future conflict candidate discovery should reuse memory retrieval infrastructure

When conflict resolution is designed, it should not introduce a completely separate search system. It should reuse the existing memory retrieval stack to find related existing memories for a new extracted candidate.

The retrieval mode must be distinct from prompt context injection:

- Agent context retrieval prioritizes a small, precise memory pack for the current request.
- Conflict retrieval prioritizes recall over precision and finds existing memories related to the candidate memory content.

Conflict retrieval may use lexical search, vector search, hybrid fusion, and memory links, but its output should only be an input to relation classification and conflict policy. It must not be injected into the agent prompt.

## Risks / Trade-offs

- Existing tests and fixtures depend on the old JSON shape. Mitigation: update or remove those tests directly; do not keep compatibility solely for old fixture shapes.
- Existing tests, fixtures, and storage helpers may still encode removed fields. Mitigation: replace the schema/model contract directly where practical and remove obsolete code in the same change.
- Removing judge paths may reduce path-based retrieval in the short term. Mitigation: rely on content/type initially and plan dynamic association separately.
- Removing judge activation may initially make more memories candidate-only if activation policy is conservative. Mitigation: define explicit active eligibility rules and test them.
- Reusing retrieval for conflict discovery can overfit to context-injection ranking if the modes are not separated. Mitigation: introduce a dedicated conflict retrieval mode with separate filters, limits, diagnostics, and tests.

## Migration Plan

1. Update judge instruction to request V2 output.
2. Replace V1 parser and fixtures with V2 parser and fixtures.
3. Remove `existing_conflicts` from judge payload.
4. Remove judge-created path handling from the extraction path.
5. Remove `caution_level` from the judge contract and any now-obsolete related behavior touched by this change.
6. Move active/candidate/rejected decisions into an activation policy function.
7. Remove dead code and tests that exist only for removed V1 behavior.
8. Update tests, eval fixtures, docs, and GUI labels.

## Open Questions

- Decide whether path relevance should be represented by a new association table, computed only at retrieval time, or both.
- In a follow-up change, define the conflict resolver decision table and relation classifier output values, such as duplicate, contradiction, supersedes, complements, and unrelated.
