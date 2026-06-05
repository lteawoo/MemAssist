## Why

The current memory judge contract mixes durable memory extraction with lifecycle state, conflict hints, path prediction, and reminder severity. This makes it hard to explain why a memory became active, whether a candidate was source-clean, and which component owns conflict resolution.

This change narrows the judge to a memory extraction role and moves lifecycle, conflict, activation, and path association decisions into explicit system policies.

## What Changes

- Introduce a Memory Extraction Judge Contract V2 where the judge returns either a normalized `memory` object or `memory: null`.
- Remove duplicated or policy-owned judge fields:
  - **BREAKING**: remove `should_store`; use `memory: null` for no memory.
  - **BREAKING**: remove `meaning_preserved`; invalid or distorted extraction is represented by `memory: null` plus `reject_reason`.
  - **BREAKING**: remove `activation`; active/candidate/rejected is decided by system policy, not the extraction judge.
  - **BREAKING**: remove `candidate_paths`; path association is not part of judge output.
- **BREAKING**: remove `caution_level` from judge output; reminder strength is derived later from memory type and retrieval policy unless a future change proves a separate field is necessary.
- Rename `contamination_risk` to `source_integrity`.
- Remove `existing_conflicts` from judge input. Enhanced conflict resolution is deferred to a follow-up change.
- Treat file/path relevance as dynamic association or retrieval-time evidence, not as a core extracted memory attribute.
- Prohibit language-specific keyword heuristics for semantic classification. Multilingual meaning interpretation remains the judge's responsibility.
- Defer RAG-backed conflict retrieval and relation classification to a later change.
- Assume there are no existing users. Do not preserve legacy judge-output compatibility, obsolete fields, dead code, or tests that only protect removed behavior.
- Preserve provenance through `source_quote` and audit/debug explanation through `reason`.

## Capabilities

### New Capabilities

- `memory-judge-contract`: Defines the judge output contract, source integrity semantics, and separation between extraction, conflict resolution, and activation policy.

### Modified Capabilities

- None. There are no existing OpenSpec capabilities in this repository yet.

## Impact

- `src/memassist/memory_judge.py`: judge instruction, output parser, payload construction, fixture handling, and storage handoff.
- `src/memassist/models.py`, `src/memassist/storage.py`, `src/memassist/memory_artifacts.py`: remove or replace legacy fields such as `caution_level` and judge-created `paths` without backward-compatibility shims unless an internal transition is strictly required.
- `src/memassist/retrieval.py`: future reminder rendering should use memory type and retrieval policy without implying tool blocking.
- `src/memassist/lifecycle.py`: activation policy becomes explicit; enhanced conflict resolver work is out of scope for this change.
- `tests/test_memassist.py`, `tests/test_memassist_e2e.py`: update fixture JSON, parser tests, activation tests, and regression tests for one-shot instruction exclusion.
- CLI/export/import/GUI/sync should be updated to the new contract directly; obsolete compatibility paths and obsolete tests should be removed.
